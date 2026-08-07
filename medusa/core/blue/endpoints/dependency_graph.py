"""Dependency graph — which endpoints call which internal functions."""
def build_dependency_graph(endpoints: list) -> dict:
    graph = {}
    for ep in endpoints:
        graph[ep.get("path","/")] = {"calls": [], "called_by": []}
    return graph
