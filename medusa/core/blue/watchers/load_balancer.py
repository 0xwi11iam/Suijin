"""Load balancer — distribute watchers by traffic volume."""
def balance_watchers(endpoints: list, traffic_stats: dict, max_per: int = 3) -> dict:
    allocation = {}
    sorted_eps = sorted(endpoints, key=lambda e: traffic_stats.get(e.get("path","/"), 0), reverse=True)
    for ep in sorted_eps:
        vol = traffic_stats.get(ep.get("path","/"), 1)
        count = min(max_per, max(1, int(vol / 10)))
        allocation[ep.get("path","/")] = count
    return allocation
