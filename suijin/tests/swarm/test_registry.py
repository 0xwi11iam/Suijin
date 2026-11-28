"""The swarm registry (S0 of the 2026-11-28 swarm plan): a swarm is a thing with a
definition — one folder per target, swarm.json as the truth, legacy folded in."""

import json
import os

import pytest

from suijin.server.swarm import registry


@pytest.fixture()
def targets_root(tmp_path, monkeypatch):
    root = tmp_path / "Targets"
    monkeypatch.setenv(registry._ROOT_ENV, str(root))
    return root


def test_create_writes_the_definition_and_the_layout(targets_root):
    d = registry.create(
        "Immutable",
        "Hunt the SSO hub",
        lanes=["recon the edge", "probe the API"],
        host_n=3,
        boxed_n=1,
        brain={"provider": "numes", "model": "deepseek"},
        permissions="exploitation",
        mode="BBP",
        chat="chat-123",
    )
    folder = targets_root / "immutable" / "swarms" / d["id"]
    assert (folder / "swarm.json").exists()
    for sub in ("inbox", "exploits", "findings", "home"):
        assert (folder / sub).is_dir()
    on_disk = json.loads((folder / "swarm.json").read_text())
    assert on_disk["title"] == "Hunt the SSO hub"
    assert on_disk["target"] == "immutable"
    assert on_disk["lanes"] == ["recon the edge", "probe the API"]
    assert on_disk["agents"] == {"host": 3, "boxed": 1}
    assert on_disk["status"] == "defining"
    assert on_disk["chat"] == "chat-123"
    assert d["id"].startswith("swarm-")


def test_load_and_status_round_trip(targets_root):
    d = registry.create("Acme", "Probe the storefront")
    assert registry.load(d["id"])["title"] == "Probe the storefront"
    registry.set_status(d["id"], "running")
    assert registry.load(d["id"])["status"] == "running"
    assert registry.load(d["id"])["history"][-1]["status"] == "running"
    with pytest.raises(ValueError):
        registry.set_status(d["id"], "exploded")
    assert registry.set_status("swarm-none", "running") is None


def test_list_orders_newest_first_and_folds_legacy(tmp_path, targets_root, monkeypatch):
    old = registry.create("Acme", "older swarm")
    new = registry.create("Beta", "newer swarm")
    engagements = tmp_path / ".suijin" / "engagements"
    (engagements / "old-run").mkdir(parents=True)
    (engagements / "old-run" / "engagement.json").write_text(json.dumps({"title": "An old run", "target": "Gamma"}))
    monkeypatch.setenv("HOME", str(tmp_path))
    listed = registry.list_swarms()
    ids = [row["id"] for row in listed]
    assert set(ids) >= {old["id"], new["id"], "old-run"}
    assert listed[0]["id"] == new["id"]  # newest first
    legacy = next(row for row in listed if row["id"] == "old-run")
    assert legacy["status"] == "legacy"
    assert legacy["title"] == "An old run"
    assert legacy["target"] == "gamma"
    # an engagements folder without metadata is NOT an engagement (the _default husk)
    (engagements / "_default").mkdir()
    assert "_default" not in [row["id"] for row in registry.list_swarms()]


def test_migrate_legacy_moves_the_folder_and_writes_the_definition(tmp_path, targets_root, monkeypatch):
    engagements = tmp_path / ".suijin" / "engagements"
    src = engagements / "old-run"
    (src / "exploits").mkdir(parents=True)
    (src / "engagement.json").write_text(
        json.dumps({"title": "An old run", "target": "Immutable", "created": "2026-11-19"})
    )
    (src / "exploits" / "EXP-001").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    report = registry.migrate_legacy("old-run", dry_run=True)
    assert report["moved"] and report["dry_run"]
    assert src.exists()  # dry run moved nothing
    report = registry.migrate_legacy("old-run")
    assert report["moved"] and not src.exists()
    folder = targets_root / "immutable" / "swarms" / "old-run"
    assert (folder / "exploits" / "EXP-001").is_dir()  # contents travelled
    d = json.loads((folder / "swarm.json").read_text())
    assert d["title"] == "An old run" and d["status"] == "stopped"
    assert d["migrated_from"].endswith("engagements/old-run")
    # the archive now shows it as a swarm, not legacy
    rows = {row["id"]: row for row in registry.list_swarms()}
    assert rows["old-run"]["status"] == "stopped"


def test_migrate_refuses_without_metadata_and_on_existing_destination(tmp_path, targets_root, monkeypatch):
    engagements = tmp_path / ".suijin" / "engagements"
    (engagements / "_default").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert registry.migrate_legacy("_default")["moved"] is False
    d = registry.create("Acme", "already here")
    (engagements / d["id"]).mkdir(parents=True)
    (engagements / d["id"] / "engagement.json").write_text(
        json.dumps({"target": "Acme"})
    )  # same slug → destination clash
    report = registry.migrate_legacy(d["id"])
    assert report["moved"] is False and "exists" in report["reason"]


def test_delete_removes_the_folder(targets_root):
    d = registry.create("Acme", "doomed")
    folder = targets_root / "acme" / "swarms" / d["id"]
    assert folder.exists()
    assert registry.delete(d["id"]) is True
    assert not folder.exists()
    assert registry.delete(d["id"]) is False


def test_slugs_and_ids_are_stable():
    assert registry.slugify("Immutable Passport SSO!") == "immutable-passport-sso"
    assert registry.new_swarm_id(1795851565.0).startswith("swarm-2026-")
