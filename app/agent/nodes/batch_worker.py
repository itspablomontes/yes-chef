"""Single-item worker node.

Processes one durable menu item in a focused ReAct loop,
looks up ingredients for that item, and returns completed item data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import RateLimitError
from pydantic import BaseModel, Field

from app.agent.prompts.few_shot_examples import FEW_SHOT_EXAMPLE
from app.agent.state import EstimationState, ItemEstimation
from app.agent.tools import get_all_tools
from app.agent.validation.validators import validate_item_estimation
from app.application.stream_events import emit_progress_event
from app.application.work_units import ITEM_KEY_FIELD, build_menu_work_units, completed_item_keys
from app.infrastructure.settings import get_settings

logger = logging.getLogger(__name__)


# Structured output model for saving a single item result
class SaveItemResult(BaseModel):
    """Save the estimation result for the current menu item."""

    estimation: ItemEstimation = Field(
        description="The complete estimation for the current menu item."
    )


def build_item_system_prompt(
    item_to_process: dict[str, Any],
    knowledge: dict[str, str],
) -> str:
    """Build the system prompt for a single work unit."""

    knowledge_hints = ""
    if knowledge:
        lines = ["\n## Known Catalog Findings (from previous items):"]
        for ingredient, status in knowledge.items():
            if status == "not_available":
                lines.append(f"- {ingredient}: NOT AVAILABLE in Sysco catalog. Mark as not_available.")
            elif status == "estimated":
                lines.append(f"- {ingredient}: Not in catalog, use estimated pricing.")
            elif status.startswith("found:"):
                sysco_id = status.split(":", 1)[1]
                lines.append(
                    f"- {ingredient}: Already matched in Sysco catalog as item #{sysco_id}. "
                    "Reuse that match if it fits this dish."
                )
        knowledge_hints = "\n".join(lines)

    return f"""You are an elite culinary cost estimator for Elegant Foods catering.

Your job is to break down event menu items into component ingredients, look them up in the
Sysco supplier catalog, and produce structured cost estimates.

## Process
1. I will give you EXACTLY ONE menu item to process.
2. Identify the ingredients needed for that single dish only.
3. Use `search_catalog` only for ingredients that are not already covered by the known findings below.
4. For items you find, use `get_item_price` to calculate the exact per-serving unit cost.
5. Finally, call `SaveItemResult` with the complete estimation for this single menu item.
{knowledge_hints}

## Rules
- You must use tools to search and price. NEVER guess a unit cost without pulling it from a tool.
- Work on ONLY the current menu item. Do not plan or estimate any other item.
- `search_catalog` requires: `query`
- `get_item_price` requires: `sysco_item_number` and `quantity_needed`
- NEVER call any tool with empty arguments.
- The Sysco catalog uses reverse-comma notation (e.g., "bacon applewood" -> "BACON, SMOKED, APPLEWOOD").
- If a specialty item (like wagyu, truffle, saffron) isn't in Sysco, DO NOT guess a price. 
  Mark `unit_cost` as null and `source` as "not_available".
- If a basic item (like salt, pepper, oil) isn't in Sysco, mark it "estimated" and assign a
  reasonable small unit_cost (e.g., 0.05).
- Always ensure `ingredient_cost_per_unit` equals the EXACT mathematical sum of all
  ingredient `unit_cost`s for that dish.

