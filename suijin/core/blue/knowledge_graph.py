"""
suijin/core/blue/knowledge_graph.py — Session-scoped shared intelligence.

A lightweight in-memory graph that all subagents and the main AI share.
Persists to blue_kg.json during the session, wiped on new session.
Nodes: attackers, endpoints, attacks, defenses, deceptions
Edges: attacked, defended_by, deceived_by, related_to
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from suijin.core.constants import BLUE_KG_PATH

KG_PATH = BLUE_KG_PATH


@dataclass
class KGNode:
    node_id: str
    node_type: str  # attacker, endpoint, attack, defense, deception, intelligence
    data: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class BlueKnowledgeGraph:
    """In-memory knowledge graph shared across all blue team agents."""

    def __init__(self):
        self.nodes: dict[str, KGNode] = {}
        self.edges: list[tuple[str, str, str]] = []  # (from_id, to_id, relation)
        self._lock = threading.Lock()

    def add_attacker(self, ip: str, **details) -> str:
        nid = hashlib.md5(f"attacker:{ip}".encode()).hexdigest()[:12]
        with self._lock:
            if nid not in self.nodes:
                self.nodes[nid] = KGNode(nid, "attacker", {"ip": ip, "first_seen": time.time(), "flags": 1, **details})
            else:
                self.nodes[nid].data["flags"] = self.nodes[nid].data.get("flags", 1) + 1
                self.nodes[nid].updated_at = time.time()
        return nid

    def add_attack(self, ip: str, path: str, attack_type: str, score: int, payload: str = "") -> str:
        attacker_id = self.add_attacker(ip)
        nid = hashlib.md5(f"attack:{ip}:{path}:{attack_type}:{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            self.nodes[nid] = KGNode(nid, "attack", {
                "ip": ip, "path": path, "attack_type": attack_type,
                "score": score, "payload": payload, "time": time.time(),
            })
            self.edges.append((attacker_id, nid, "launched"))
        return nid

    def add_defense(self, ip: str, defense_type: str, detail: str = "") -> str:
        nid = hashlib.md5(f"defense:{ip}:{defense_type}:{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            self.nodes[nid] = KGNode(nid, "defense", {
                "ip": ip, "type": defense_type, "detail": detail, "time": time.time(),
            })
            # Link to attacker if known
            attacker_id = hashlib.md5(f"attacker:{ip}".encode()).hexdigest()[:12]
            if attacker_id in self.nodes:
                self.edges.append((nid, attacker_id, "defended_against"))
        return nid

    def add_intelligence(self, source: str, content: str) -> str:
        nid = hashlib.md5(f"intel:{source}:{content}:{time.time()}".encode()).hexdigest()[:12]
        with self._lock:
            self.nodes[nid] = KGNode(nid, "intelligence", {
                "source": source, "content": content, "time": time.time(),
            })
        return nid

    def get_attacker_history(self, ip: str) -> dict:
        """Get everything we know about an attacker."""
        attacker_id = hashlib.md5(f"attacker:{ip}".encode()).hexdigest()[:12]
        with self._lock:
            attacker = self.nodes.get(attacker_id)
            related_attacks = []
            related_defenses = []
            for src, dst, rel in self.edges:
                if src == attacker_id:
                    target = self.nodes.get(dst)
                    if target:
                        related_attacks.append(target.data)
                if dst == attacker_id and rel == "defended_against":
                    source = self.nodes.get(src)
                    if source:
                        related_defenses.append(source.data)
            return {
                "attacker": attacker.data if attacker else {},
                "attacks": related_attacks,
                "defenses": related_defenses,
                "total_flags": attacker.data.get("flags", 0) if attacker else 0,
            }

    def get_summary(self) -> dict:
        with self._lock:
            attackers = [n for n in self.nodes.values() if n.node_type == "attacker"]
            attacks = [n for n in self.nodes.values() if n.node_type == "attack"]
            defenses = [n for n in self.nodes.values() if n.node_type == "defense"]
            return {
                "total_attackers": len(attackers),
                "total_attacks": len(attacks),
                "total_defenses": len(defenses),
                "top_attackers": sorted(
                    [{"ip": a.data["ip"], "flags": a.data.get("flags", 0)}
                     for a in attackers],
                    key=lambda x: x["flags"], reverse=True,
                )[:5],
                "recent_attacks": sorted(
                    [a.data for a in attacks], key=lambda x: x.get("time", 0), reverse=True
                )[:10],
            }

    def save(self):
        with self._lock:
            data = {
                "nodes": {k: {"id": v.node_id, "type": v.node_type, "data": v.data,
                              "created": v.created_at, "updated": v.updated_at}
                          for k, v in self.nodes.items()},
                "edges": self.edges,
            }
            KG_PATH.write_text(json.dumps(data, indent=2, default=str))

    def load(self):
        if not KG_PATH.exists():
            return
        try:
            data = json.loads(KG_PATH.read_text())
            with self._lock:
                for k, v in data.get("nodes", {}).items():
                    self.nodes[k] = KGNode(v["id"], v["type"], v["data"],
                                           v.get("created", time.time()),
                                           v.get("updated", time.time()))
                self.edges = data.get("edges", [])
        except Exception:
            pass

    def clear(self):
        with self._lock:
            self.nodes.clear()
            self.edges.clear()
        if KG_PATH.exists():
            KG_PATH.unlink()

    def bridge_from_red_team(self):
        """Import intelligence from the red team knowledge graph.

        Reads suijin/intel/knowledge_graph.json and imports WAF rules,
        blocked patterns, confirmed CVEs, and discovered endpoints into
        the blue team's intelligence nodes for defensive use.
        """
        red_kg_path = Path(__file__).resolve().parent.parent.parent / "intel" / "knowledge_graph.json"
        if not red_kg_path.exists():
            return 0
        try:
            red_data = json.loads(red_kg_path.read_text())
            imported = 0
            # Import findings that are relevant for defense
            for _target, findings in red_data.items() if isinstance(red_data, dict) else []:
                if isinstance(findings, list):
                    for finding in findings:
                        if isinstance(finding, dict):
                            cve = finding.get("cve") or finding.get("vulnerability")
                            if cve:
                                self.add_intelligence("red_team", f"CVE: {cve} — {finding.get('description', '')}")
                                imported += 1
            return imported
        except Exception:
            return 0


# Global singleton
_global_kg: Optional[BlueKnowledgeGraph] = None

def get_kg() -> BlueKnowledgeGraph:
    global _global_kg
    if _global_kg is None:
        _global_kg = BlueKnowledgeGraph()
        _global_kg.load()
    return _global_kg

def reset_kg():
    global _global_kg
    if _global_kg:
        _global_kg.clear()
    _global_kg = BlueKnowledgeGraph()
