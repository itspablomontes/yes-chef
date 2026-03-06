"""Sequential batch worker node.

Processes multiple menu items in a single ReAct loop to minimize
token usage and rate limit spikes. Evaluates a batch of dishes,
looks up ingredients across the batch, and returns a list of completed items.
"""

from __future__ import annotations

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
from app.infrastructure.settings import get_settings

logger = logging.getLogger(__name__)


# Structured output model for saving the batch of results
class SaveBatchResult(BaseModel):
    """Save the estimation results for ALL items in the current batch."""
    estimations: list[ItemEstimation] = Field(description="The complete list of estimations for the processed batch")


def build_batch_system_prompt(
    items_to_process: list[dict[str, Any]],
    knowledge: dict[str, str],
) -> str:
    """Build the system prompt for a batch of items."""
    
    knowledge_hints = ""
    if knowledge:
        lines = ["\n## Known Catalog Findings (from previous items):"]
        for ingredient, status in knowledge.items():
            if status == "not_available":
                lines.append(f"- {ingredient}: NOT AVAILABLE in Sysco catalog. Mark as not_available.")
            elif status == "estimated":
                lines.append(f"- {ingredient}: Not in catalog, use estimated pricing.")
        knowledge_hints = "\n".join(lines)

    items_text = "\n".join([
        f"- {i.get('name', 'Unknown')} (Category: {i.get('category', 'Unknown')})\n"
        f"  Description: {i.get('description', '')}\n"
        f"  Dietary: {i.get('dietary_notes', 'None')}"
        for i in items_to_process
    ])

    return f"""You are an elite culinary cost estimator for Elegant Foods catering.

Your job is to break down event menu items into component ingredients, look them up in the
Sysco supplier catalog, and produce structured cost estimates.

## The Process
1. I will give you a BATCH of {len(items_to_process)} menu items to process.
2. Formulate a list of every ingredient needed across all dishes in the batch.
3. Use the `search_catalog` tool to find each ingredient in Sysco.
4. For items you find, use the `get_item_price` tool to calculate the exact per-serving unit cost
   based on the dish's portion size.
5. Finally, call the `save_batch_result` tool to submit the final estimations for EVERY item in the batch.
{knowledge_hints}

## Rules
- You must use tools to search and price. NEVER guess a unit cost without pulling it from a tool.
- The Sysco catalog uses reverse-comma notation (e.g., "bacon applewood" -> "BACON, SMOKED, APPLEWOOD").
- If a specialty item (like wagyu, truffle, saffron) isn't in Sysco, DO NOT guess a price. 
  Mark `unit_cost` as null and `source` as "not_available".
- If a basic item (like salt, pepper, oil) isn't in Sysco, mark it "estimated" and assign a
  reasonable small unit_cost (e.g., 0.05).
- Always ensure `ingredient_cost_per_unit` equals the EXACT mathematical sum of all
  ingredient `unit_cost`s for that dish.

## Current Batch Items:
{items_text}
"""


