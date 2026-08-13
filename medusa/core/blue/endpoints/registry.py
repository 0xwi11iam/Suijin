"""Endpoint registry — master list of all discovered endpoints."""
from __future__ import annotations
class EndpointRegistry:
    def __init__(self):
        self.endpoints = {}
    def register(self, endpoint: dict):
        key = f"{endpoint.get('method','GET')}:{endpoint.get('path','/')}"
        self.endpoints[key] = endpoint
    def get(self, method: str, path: str) -> dict:
        return self.endpoints.get(f"{method}:{path}", {})
    def all(self) -> list:
        return list(self.endpoints.values())
