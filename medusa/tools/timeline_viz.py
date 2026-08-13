"""Timeline visualization — Mermaid Gantt chart of engagement phases."""
from __future__ import annotations
def generate_timeline_gantt(trace: list) -> str:
    lines = ["```mermaid", "gantt", "    title Engagement Timeline", "    dateFormat HH:mm", "    axisFormat %H:%M"]
    phase_start = None
    current_phase = None
    start_time = 0
    for i, step in enumerate(trace):
        phase = step.get("phase", "recon")
        tool = step.get("tool_name", "?")
        if phase != current_phase:
            if current_phase:
                lines.append(f"    {current_phase} :{start_time}, {i}")
            current_phase = phase
            start_time = i
    if current_phase:
        lines.append(f"    {current_phase} :{start_time}, {len(trace)}")
    # Add finding markers
    for i, step in enumerate(trace):
        if "flag{" in str(step.get("tool_output", "")).lower() or "flag{" in str(step.get("thought", "")).lower():
            lines.append(f"    FLAG :milestone, {i}, 0")
    lines.append("```")
    return "\n".join(lines)
