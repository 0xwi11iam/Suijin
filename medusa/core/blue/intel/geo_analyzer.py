"""Geo analyzer — GeoIP lookup."""
def get_geo(ip: str) -> dict:
    return {"ip": ip, "country": "unknown", "city": "unknown"}
