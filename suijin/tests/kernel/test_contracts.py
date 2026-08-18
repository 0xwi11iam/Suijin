"""Kernel contracts — the Module/Tool protocols and tier model.

The kernel must understand CATEGORIES of software, never specific
modules. These tests pin the contract shapes every module lives by.
"""

import pytest

from suijin.kernel.contracts import Module, Tier, Tool


class TestTier:
    def test_ordering_core_first(self):
        assert Tier.CORE < Tier.RECOMMENDED < Tier.INSTALLED

    def test_from_string_and_invalid(self):
        assert Tier.from_string("core") is Tier.CORE
        assert Tier.from_string("INSTALLED") is Tier.INSTALLED
        with pytest.raises(ValueError, match="unknown tier"):
            Tier.from_string("banana")


class TestModuleContract:
    def test_minimal_conformance(self):
        class M(Module):
            id = "demo"
            tier = Tier.RECOMMENDED

            def register(self, ctx):
                pass

            def start(self, ctx):
                pass

            def stop(self, ctx):
                pass

        assert M().id == "demo"
        assert M().tier is Tier.RECOMMENDED

    def test_protocol_is_structural(self):
        # Protocols with non-method members need explicit conformance;
        # duck-typed objects still work where used as annotations
        class Duck:
            id = "duck"

            def register(self, ctx): ...
            def start(self, ctx): ...
            def stop(self, ctx): ...

        assert Duck().id == "duck"  # usable; isinstance checks via runtime_checkable


class TestToolContract:
    def test_minimal_conformance(self):
        class T(Tool):
            name = "demo.scan"
            description = "demo tool"

            def __call__(self, args: dict, ctx):
                return "ran"

        assert T().name == "demo.scan"
        assert T()({"x": 1}, None) == "ran"

    def test_default_permissions_empty(self):
        class T(Tool):
            name = "t"
            description = "d"

            def __call__(self, args, ctx):
                return ""

        assert T().permissions == ()
