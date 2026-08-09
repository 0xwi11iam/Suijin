"""Traffic normalizer — learn normal patterns, zero LLM cost.

Smart baseline learning:
- First N requests establish the baseline blindly.
- After baseline, known patterns skip AI entirely.
- Anomalous requests go to AI; if AI says innocent, pattern is added to baseline.
- Patterns are hashed by (method, path_structure, param_keys, body_structure)
  so minor variations (different IDs, timestamps) don't break matching.

Global singleton available via get_global_normalizer().
"""
import json, math, hashlib
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ── Global singleton for cross-module access ──
_global_normalizer: Optional["SmartNormalizer"] = None


def get_global_normalizer() -> Optional["SmartNormalizer"]:
    return _global_normalizer


def set_global_normalizer(normalizer: "SmartNormalizer"):
    global _global_normalizer
    _global_normalizer = normalizer


class SmartNormalizer:
    """Learns and matches normal traffic patterns to minimize AI cost."""

    def __init__(self):
        self.profiles: dict = {}
        self.known_patterns: set = set()     # Set of pattern hashes
        self.endpoint_methods: dict = {}      # endpoint -> set of known methods
        self.endpoint_params: dict = {}       # endpoint -> set of known param keys
        self.baseline_requests: list = []     # Store first N requests for training
        self.samples_seen = 0
        self.training_complete = False

    def train(self, requests: list, training_turns: int = 10):
        """Feed requests into the normalizer during baseline phase."""
        for req in requests:
            self._learn_from_request(req)

    def _learn_from_request(self, req: dict):
        """Internal: learn all patterns from a single request."""
        ep = req.get("path", "/")
        method = req.get("method", "GET")

        # Track endpoint profiles
        if ep not in self.profiles:
            self.profiles[ep] = {
                "methods": defaultdict(int),
                "statuses": defaultdict(int),
                "param_names": defaultdict(int),
                "body_sizes": [],
                "user_agents": defaultdict(int),
                "ips": set(),
                "request_count": 0,
                "avg_body_size": 0,
            }
        p = self.profiles[ep]
        p["methods"][method] += 1
        p["statuses"][str(req.get("status", 200))] += 1
        p["ips"].add(req.get("ip", ""))
        p["request_count"] += 1

        body = str(req.get("body", ""))
        body_size = len(body)
        if body_size > 0:
            p["body_sizes"].append(body_size)
            p["avg_body_size"] = sum(p["body_sizes"]) / len(p["body_sizes"])

        # Track known params
        query = req.get("query", {})
        if isinstance(query, dict):
            for k in query:
                p["param_names"][k] += 1

        # Build pattern hash and add to known set
        pattern_hash = self._hash_pattern(req)
        self.known_patterns.add(pattern_hash)

        # Track endpoint-method combinations
        if ep not in self.endpoint_methods:
            self.endpoint_methods[ep] = set()
        self.endpoint_methods[ep].add(method)

        # Track endpoint-param combinations
        if ep not in self.endpoint_params:
            self.endpoint_params[ep] = set()
        if isinstance(query, dict):
            self.endpoint_params[ep].update(query.keys())

        self.samples_seen += 1

    def _hash_pattern(self, req: dict) -> str:
        """Create a stable hash for a request pattern.

        Normalizes away:
        - Specific ID values in paths (/api/users/42 -> /api/users/:id)
        - Specific query param values (search=foo -> search=*)
        - Body content that varies (CSRF tokens, timestamps)
        """
        method = req.get("method", "GET")
        path = req.get("path", "/")

        # Normalize path: replace numeric segments with :id
        import re
        normalized_path = re.sub(r'/\d+', '/:id', path)

        # Normalize query params: keep keys, drop values
        query = req.get("query", {})
        if isinstance(query, dict) and query:
            param_keys = sorted(query.keys())
            query_str = "&".join(f"{k}=*" for k in param_keys)
        else:
            query_str = ""

        # Normalize body: check for key structural elements
        body = str(req.get("body", ""))
        if body:
            # Try to parse as form data
            try:
                from urllib.parse import parse_qs
                parsed = parse_qs(body)
                body_str = "&".join(f"{k}=*" for k in sorted(parsed.keys()))
            except Exception:
                # For JSON bodies, just note it's JSON and its top-level keys
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        body_str = "json:" + ",".join(sorted(parsed.keys()))
                    else:
                        body_str = "json:array"
                except (json.JSONDecodeError, TypeError):
                    body_str = f"raw:{len(body)}"
        else:
            body_str = ""

        pattern = f"{method}:{normalized_path}"
        if query_str:
            pattern += f"?{query_str}"
        if body_str:
            pattern += f"|{body_str}"

        return hashlib.md5(pattern.encode()).hexdigest()[:16]

    def is_known_normal(self, request: dict) -> bool:
        """Check if a request matches a known normal pattern.

        Returns True if this request pattern has been seen before and
        can safely skip AI analysis.
        """
        pattern_hash = self._hash_pattern(request)

        if pattern_hash in self.known_patterns:
            return True

        # Also check: same endpoint + same method + no unusual params
        ep = request.get("path", "/")
        method = request.get("method", "GET")

        if ep in self.endpoint_methods and method in self.endpoint_methods[ep]:
            # Method is known for this endpoint. Check params.
            query = request.get("query", {})
            if isinstance(query, dict):
                unknown_params = set(query.keys()) - self.endpoint_params.get(ep, set())
                if not unknown_params:
                    # Same endpoint + same method + all params known = likely normal
                    # But not in known_patterns yet, so this is a new variation.
                    # We DON'T auto-add — let AI verify once.
                    return False
                # Has unknown params -> definitely anomalous
                return False
            # No query params, method known -> probably normal variation
            return False

        # Endpoint or method never seen -> anomalous
        return False

    def add_to_baseline(self, request: dict):
        """Add a request pattern to the known-normal baseline.

        Called after AI confirms a request is benign.
        """
        self._learn_from_request(request)

    def get_profile(self, endpoint: str) -> dict:
        """Get the traffic profile for an endpoint."""
        return self.profiles.get(endpoint, {
            "methods": defaultdict(int),
            "request_count": 0,
            "ips": set(),
            "param_names": defaultdict(int),
            "avg_body_size": 0,
        })

    def is_normal(self, request: dict) -> bool:
        """Legacy method — delegates to is_known_normal."""
        return self.is_known_normal(request)


# ── Keep backward compatibility ──
TrafficNormalizer = SmartNormalizer

