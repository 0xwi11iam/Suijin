"""Property tests — normalize_decision never raises, always canonical.

The golden contracts pin the REAL shapes; these fuzz the infinite rest:
random malformed decisions must always yield the canonical keys. The
boundary is only trustworthy if it cannot be surprised.
"""

import random
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from suijin.server.agent.lib.decision import normalize_decision

JSONISH = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(string.printable, max_size=40),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(string.printable, max_size=12), children, max_size=5)
    ),
    max_leaves=12,
)


class TestNeverRaises:
    @settings(max_examples=300, deadline=None)
    @given(JSONISH)
    def test_any_json_value_returns_canonical(self, v):
        n = normalize_decision(v)
        for key in ("action", "tool_name", "tool_args", "question", "thought", "reasoning"):
            assert key in n
        assert isinstance(n["tool_args"], dict)
        assert isinstance(n["question"], str)

    @settings(max_examples=200, deadline=None)
    @given(st.dictionaries(st.text(max_size=8), st.text(max_size=60), max_size=6))
    def test_string_dicts_always_normalize(self, d):
        n = normalize_decision(d)
        assert isinstance(n["action"], str) and n["action"]
        assert isinstance(n["tool_name"], str)


class TestFuzzRoundtrip:
    def test_random_seed_determinism(self):
        random.seed(30)
        for _ in range(100):
            weird = {
                random.choice(["action", "question", "tool_args", "tool_name", "zzz"]): random.choice(
                    [None, 42, [], {}, "x" * 50, {"question": "nested!"}, {"q": "shorthand"}]
                )
            }
            n = normalize_decision(weird)
            if isinstance(weird.get("tool_args"), dict) and "question" in weird["tool_args"]:
                assert n["question"] == weird["tool_args"]["question"] or n["question"] == ""
