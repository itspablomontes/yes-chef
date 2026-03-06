"""Single-item worker node.

Processes one durable menu item in a focused ReAct loop,
looks up ingredients for that item, and returns completed item data.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import RateLimitError
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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

## The Process
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

    MAX_RETRIES = 2
    MAX_ITERATIONS = 20
    
    def __init__(self, llm: ChatOpenAI) -> None:
        self._llm = llm
        self._tools = get_all_tools()
        self._tools_by_name = {t.name: t for t in self._tools}

        # Process one durable work unit at a time for faster resumability.
        self._llm_with_tools = self._llm.bind_tools(self._tools + [SaveItemResult])

    async def _invoke_tool_with_progress(
        self,
        *,
        state: EstimationState,
        batch_index: int,
        tool_name: str,
        tool_fn: Any,
        tool_args: dict[str, Any],
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
    ) -> Any:
        """Run the LLM call with periodic wait events for SSE clients."""
        settings = get_settings()
        heartbeat_seconds = max(1, settings.llm_heartbeat_seconds)
        timeout_seconds = max(heartbeat_seconds, settings.llm_timeout_seconds)

        llm_task = asyncio.create_task(self._safe_invoke(messages))
        elapsed_seconds = 0

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
                    if elapsed_seconds >= timeout_seconds:
                        llm_task.cancel()
                        raise TimeoutError(
                            f"LLM call timed out after {timeout_seconds}s"
                        )
        finally:
            if not llm_task.done():
                llm_task.cancel()

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(10),
        reraise=True,
    )
    async def _safe_invoke(self, messages: list[Any]) -> Any:
        return await self._llm_with_tools.ainvoke(messages)

    @staticmethod
    def _serialize_tool_result(tool_result: Any) -> str:
        """Serialize tool output as JSON when possible."""
        if isinstance(tool_result, (dict, list)):
            return json.dumps(tool_result, sort_keys=True)
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

    def _get_next_work_unit(self, state: EstimationState) -> dict[str, Any] | None:
        """Identify the next unfinished menu item in stable menu order."""
        work_units = build_menu_work_units(state.menu_spec)
        completed_keys = completed_item_keys(state.menu_spec, state.completed_items)

        for unit in work_units:
            item_key = unit.get(ITEM_KEY_FIELD)
            if isinstance(item_key, str) and item_key not in completed_keys:
                return unit

        return None

    def _build_terminal_update(
        self,
        *,
        state: EstimationState,
        completed_items: list[dict[str, Any]],
        knowledge_store: dict[str, str],
    ) -> dict[str, Any]:
        """Return the completed item update for the current work unit."""
        return {
            "completed_items": completed_items,
            "knowledge_store": knowledge_store,
        }

    async def __call__(self, state: EstimationState) -> dict[str, Any]:
        """Execute the ReAct loop for the next unfinished item."""
        item_to_process = self._get_next_work_unit(state)
        if item_to_process is None:
            return {}

        completed_count = len(state.completed_items)
        batch_index = completed_count + 1
        item_name = str(item_to_process.get("name", ""))
        item_key = str(item_to_process.get(ITEM_KEY_FIELD, ""))

        await emit_progress_event(
            "item_started",
            estimation_id=state.estimation_id,
            batch_index=batch_index,
            item_name=item_name,
            item_key=item_key,
            completed_items=completed_count,
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
            HumanMessage(content=FEW_SHOT_EXAMPLE),
            HumanMessage(
                content=(
                    f"Please process this item now: {item_name}. "
                    "Search the catalog only when needed, get prices, and finally "
                    "call `SaveItemResult`."
                )
            ),
        ]

        result_estimation: dict[str, Any] | None = None

        for attempt in range(self.MAX_RETRIES + 1):
            for _iteration in range(self.MAX_ITERATIONS):
                try:
                    response = await self._invoke_llm_with_progress(
                        state=state,
                        batch_index=batch_index,
                        messages=messages,
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

                messages.append(response)

                if not isinstance(response, AIMessage) or not response.tool_calls:
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
                        tool_result = await self._invoke_tool_with_progress(
                            state=state,
                            batch_index=batch_index,
                            tool_name=tool_name,
                            tool_fn=tool_fn,
                            tool_args=tool_args,
                        )
                        from langchain_core.messages import ToolMessage
                        messages.append(
                            ToolMessage(
                                content=self._serialize_tool_result(tool_result),
                                tool_call_id=tool_call["id"],
                            )
                        )
                    except Exception as e:
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
                
                if attempt < self.MAX_RETRIES:
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
                    logger.error("Item '%s' failed after %d retries", item_name, self.MAX_RETRIES)
            else:
                if attempt < self.MAX_RETRIES:
                    messages.append(
                        HumanMessage(
                            content="You did not call SaveItemResult. "
                            "Please output the SaveItemResult tool call "
                            "with the final estimation for the current item."
                        )
                    )
                else:
                    logger.error("Item '%s' never called SaveItemResult", item_name)

        new_knowledge = dict(state.knowledge_store)
        if result_estimation is not None:
            result_estimation[ITEM_KEY_FIELD] = item_key
            for ingredient in result_estimation.get("ingredients", []):
                source = ingredient.get("source")
                name = ingredient.get("name")
                sysco_item_number = ingredient.get("sysco_item_number")
                normalized_name = str(name).lower().strip() if name else ""
                if normalized_name and source == "not_available":
                    new_knowledge[normalized_name] = "not_available"
                elif normalized_name and source == "estimated":
                    new_knowledge[normalized_name] = "estimated"
                elif normalized_name and source == "sysco_catalog" and sysco_item_number:
                    new_knowledge[normalized_name] = f"found:{sysco_item_number}"

            if len(new_knowledge) > 30:
                new_knowledge = dict(list(new_knowledge.items())[-30:])

            return self._build_terminal_update(
                state=state,
                completed_items=[result_estimation],
                knowledge_store=new_knowledge,
            )

        fallback_result = {
            "item_name": item_name,
            "category": item_to_process.get("category"),
            "ingredients": [],
            "ingredient_cost_per_unit": 0.0,
            "status": "failed",
            ITEM_KEY_FIELD: item_key,
        }

        return self._build_terminal_update(
            state=state,
            completed_items=[fallback_result],
            knowledge_store=new_knowledge,
        )


BatchWorkerNode = ItemWorkerNode