## Current Menu Item
- Name: {item_to_process.get('name', 'Unknown')}
- Category: {item_to_process.get('category', 'Unknown')}
- Description: {item_to_process.get('description', '')}
- Dietary: {item_to_process.get('dietary_notes', 'None')}
- Service Style: {item_to_process.get('service_style', 'None')}
"""


class ItemWorkerNode:
    """Processes one durable menu item at a time."""

    def __init__(self, llm: ChatOpenAI, repair_llm: ChatOpenAI | None = None) -> None:
        self._llm = llm
        self._repair_llm = repair_llm
        self._tools = get_all_tools()
        self._tools_by_name = {t.name: t for t in self._tools}

        # Process one durable work unit at a time for faster resumability.
        self._llm_with_tools = self._llm.bind_tools(self._tools + [SaveItemResult])
        self._repair_llm_with_tools = (
            self._repair_llm.bind_tools(self._tools + [SaveItemResult])
            if self._repair_llm is not None
            else self._llm_with_tools
        )

    async def _invoke_tool_with_progress(
        self,
        *,
        state: EstimationState,
        batch_index: int,
        tool_name: str,
        tool_fn: Any,
        tool_args: dict[str, Any],
        telemetry: dict[str, Any],
    ) -> Any:
        """Run a tool with heartbeats so the SSE stream never looks frozen."""
        settings = get_settings()
        heartbeat_seconds = max(1, settings.tool_heartbeat_seconds)
        timeout_seconds = max(heartbeat_seconds, settings.tool_timeout_seconds)

        await emit_progress_event(
            "tool_started",
            estimation_id=state.estimation_id,
            batch_index=batch_index,
            tool=tool_name,
        )

        tool_task = asyncio.create_task(tool_fn.ainvoke(tool_args))
        elapsed_seconds = 0
        telemetry["tool_calls"] += 1

        try:
            while True:
                try:
                    tool_result = await asyncio.wait_for(
                        asyncio.shield(tool_task),
                        timeout=heartbeat_seconds,
                    )
                    await emit_progress_event(
                        "tool_finished",
                        estimation_id=state.estimation_id,
                        batch_index=batch_index,
                        tool=tool_name,
                        status="ok",
                    )
                    return tool_result
                except asyncio.TimeoutError:
                    elapsed_seconds += heartbeat_seconds
                    telemetry["tool_wait_seconds"] += heartbeat_seconds
                    if elapsed_seconds >= timeout_seconds:
                        tool_task.cancel()
                        raise TimeoutError(
                            f"{tool_name} timed out after {timeout_seconds}s"
                        )

                    await emit_progress_event(
                        "tool_waiting",
                        estimation_id=state.estimation_id,
                        batch_index=batch_index,
                        tool=tool_name,
                        elapsed_seconds=elapsed_seconds,
                        message=(
                            f"{tool_name} is still running after "
                            f"{elapsed_seconds}s"
                        ),
                    )
        finally:
            if not tool_task.done():
                tool_task.cancel()

    async def _invoke_llm_with_progress(
        self,
        *,
        state: EstimationState,
        batch_index: int,
        messages: list[Any],
        use_repair_model: bool,
        telemetry: dict[str, Any],
    ) -> Any:
        """Run the LLM call with periodic wait events for SSE clients."""
        settings = get_settings()
        heartbeat_seconds = max(1, settings.llm_heartbeat_seconds)
        timeout_seconds = max(heartbeat_seconds, settings.llm_timeout_seconds)

        llm_task = asyncio.create_task(
            self._safe_invoke(
                messages,
                use_repair_model=use_repair_model,
                telemetry=telemetry,
            )
        )
        elapsed_seconds = 0
        telemetry["llm_calls"] += 1

        try:
            while True:
                await emit_progress_event(
                    "llm_waiting",
                    estimation_id=state.estimation_id,
                    batch_index=batch_index,
                    elapsed_seconds=elapsed_seconds,
                    message=(
                        "Waiting for LLM response"
                        if elapsed_seconds == 0
                        else f"Still waiting for LLM response ({elapsed_seconds}s)"
                    ),
                )

                try:
                    return await asyncio.wait_for(
                        asyncio.shield(llm_task),
                        timeout=heartbeat_seconds,
                    )
                except asyncio.TimeoutError:
                    elapsed_seconds += heartbeat_seconds
                    telemetry["llm_wait_seconds"] += heartbeat_seconds
                    if elapsed_seconds >= timeout_seconds:
                        llm_task.cancel()
                        raise TimeoutError(
                            f"LLM call timed out after {timeout_seconds}s"
                        )
        finally:
            if not llm_task.done():
                llm_task.cancel()

    async def _safe_invoke(
        self,
        messages: list[Any],
        *,
        use_repair_model: bool,
        telemetry: dict[str, Any],
    ) -> Any:
        settings = get_settings()
        attempts = max(1, settings.llm_rate_limit_attempts)
        backoff_seconds = 2
        llm = (
            self._repair_llm_with_tools
            if use_repair_model and self._repair_llm is not None
            else self._llm_with_tools
        )
        for attempt in range(attempts):
            try:
                return await llm.ainvoke(messages)
            except RateLimitError:
                telemetry["rate_limit_retries"] += 1
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(min(60, backoff_seconds))
                backoff_seconds *= 2
        return await llm.ainvoke(messages)

    @staticmethod
    def _extract_token_usage(response: Any) -> dict[str, int]:
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        usage_metadata = getattr(response, "usage_metadata", None)
        if isinstance(usage_metadata, dict):
            prompt_tokens = int(usage_metadata.get("input_tokens") or 0)
            completion_tokens = int(usage_metadata.get("output_tokens") or 0)
            total_tokens = int(usage_metadata.get("total_tokens") or 0)

        response_metadata = getattr(response, "response_metadata", None)
        if isinstance(response_metadata, dict):
            token_usage = response_metadata.get("token_usage")
            if isinstance(token_usage, dict):
                prompt_tokens = int(token_usage.get("prompt_tokens") or prompt_tokens)
                completion_tokens = int(
                    token_usage.get("completion_tokens") or completion_tokens
                )
                total_tokens = int(token_usage.get("total_tokens") or total_tokens)

        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _serialize_tool_result(tool_result: Any, max_matches: int) -> str:
        """Serialize tool output as JSON when possible."""
        if isinstance(tool_result, dict):
            if isinstance(tool_result.get("matches"), list):
                compact = {
                    "query": tool_result.get("query", ""),
                    "matches": [
                        {
                            "item_number": match.get("item_number"),
                            "description": match.get("description"),
                            "unit_of_measure": match.get("unit_of_measure"),
                            "cost_per_case": match.get("cost_per_case"),
                            "score": match.get("score"),
                        }
                        for match in tool_result["matches"][:max_matches]
                        if isinstance(match, dict)
                    ],
                }
                return json.dumps(compact, sort_keys=True)
            if "unit_cost" in tool_result and "sysco_item_number" in tool_result:
                compact = {
                    "sysco_item_number": tool_result.get("sysco_item_number"),
                    "quantity_needed": tool_result.get("quantity_needed"),
                    "unit_cost": tool_result.get("unit_cost"),
                }
                return json.dumps(compact, sort_keys=True)
            return json.dumps(tool_result, sort_keys=True)
        if isinstance(tool_result, list):
            return json.dumps(tool_result[:max_matches], sort_keys=True)
        return str(tool_result)

    @staticmethod
    def _normalize_tool_args(tool_name: str, tool_args: Any) -> dict[str, Any]:
        """Handle provider-specific tool arg wrappers like `parameters`."""
        if tool_name == "get_item_price" and isinstance(tool_args, dict):
            params_value = tool_args.get("parameters")
            if isinstance(params_value, str) and "quantity_needed" not in tool_args:
                tool_args = dict(tool_args)
                tool_args["quantity_needed"] = params_value

        if tool_name == "search_catalog" and isinstance(tool_args, dict):
            params_value = tool_args.get("parameters")
            if isinstance(params_value, str) and "query" not in tool_args:
                tool_args = {"query": params_value}

        if not isinstance(tool_args, dict):
            return {}

        for nested_key in ("parameters", "arguments", "input"):
            nested = tool_args.get(nested_key)
            if isinstance(nested, dict):
                tool_args = nested
                break

        if tool_name == "get_item_price" and "quantity_needed" not in tool_args:
            quantity = tool_args.get("quantity")
            if quantity is not None:
                tool_args = dict(tool_args)
                tool_args["quantity_needed"] = quantity

        return tool_args

    def _get_next_work_units(
        self,
        state: EstimationState,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Identify unfinished menu items in stable order up to limit."""
        work_units = build_menu_work_units(state.menu_spec)
        completed_keys = completed_item_keys(state.menu_spec, state.completed_items)
        selected: list[dict[str, Any]] = []

        for unit in work_units:
            item_key = unit.get(ITEM_KEY_FIELD)
            if isinstance(item_key, str) and item_key not in completed_keys:
                selected.append(unit)
                if len(selected) >= limit:
                    break

        return selected

    def _build_terminal_update(
        self,
        *,
        completed_items: list[dict[str, Any]],
        knowledge_store: dict[str, str],
        memo_store: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Return the completed item update for the current work unit(s)."""
        return {
            "completed_items": completed_items,
            "knowledge_store": knowledge_store,
            "memo_store": memo_store,
        }

    async def _process_single_item(
        self,
        *,
        state: EstimationState,
        item_to_process: dict[str, Any],
        batch_index: int,
        completed_items_count: int,
        search_cache: dict[str, dict[str, Any]],
        price_cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        item_name = str(item_to_process.get("name", "Unknown"))
        item_key = str(item_to_process.get(ITEM_KEY_FIELD, ""))
        settings = get_settings()
        max_retries = max(0, settings.item_max_retries)
        max_iterations = max(1, settings.item_max_iterations)
        max_matches = max(1, settings.tool_result_max_matches)

        started_at = time.monotonic()
        telemetry: dict[str, Any] = {
            "item_key": item_key,
            "item_name": item_name,
            "attempts": 0,
            "llm_calls": 0,
            "llm_wait_seconds": 0,
            "tool_calls": 0,
            "tool_wait_seconds": 0,
            "tool_errors": 0,
            "validation_retries": 0,
            "rate_limit_retries": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        await emit_progress_event(
            "item_started",
            estimation_id=state.estimation_id,
            batch_index=batch_index,
            item_name=item_name,
            item_key=item_key,
            completed_items=completed_items_count,
        )

        logger.info(
            "Processing item %s (%s)",
            item_name,
            item_key,
        )

        system_prompt = build_item_system_prompt(
            item_to_process=item_to_process,
            knowledge=state.knowledge_store,
        )

        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Please process this item now: {item_name}. "
                    "Search the catalog only when needed, get prices, and finally "
                    "call `SaveItemResult`."
                )
            ),
        ]
        if settings.include_few_shot_per_item:
            messages.insert(1, HumanMessage(content=FEW_SHOT_EXAMPLE))

        result_estimation: dict[str, Any] | None = None
        stale_iterations = 0

        for attempt in range(max_retries + 1):
            telemetry["attempts"] = attempt + 1
            for _iteration in range(max_iterations):
                try:
                    response = await self._invoke_llm_with_progress(
                        state=state,
                        batch_index=batch_index,
                        messages=messages,
                        use_repair_model=attempt > 0,
                        telemetry=telemetry,
                    )
                except RateLimitError as e:
                    logger.error("Rate limit exhausted for item workflow: %s", e)
                    break
                except Exception as e:
                    # Catch Pydantic ValidationErrors from structured output coercion
                    # If LangChain fails to parse the tool arguments into the Pydantic schema
                    error_str = str(e)
                    if "ValidationError" in error_str or "validation error" in error_str.lower():
                        logger.warning(
                            "Pydantic Structured Output Validation Failed. "
                            "Instructing LLM to correct schema: %s",
                            e,
                        )
                        messages.append(
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "SaveItemResult",
                                        "args": {},
                                        "id": "call_failed",
                                    }
                                ],
                            )
                        )
                        from langchain_core.messages import ToolMessage
                        messages.append(
                            ToolMessage(
                                content=(
                                    "Your previous call failed schema validation:\n"
                                    f"{error_str}\n\n"
                                    "Please strictly follow the required schema and "
                                    "try calling SaveItemResult again."
                                ),
                                tool_call_id="call_failed",
                            )
                        )
                        continue
                    else:
                        raise e

                token_usage = self._extract_token_usage(response)
                telemetry["prompt_tokens"] += token_usage["prompt_tokens"]
                telemetry["completion_tokens"] += token_usage["completion_tokens"]
                telemetry["total_tokens"] += token_usage["total_tokens"]

                if telemetry["total_tokens"] >= settings.token_budget_per_item:
                    await emit_progress_event(
                        "token_budget_warning",
                        estimation_id=state.estimation_id,
                        batch_index=batch_index,
                        item_name=item_name,
                        item_key=item_key,
                        total_tokens=telemetry["total_tokens"],
                        token_budget=settings.token_budget_per_item,
                    )

                messages.append(response)

                if not isinstance(response, AIMessage) or not response.tool_calls:
                    stale_iterations += 1
                    if stale_iterations >= 2:
                        break
                    break

                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = self._normalize_tool_args(tool_name, tool_call["args"])

                    if tool_name == "SaveItemResult":
                        estimation = tool_args.get("estimation")
                        if isinstance(estimation, dict):
                            result_estimation = estimation
                        elif hasattr(estimation, "model_dump"):
                            result_estimation = estimation.model_dump()

                        from langchain_core.messages import ToolMessage
                        messages.append(
                            ToolMessage(
                                content="Item result saved for validation.",
                                tool_call_id=tool_call["id"]
                            )
                        )
                        continue

                    missing_args: list[str] = []
                    if tool_name == "search_catalog" and not tool_args.get("query"):
                        missing_args = ["query"]
                    elif tool_name == "get_item_price":
                        if not tool_args.get("sysco_item_number"):
                            missing_args.append("sysco_item_number")
                        if not tool_args.get("quantity_needed"):
                            missing_args.append("quantity_needed")

                    if missing_args:
                        from langchain_core.messages import ToolMessage
                        messages.append(
                            ToolMessage(
                                content=(
                                    f"Error: {tool_name} is missing required arguments: "
                                    f"{', '.join(missing_args)}. "
                                    "Retry the same tool call with the required fields."
                                ),
                                tool_call_id=tool_call["id"],
                            )
                        )
                        continue

                    tool_fn = self._tools_by_name.get(tool_name)
                    if tool_fn is None:
                        from langchain_core.messages import ToolMessage
                        messages.append(
                            ToolMessage(
                                content=f"Error: Unknown tool '{tool_name}'", 
                                tool_call_id=tool_call["id"]
                            )
                        )
                        continue

                    try:
                        if tool_name == "search_catalog":
                            query_key = str(tool_args.get("query", "")).strip().lower()
                            if query_key and query_key in search_cache:
                                await emit_progress_event(
                                    "tool_started",
                                    estimation_id=state.estimation_id,
                                    batch_index=batch_index,
                                    tool=tool_name,
                                    cached=True,
                                )
                                tool_result = search_cache[query_key]
                                await emit_progress_event(
                                    "tool_finished",
                                    estimation_id=state.estimation_id,
                                    batch_index=batch_index,
                                    tool=tool_name,
                                    status="ok",
                                    cached=True,
                                )
                            else:
                                tool_result = await self._invoke_tool_with_progress(
                                    state=state,
                                    batch_index=batch_index,
                                    tool_name=tool_name,
                                    tool_fn=tool_fn,
                                    tool_args=tool_args,
                                    telemetry=telemetry,
                                )
                                if query_key and isinstance(tool_result, dict):
                                    search_cache[query_key] = tool_result
                        elif tool_name == "get_item_price":
                            price_key = (
                                str(tool_args.get("sysco_item_number", "")),
                                str(tool_args.get("quantity_needed", "")),
                            )
                            if all(price_key) and price_key in price_cache:
                                await emit_progress_event(
                                    "tool_started",
                                    estimation_id=state.estimation_id,
                                    batch_index=batch_index,
                                    tool=tool_name,
                                    cached=True,
                                )
                                tool_result = price_cache[price_key]
                                await emit_progress_event(
                                    "tool_finished",
                                    estimation_id=state.estimation_id,
                                    batch_index=batch_index,
                                    tool=tool_name,
                                    status="ok",
                                    cached=True,
                                )
                            else:
                                tool_result = await self._invoke_tool_with_progress(
                                    state=state,
                                    batch_index=batch_index,
                                    tool_name=tool_name,
                                    tool_fn=tool_fn,
                                    tool_args=tool_args,
                                    telemetry=telemetry,
                                )
                                if all(price_key) and isinstance(tool_result, dict):
                                    price_cache[price_key] = tool_result
                        else:
                            tool_result = await self._invoke_tool_with_progress(
                                state=state,
                                batch_index=batch_index,
                                tool_name=tool_name,
                                tool_fn=tool_fn,
                                tool_args=tool_args,
                                telemetry=telemetry,
                            )
                        from langchain_core.messages import ToolMessage
                        messages.append(
                            ToolMessage(
                                content=self._serialize_tool_result(
                                    tool_result,
                                    max_matches=max_matches,
                                ),
                                tool_call_id=tool_call["id"],
                            )
                        )
                        stale_iterations = 0
                    except Exception as e:
                        telemetry["tool_errors"] += 1
                        await emit_progress_event(
                            "tool_finished",
                            estimation_id=state.estimation_id,
                            batch_index=batch_index,
                            tool=tool_name,
                            status="error",
                            message=str(e),
                        )
                        from langchain_core.messages import ToolMessage
                        logger.warning("Tool %s failed: %s", tool_name, e)
                        messages.append(
                            ToolMessage(
                                content=f"Error executing {tool_name}: {e!s}", 
                                tool_call_id=tool_call["id"]
                            )
                        )
                        
                if result_estimation is not None:
                    break

            if result_estimation is not None:
                all_passed = True
                validation_errors = []

                if result_estimation.get("item_name") != item_name:
                    validation_errors.append(
                        f"Expected item_name '{item_name}', got "
                        f"'{result_estimation.get('item_name', '')}'."
                    )
                    all_passed = False

                if result_estimation.get("category") != item_to_process.get("category"):
                    validation_errors.append(
                        f"Expected category '{item_to_process.get('category', '')}', got "
                        f"'{result_estimation.get('category', '')}'."
                    )
                    all_passed = False

                errors = validate_item_estimation(
                    item_name=str(result_estimation.get("item_name", "")),
                    category=str(result_estimation.get("category", "")),
                    ingredients=result_estimation.get("ingredients", []),
                    ingredient_cost_per_unit=float(
                        result_estimation.get("ingredient_cost_per_unit") or 0
                    ),
                )
                if errors:
                    all_passed = False
                    validation_errors.append(
                        f"Item '{result_estimation.get('item_name')}' errors: "
                        + "; ".join(errors)
                    )

                if all_passed:
                    logger.info("Item '%s' passed validation", item_name)
                    break
                
                if attempt < max_retries:
                    telemetry["validation_retries"] += 1
                    error_msg = "\n".join(f"- {e}" for e in validation_errors)
                    logger.warning("Item result failed validation (attempt %d): %s", attempt + 1, error_msg)
                    await emit_progress_event(
                        "validation_retry",
                        estimation_id=state.estimation_id,
                        batch_index=batch_index,
                        attempt=attempt + 1,
                        message=error_msg,
                    )
                    messages.append(
                        HumanMessage(
                            content=f"Your previous result had validation errors:\n{error_msg}\n\n"
                            "Please reprocess the current item and call SaveItemResult "
                            "again with corrections."
                        )
                    )
                    result_estimation = None
                else:
                    logger.error("Item '%s' failed after %d retries", item_name, max_retries)
            else:
                if attempt < max_retries:
                    messages.append(
                        HumanMessage(
                            content="You did not call SaveItemResult. "
                            "Please output the SaveItemResult tool call "
                            "with the final estimation for the current item."
                        )
                    )
                else:
                    logger.error("Item '%s' never called SaveItemResult", item_name)

        duration_seconds = round(time.monotonic() - started_at, 2)
        telemetry["duration_seconds"] = duration_seconds
        await emit_progress_event(
            "item_telemetry",
            estimation_id=state.estimation_id,
            batch_index=batch_index,
            item_name=item_name,
            item_key=item_key,
            telemetry=telemetry,
        )

        knowledge_updates: dict[str, str] = {}
        if result_estimation is not None:
            result_estimation[ITEM_KEY_FIELD] = item_key
            result_estimation["telemetry"] = telemetry
            for ingredient in result_estimation.get("ingredients", []):
                source = ingredient.get("source")
                name = ingredient.get("name")
                sysco_item_number = ingredient.get("sysco_item_number")
                normalized_name = str(name).lower().strip() if name else ""
                if normalized_name and source == "not_available":
                    knowledge_updates[normalized_name] = "not_available"
                elif normalized_name and source == "estimated":
                    knowledge_updates[normalized_name] = "estimated"
                elif normalized_name and source == "sysco_catalog" and sysco_item_number:
                    knowledge_updates[normalized_name] = f"found:{sysco_item_number}"
            return result_estimation | {"_knowledge_updates": knowledge_updates}

        fallback_result = {
            "item_name": item_name,
            "category": item_to_process.get("category"),
            "ingredients": [],
            "ingredient_cost_per_unit": 0.0,
            "status": "failed",
            ITEM_KEY_FIELD: item_key,
            "telemetry": telemetry,
            "_knowledge_updates": knowledge_updates,
        }
        return fallback_result

    @staticmethod
    def _item_sort_key(item: dict[str, Any]) -> tuple[str, int]:
        item_key = str(item.get(ITEM_KEY_FIELD, "zzz:999999"))
        if ":" not in item_key:
            return item_key, 0
        prefix, index = item_key.split(":", 1)
        try:
            return prefix, int(index)
        except ValueError:
            return prefix, 0

    async def __call__(self, state: EstimationState) -> dict[str, Any]:
        """Execute the ReAct loop for up to N unfinished items."""
        settings = get_settings()
        worker_concurrency = max(1, settings.worker_concurrency)
        work_units = self._get_next_work_units(state, limit=worker_concurrency)
        if not work_units:
            return {}

        completed_count = len(state.completed_items)
        memo_store = {
            "search_cache": dict(state.memo_store.get("search_cache", {})),
            "price_cache": dict(state.memo_store.get("price_cache", {})),
        }

        tasks = [
            self._process_single_item(
                state=state,
                item_to_process=work_unit,
                batch_index=completed_count + 1 + offset,
                completed_items_count=completed_count,
                search_cache=memo_store["search_cache"],
                price_cache=memo_store["price_cache"],
            )
            for offset, work_unit in enumerate(work_units)
        ]
        processed_items = await asyncio.gather(*tasks)

        new_knowledge = dict(state.knowledge_store)
        final_items: list[dict[str, Any]] = []
        for item in processed_items:
            knowledge_updates = item.pop("_knowledge_updates", {})
            if isinstance(knowledge_updates, dict):
                for key, value in knowledge_updates.items():
                    if isinstance(key, str) and isinstance(value, str):
                        new_knowledge[key] = value
            final_items.append(item)

        if len(new_knowledge) > 30:
            new_knowledge = dict(list(new_knowledge.items())[-30:])

        return self._build_terminal_update(
            completed_items=sorted(final_items, key=self._item_sort_key),
            knowledge_store=new_knowledge,
            memo_store=memo_store,
        )


BatchWorkerNode = ItemWorkerNode