class BatchWorkerNode:
    """Processes a batch of menu items sequentially."""

    MAX_RETRIES = 2
    MAX_ITERATIONS = 20  # ReAct loop iterations (slightly higher for batches)
    
    def __init__(self, llm: ChatOpenAI) -> None:
        self._llm = llm
        self._tools = get_all_tools()
        self._tools_by_name = {t.name: t for t in self._tools}
        
        # We bind the standard tools, plus our specific "save_batch_result" structured payload
        self._llm_with_tools = self._llm.bind_tools(self._tools + [SaveBatchResult])
        self._batch_size = get_settings().batch_size

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(10),
        reraise=True,
    )
    async def _safe_invoke(self, messages: list[Any]) -> Any:
        return await self._llm_with_tools.ainvoke(messages)

    def _get_next_batch(self, state: EstimationState, batch_size: int = 5) -> list[dict[str, Any]]:
        """Identify up to batch_size unprocessed items."""
        menu_spec = state.menu_spec
        categories: dict[str, Any] = menu_spec.get("categories", {})

        completed_names: set[str] = set()
        for item in state.completed_items:
            if isinstance(item, dict):
                name = item.get("item_name", "")
                if name:
                    completed_names.add(name)

        batch = []
        for category_name, items in categories.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_name = item.get("name", "")
                if item_name and item_name not in completed_names:
                    # Inject category into the dict for convenience
                    item_copy = dict(item)
                    item_copy["category"] = category_name
                    batch.append(item_copy)
                    if len(batch) >= batch_size:
                        return batch
        return batch

    async def __call__(self, state: EstimationState) -> dict[str, Any]:
        """Execute the ReAct loop for a batch of menu items."""
        items_to_process = self._get_next_batch(state, batch_size=self._batch_size)
        if not items_to_process:
            return {}
            
        logger.info(
            "Processing batch of %d items: %s", 
            len(items_to_process),
            [i.get("name") for i in items_to_process]
        )

        system_prompt = build_batch_system_prompt(
            items_to_process=items_to_process,
            knowledge=state.knowledge_store,
        )

        batch_names = [i.get('name', '') for i in items_to_process]
        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=FEW_SHOT_EXAMPLE), # We provide the standard 1-item few-shot to show the process
            HumanMessage(
                content=f"Please process the following items now: {', '.join(batch_names)}. "
                f"Search the catalog, get prices, and finally call `SaveBatchResult`."
            ),
        ]

        result_estimations: list[dict[str, Any]] | None = None

        for attempt in range(self.MAX_RETRIES + 1):
            for _iteration in range(self.MAX_ITERATIONS):
                try:
                    response = await self._safe_invoke(messages)
                except RateLimitError as e:
                    logger.error("Rate limit exhausted for batch: %s", e)
                    break
                except Exception as e:
                    # Catch Pydantic ValidationErrors from structured output coercion
                    # If LangChain fails to parse the tool arguments into the Pydantic schema
                    error_str = str(e)
                    if "ValidationError" in error_str or "validation error" in error_str.lower():
                        logger.warning(
                            "Pydantic Structured Output Validation Failed. Instructing LLM to correct schema: %s",
                            e
                        )
                        messages.append(
                            AIMessage(content="", tool_calls=[{"name": "SaveBatchResult", "args": {}, "id": "call_failed"}])
                        )
                        from langchain_core.messages import ToolMessage
                        messages.append(
                            ToolMessage(
                                tool_call_id="call_failed",
                                name="SaveBatchResult",
                                content=f"Validation Error: {error_str}\n\nPlease strictly follow the required schema and try calling SaveBatchResult again.",
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
                    tool_args = tool_call["args"]

                    if tool_name == "SaveBatchResult":
                        estimations: list[Any] = tool_args.get("estimations", [])
                        # Convert models back to dicts if they are pydantic objects
                        result_estimations = []
                        for est in estimations:
                            if isinstance(est, dict):
                                result_estimations.append(est)
                            elif hasattr(est, "model_dump"):
                                result_estimations.append(est.model_dump())
                        # We must return a tool message to satisfy OpenAI's API requirements before proceeding
                        from langchain_core.messages import ToolMessage
                        messages.append(
                            ToolMessage(
                                content="Batch result saved for validation.", 
                                tool_call_id=tool_call["id"]
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
                        tool_result = await tool_fn.ainvoke(tool_args)
                        from langchain_core.messages import ToolMessage
                        messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))
                    except Exception as e:
                        from langchain_core.messages import ToolMessage
                        logger.warning("Tool %s failed: %s", tool_name, e)
                        messages.append(
                            ToolMessage(
                                content=f"Error executing {tool_name}: {e!s}", 
                                tool_call_id=tool_call["id"]
                            )
                        )
                        
                if result_estimations is not None:
                    break # exit react loop

            # Validate the result batch
            if result_estimations is not None:
                all_passed = True
                validation_errors = []
                
                # Check that every requested item was actually returned
                returned_names = [r.get("item_name") for r in result_estimations]
                missing = [name for name in batch_names if name not in returned_names]
                if missing:
                    validation_errors.append(f"Missing items in SaveBatchResult: {missing}")
                    all_passed = False

                for res in result_estimations:
                    errors = validate_item_estimation(
                        item_name=str(res.get("item_name", "")),
                        category=str(res.get("category", "")),
                        ingredients=res.get("ingredients", []),
                        ingredient_cost_per_unit=float(res.get("ingredient_cost_per_unit") or 0),
                    )
                    if errors:
                        all_passed = False
                        validation_errors.append(f"Item '{res.get('item_name')}' errors: " + "; ".join(errors))

                if all_passed:
                    logger.info("Batch of %d items passed validation", len(batch_names))
                    break
                
                if attempt < self.MAX_RETRIES:
                    error_msg = "\n".join(f"- {e}" for e in validation_errors)
                    logger.warning("Batch failed validation (attempt %d): %s", attempt + 1, error_msg)
                    messages.append(
                        HumanMessage(
                            content=f"Your previous result had validation errors:\n{error_msg}\n\n"
                            f"Please reprocess the items and call SaveBatchResult "
                            f"again with corrections."
                        )
                    )
                    result_estimations = None
                else:
                    logger.error("Batch failed after %d retries", self.MAX_RETRIES)
            else:
                if attempt < self.MAX_RETRIES:
                    messages.append(
                        HumanMessage(
                            content="You did not call SaveBatchResult. "
                            "Please output the SaveBatchResult tool call "
                            "with the final list of estimations."
                        )
                    )
                else:
                    logger.error("Batch never called SaveBatchResult")

        # Update knowledge store dynamically based on these results
        # so the next sequential batch gets the benefit immediately.
        new_knowledge = dict(state.knowledge_store)
        fallback_results = []
        
        if result_estimations is not None:
            # Reconstruct knowledge and merge
            for item in result_estimations:
                for ingredient in item.get("ingredients", []):
                    source = ingredient.get("source")
                    name = ingredient.get("name")
                    if name and source == "not_available":
                        new_knowledge[name] = "not_available"
                    elif name and source == "estimated":
                        new_knowledge[name] = "estimated"
                        
            # Apply LRU Bounding (max 30 items)
            # Dictionaries maintain insertion order in Python 3.7+
            if len(new_knowledge) > 30:
                # Keep only the newest 30
                new_knowledge = dict(list(new_knowledge.items())[-30:])
            
            return {
                "completed_items": result_estimations,
                "knowledge_store": new_knowledge
            }

        # Failed batch — return failure objects so the graph doesn't hang
        for item in items_to_process:
            fallback_results.append({
                "item_name": item.get("name"),
                "category": item.get("category"),
                "ingredients": [],
                "ingredient_cost_per_unit": 0.0,
                "status": "failed",
            })
            
        return {
            "completed_items": fallback_results,
            "knowledge_store": new_knowledge
        }
