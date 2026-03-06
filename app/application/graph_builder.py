"""Graph builder — constructs the compiled LangGraph.

The graph is built once at startup and reused for all estimations.
State is ephemeral (created per estimation), graph is a singleton.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.batch_router import route_batch
from app.agent.nodes.batch_worker import BatchWorkerNode
from app.agent.nodes.reduce import reduce
from app.agent.state import EstimationState


class GraphBuilder:
    """Builds the estimation graph with sequential batched loop topology.

    [START] → [batch_router] ↻ [batch_worker] → [reduce]

    The graph is compiled once and reused. Each estimation gets
    fresh state injected at runtime.
    """

    def __init__(self, llm: ChatOpenAI) -> None:
        self._llm = llm

    def build(self) -> StateGraph:
        """Build and return the state graph (not yet compiled)."""
        graph = StateGraph(EstimationState)

        # Create callable batch worker with LLM
        batch_worker_node = BatchWorkerNode(llm=self._llm)

        # Add nodes
        graph.add_node("batch_worker", batch_worker_node)
        graph.add_node("reduce", reduce)

        # Conditional edges for sequential loop
        graph.add_conditional_edges(START, route_batch, ["batch_worker", "reduce"])
        graph.add_conditional_edges("batch_worker", route_batch, ["batch_worker", "reduce"])

        # Reduce → END
        graph.add_edge("reduce", END)

        return graph
