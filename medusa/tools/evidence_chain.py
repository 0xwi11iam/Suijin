"""Evidence chain — links tool output → diff → KG record → note for every finding."""
import json, os, hashlib
from datetime import datetime, timezone
from pathlib import Path
CHAIN_DIR = Path(__file__).resolve().parent.parent / "medusa_agent" / "evidence_chains"
CHAIN_DIR.mkdir(parents=True, exist_ok=True)

def create_evidence_chain(finding_id: str, tool_output: str, diff_result: str = "", kg_record: str = "", note: str = "") -> str:
    chain = {"finding_id": finding_id, "created_at": datetime.now(timezone.utc).isoformat(),
             "evidence": {"tool_output_hash": hashlib.sha256(tool_output.encode()).hexdigest()[:16],
                          "tool_output_snippet": tool_output[:500],
                          "diff_verified": bool(diff_result), "diff_snippet": diff_result[:300],
                          "kg_recorded": bool(kg_record), "note_recorded": bool(note)},
             "completeness": sum([bool(tool_output), bool(diff_result), bool(kg_record), bool(note)]) / 4}
    path = CHAIN_DIR / f"{finding_id}.json"
    path.write_text(json.dumps(chain, indent=2))
    return str(path)

def verify_chain_complete(finding_id: str) -> dict:
    path = CHAIN_DIR / f"{finding_id}.json"
    if not path.exists(): return {"complete": False, "missing": ["tool_output","diff","kg","note"]}
    chain = json.loads(path.read_text())
    e = chain["evidence"]
    missing = []
    if not e["tool_output_snippet"]: missing.append("tool_output")
    if not e["diff_verified"]: missing.append("diff_verification")
    if not e["kg_recorded"]: missing.append("kg_record")
    if not e["note_recorded"]: missing.append("note")
    return {"complete": len(missing) == 0, "missing": missing, "completeness": chain["completeness"]}
