"""
Attack Tree Visualizer — generates Mermaid flowcharts from execution traces.
"""
from __future__ import annotations
import json


def build_attack_tree(trace: list) -> str:
    """Generate a Mermaid flowchart from execution trace.

    Nodes are tool actions. Edges connect successive actions.
    SUCCESS edges are solid, FAILED edges are dashed.
    """
    if not trace:
        return "graph TD\n    start[No actions recorded]"

    lines = ["graph TD"]
    node_ids = {}
    last_success_node = None

    for i, step in enumerate(trace):
        tn = step.get("tool_name", "unknown")
        success = step.get("success", True)
        thought = step.get("thought", "")[:40].replace('"', "'")
        node_id = f"step{i}"
        status = "OK" if success else "FAIL"
        label = f"{tn}[{status}]"
        if thought:
            label = f"{tn}\\n{thought}"

        if success:
            lines.append(f"    {node_id}[\"{label}\"]")
        else:
            lines.append(f"    {node_id}{{\"{label}\"}}")

        node_ids[i] = node_id

        # Link to previous step
        if i > 0:
            prev_id = node_ids[i-1]
            style = "-->|success|" if success else "-.->|failed|"
            lines.append(f"    {prev_id} {style} {node_id}")

        # Highlight flag/completion steps
        if step.get("completion_reason") or "flag" in str(step.get("tool_args", {})).lower():
            lines.append(f"    style {node_id} fill:#238636,stroke:#3fb950,color:#fff")

    return "\n".join(lines)


def build_attack_chain_summary(trace: list) -> str:
    """Generate a text-based attack chain summary for reports."""
    lines = ["## Attack Chain Summary", ""]
    chain_num = 0
    current_steps = []

    for step in trace:
        tn = step.get("tool_name", "?")
        success = step.get("success", True)
        current_steps.append((tn, success))

        if step.get("completion_reason"):
            chain_num += 1
            lines.append(f"### Chain {chain_num}")
            for j, (name, ok) in enumerate(current_steps, 1):
                arrow = "->" if ok else "-/>"
                lines.append(f"{j}. {arrow} {name}")
            lines.append(f"**Result**: {step.get('completion_reason', 'completed')}")
            lines.append("")
            current_steps = []

    # Remaining steps without completion
    if current_steps:
        chain_num += 1
        lines.append(f"### Chain {chain_num} (incomplete)")
        for j, (name, ok) in enumerate(current_steps, 1):
            arrow = "->" if ok else "-/>"
            lines.append(f"{j}. {arrow} {name}")
        lines.append("")

    return "\n".join(lines)


def generate_finding_diagram(findings: list) -> str:
    """Generate a Mermaid diagram for findings grouped by type."""
    lines = ["graph LR"]
    finding_types = {}
    for f in findings:
        ftype = f.get("type", "unknown")
        if ftype not in finding_types:
            finding_types[ftype] = []
        finding_types[ftype].append(f)

    prev_type = None
    for ftype, items in finding_types.items():
        type_id = ftype.replace(" ", "_").replace("-", "_")
        lines.append(f"    {type_id}[{ftype} ({len(items)} findings)]")
        if prev_type:
            lines.append(f"    {prev_type} --> {type_id}")
        prev_type = type_id

    lines.append(f"    target[Target] --> {list(finding_types.keys())[0].replace(' ', '_').replace('-', '_')}")
    return "\n".join(lines)
