"""Dependency graph — which endpoints call which internal functions."""
from __future__ import annotations
def build_dependency_graph(endpoints: list) -> dict:
    graph = {}
    for ep in endpoints:
        graph[ep.get("path","/")] = {"calls": [], "called_by": []}
    return graph
