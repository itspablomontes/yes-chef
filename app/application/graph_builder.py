"""Graph builder — constructs the compiled LangGraph.

The graph is built once at startup and reused for all estimations.
State is ephemeral (created per estimation), graph is a singleton.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.batch_router import route_work_item
from app.agent.nodes.batch_worker import ItemWorkerNode
from app.agent.nodes.reduce import reduce
from app.agent.state import EstimationState


class GraphBuilder:
    """Builds the estimation graph with a single-item durable workflow.

    [START] → [item_worker] ↻ [item_worker] → [reduce]

    The graph is compiled once and reused. Each estimation gets
    fresh state injected at runtime.
    """

    def __init__(self, llm: ChatOpenAI) -> None:
        self._llm = llm

    def build(self) -> StateGraph:
        """Build and return the state graph (not yet compiled)."""
        graph = StateGraph(EstimationState)

        item_worker_node = ItemWorkerNode(llm=self._llm)

        # Add nodes
        graph.add_node("item_worker", item_worker_node)
        graph.add_node("reduce", reduce)

        # Conditional edges for the single-item workflow loop
        graph.add_conditional_edges(START, route_work_item, ["item_worker", "reduce"])
        graph.add_conditional_edges("item_worker", route_work_item, ["item_worker", "reduce"])

        # Reduce → END
        graph.add_edge("reduce", END)

        return graph
