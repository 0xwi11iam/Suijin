"""The landing's model picker list: complete lists are cached, incomplete ones are retried."""

from __future__ import annotations

from suijin.server.web import console_api


def test_a_provider_with_no_models_keeps_the_list_out_of_the_cache(monkeypatch):
    """Seen live (2026-11-27): the first call after a restart failed for one connection, the list was
    cached without that connection's models, and the picker missed them for the whole cache window."""
    monkeypatch.setattr(console_api, "_available_models_cache", {"at": 0.0, "models": [], "providers": []})
    monkeypatch.setattr(
        console_api,
        "provider_catalog",
        lambda: {
            "providers": [
                {"key": "a", "label": "A", "configured": True},
                {"key": "b", "label": "B", "configured": True},
                {"key": "off", "label": "Off", "configured": False},
            ]
        },
    )
    lists = {"a": ["m1", "m2"], "b": []}
    monkeypatch.setattr(console_api, "_provider_model_ids", lambda key: lists[key])

    first = console_api.available_models()
    assert first["cached"] is False
    assert [model["value"] for model in first["models"]] == ["a:m1", "a:m2"]
    assert [provider["key"] for provider in first["providers"]] == ["a", "b"]
    assert console_api._available_models_cache["models"] == []  # incomplete: nothing cached

    lists["b"] = ["m3"]  # the connection answers again
    second = console_api.available_models()
    assert second["cached"] is False and len(second["models"]) == 3
    assert len(console_api._available_models_cache["models"]) == 3  # complete: cached
    assert console_api.available_models()["cached"] is True
