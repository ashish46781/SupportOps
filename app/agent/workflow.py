from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    Services,
    diagnose,
    finalize,
    load_context,
    plan,
    retrieve,
    rewrite_query,
    verification_route,
    verify,
)
from app.agent.state import InvestigationState


def build_workflow(services: Services):
    graph = StateGraph(InvestigationState)
    graph.add_node("load_context", lambda state: load_context(state, services))
    graph.add_node("plan", lambda state: plan(state, services))
    graph.add_node("retrieve", lambda state: retrieve(state, services))
    graph.add_node("diagnose", lambda state: diagnose(state, services))
    graph.add_node("verify", lambda state: verify(state, services))
    graph.add_node("rewrite_query", lambda state: rewrite_query(state, services))
    graph.add_node("finalize", lambda state: finalize(state, services))
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "plan")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "diagnose")
    graph.add_edge("diagnose", "verify")
    graph.add_conditional_edges(
        "verify", verification_route, {"rewrite_query": "rewrite_query", "finalize": "finalize"}
    )
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("finalize", END)
    return graph.compile()
