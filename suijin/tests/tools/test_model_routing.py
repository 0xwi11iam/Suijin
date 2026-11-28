"""Tiered model routing: a model is called on the provider that lists it.

Field report 2026-11-28: the active provider was Z.ai, the leader model glm-5.3, and the WORKER
route a bare Numes id (``deepseek/deepseek-v4.1-flash:free``). An agent is a worker, so it sent a
Numes model id to Z.ai and every call answered ``400 Unknown Model`` — while the leader's calls on
the same provider succeeded. The route stored the model without its provider.
"""

from __future__ import annotations

import pytest

from suijin.server.tools.lib import model_routing


@pytest.fixture()
def owners(tmp_path, monkeypatch):
    """Where the console records which configured provider lists which model."""
    path = tmp_path / "model-owners.json"
    monkeypatch.setattr(model_routing, "model_owners_path", lambda: path)
    return path


def _config(provider: str, **models) -> dict:
    cfg = {"provider": provider}
    cfg.update(models)
    return cfg


def test_a_provider_qualified_route_goes_to_that_provider(owners, monkeypatch):
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": "numes:numes/fast")
    cfg = model_routing.apply_routing({"provider": "zai", "zai_model": "glm-5.3"}, task="worker")
    assert cfg["provider"] == "numes" and cfg["numes_model"] == "numes/fast"


def test_a_bare_model_id_goes_to_the_provider_that_lists_it(owners, monkeypatch):
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": "deepseek/deepseek-v4.1-flash:free")
    model_routing.remember_model_owners({"zai": ["glm-5.3"], "numes": ["deepseek/deepseek-v4.1-flash:free"]})
    cfg = model_routing.apply_routing(_config("zai", zai_model="glm-5.3"), task="worker")
    assert cfg["provider"] == "numes"
    assert cfg["numes_model"] == "deepseek/deepseek-v4.1-flash:free"
    assert cfg["zai_model"] == "glm-5.3"  # the leader's own setting is untouched


def test_a_bare_model_the_active_provider_lists_stays_there(owners, monkeypatch):
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": "glm-5.3-flash")
    model_routing.remember_model_owners({"zai": ["glm-5.3", "glm-5.3-flash"], "numes": ["glm-5.3-flash"]})
    cfg = model_routing.apply_routing(_config("zai", zai_model="glm-5.3"), task="worker")
    assert cfg["provider"] == "zai" and cfg["zai_model"] == "glm-5.3-flash"


def test_the_config_itself_names_the_owner_when_a_provider_model_setting_holds_that_id(owners, monkeypatch):
    """The model picker writes `<provider>_model` for the model it sets. That setting is the
    owner, and it needs no listing at all: the first agent after a fresh install is routed right."""
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": "deepseek/deepseek-v4.1-flash:free")
    owners.unlink(missing_ok=True)
    cfg = {"provider": "zai", "zai_model": "glm-5.3", "numes_model": "deepseek/deepseek-v4.1-flash:free"}
    routed = model_routing.apply_routing(cfg, task="tier:worker")
    assert routed["provider"] == "numes" and routed["numes_model"] == "deepseek/deepseek-v4.1-flash:free"
    assert model_routing.routing_doubt(cfg, "worker") == ""  # the config settles it: nothing to warn about


def test_when_the_active_provider_and_another_one_both_name_the_model_the_active_one_is_kept(owners, monkeypatch):
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": "numes/fast")
    cfg = {"provider": "numes", "numes_model": "numes/fast", "openrouter_model": "numes/fast"}
    assert model_routing.apply_routing(cfg, task="triage")["provider"] == "numes"


def test_the_route_can_be_named_for_a_log_line(owners, monkeypatch):
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": "numes:numes/fast")
    assert model_routing.routing_note({"provider": "zai"}, "worker") == ("numes:numes/fast", "numes")
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": None)
    assert model_routing.routing_note({"provider": "zai"}, "worker") == ("", "zai")


def test_a_model_nobody_listed_keeps_the_active_provider_and_says_so(owners, monkeypatch):
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": "some-vendor/some-model")
    model_routing.remember_model_owners({"zai": ["glm-5.3"]})
    original = _config("zai", zai_model="glm-5.3")
    # …the doubt is on record BEFORE the route is applied, where a failing call can quote it
    assert model_routing.routing_doubt(original, "worker") == (
        "some-vendor/some-model is not in the model list of zai, the active provider; the call is being made there anyway"
    )
    cfg = model_routing.apply_routing(dict(original), task="worker")
    assert cfg["provider"] == "zai" and cfg["zai_model"] == "some-vendor/some-model"


def test_a_corrupt_or_missing_owner_record_is_not_an_error(owners):
    owners.write_text("{not json")
    assert model_routing.model_owners() == {}
    owners.unlink(missing_ok=True)
    assert model_routing.model_owners() == {}
    assert model_routing.owner_of("glm-5.3", "zai") == ""


def test_the_owner_record_is_the_latest_complete_listing(owners):
    model_routing.remember_model_owners({"zai": ["glm-5.3"], "openrouter": ["a/b"]})
    assert model_routing.model_owners() == {"glm-5.3": ["zai"], "a/b": ["openrouter"]}
    model_routing.remember_model_owners({"numes": ["a/b", "numes/fast"], "zai": ["glm-5.3"]})
    assert model_routing.model_owners() == {"a/b": ["numes"], "numes/fast": ["numes"], "glm-5.3": ["zai"]}
    model_routing.remember_model_owners({"numes": ["numes/fast"]})  # the latest complete listing: the others are gone
    assert model_routing.model_owners() == {"numes/fast": ["numes"]}


def test_the_flat_setting_keys_feed_the_nested_route(monkeypatch):
    monkeypatch.setattr(model_routing, "_routing_config", lambda: {"worker": "numes:numes/fast"})
    assert model_routing.model_for("worker") == "numes:numes/fast"
    cfg = model_routing.apply_routing({"provider": "zai", "zai_model": "glm-5.3"}, task="worker")
    assert cfg == {"provider": "numes", "numes_model": "numes/fast", "zai_model": "glm-5.3", "model_1": "numes/fast"}
