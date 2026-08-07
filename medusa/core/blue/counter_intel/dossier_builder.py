"""Dossier builder — persistent attacker profiles across sessions."""
import json, time
from pathlib import Path

DOSSIER_DIR = Path(__file__).resolve().parent.parent.parent.parent / "medusa_agent" / "dossiers"
DOSSIER_DIR.mkdir(parents=True, exist_ok=True)

def build_dossier(attacker_id: str, data: dict):
    dossier = {"id": attacker_id, "updated": time.time(), "history": [], **data}
    (DOSSIER_DIR / f"{attacker_id}.json").write_text(json.dumps(dossier, indent=2))

def load_dossier(attacker_id: str) -> dict:
    path = DOSSIER_DIR / f"{attacker_id}.json"
    return json.loads(path.read_text()) if path.exists() else {}
