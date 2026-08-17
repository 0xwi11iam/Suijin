"""Goal decomposition — break complex objectives into sub-goals with tracking."""

from __future__ import annotations

import json

from medusa.tools.workspace import WORKSPACE_DIR

GOAL_DIR = WORKSPACE_DIR / "goals"
GOAL_DIR.mkdir(parents=True, exist_ok=True)


def decompose_objective(objective: str) -> list:
    """Heuristic goal decomposition based on keywords."""
    goals = [
        {"id": "G1", "goal": "Reconnaissance — map ports, services, endpoints", "status": "pending", "priority": "high"}
    ]
    lowered = objective.lower()
    if any(k in lowered for k in ("flag", "capture", "find flag")):
        goals.append({"id": "G2", "goal": "Capture all flags", "status": "pending", "priority": "critical"})
    if any(k in lowered for k in ("sql", "sqli", "injection")):
        goals.append(
            {"id": "G3", "goal": "Test for SQL injection on all inputs", "status": "pending", "priority": "high"}
        )
    if any(k in lowered for k in ("xss", "cross-site")):
        goals.append(
            {"id": "G4", "goal": "Test for XSS on all reflected parameters", "status": "pending", "priority": "high"}
        )
    if any(k in lowered for k in ("admin", "privilege", "escalat", "root")):
        goals.append(
            {
                "id": "G5",
                "goal": "Escalate privileges — gain admin/root access",
                "status": "pending",
                "priority": "critical",
            }
        )
    if any(k in lowered for k in ("api", "graphql", "rest")):
        goals.append({"id": "G6", "goal": "Map and test API endpoints", "status": "pending", "priority": "high"})
    if any(k in lowered for k in ("jwt", "token", "auth")):
        goals.append(
            {
                "id": "G7",
                "goal": "Test authentication mechanisms and JWT attacks",
                "status": "pending",
                "priority": "high",
            }
        )
    if any(k in lowered for k in ("report", "document")):
        goals.append(
            {"id": "G8", "goal": "Generate comprehensive engagement report", "status": "pending", "priority": "medium"}
        )
    path = GOAL_DIR / "current_goals.json"
    path.write_text(json.dumps(goals, indent=2))
    return goals


def mark_goal_complete(goal_id: str, evidence: str = "") -> dict:
    path = GOAL_DIR / "current_goals.json"
    if not path.exists():
        return {"error": "No goals file"}
    goals = json.loads(path.read_text())
    for g in goals:
        if g["id"] == goal_id:
            g["status"] = "completed"
            g["evidence"] = evidence[:200]
    path.write_text(json.dumps(goals, indent=2))
    return {"updated": goal_id, "remaining": sum(1 for g in goals if g["status"] != "completed")}


def get_goal_progress() -> dict:
    path = GOAL_DIR / "current_goals.json"
    if not path.exists():
        return {"total": 0, "completed": 0}
    goals = json.loads(path.read_text())
    return {"total": len(goals), "completed": sum(1 for g in goals if g["status"] == "completed"), "goals": goals}
