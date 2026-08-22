"""Output offloading — write large tool outputs to disk.

When a tool returns more than OFFLOAD_THRESHOLD characters, the output
is moved to a file so the LLM context window isn't flooded. The LLM
sees a summary + file path instead.
"""

from datetime import datetime, timezone

from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

from .tool_offload_policy import OFFLOAD_THRESHOLD, get_offload_mode


def maybe_offload(tool_name: str, output: str) -> tuple[str, bool]:
    """Offload large tool output to disk if policy says so.

    Returns (output_or_summary, was_offloaded).
    """
    mode = get_offload_mode(tool_name)

    if mode == "never":
        return output, False

    if mode == "auto" and len(output) <= OFFLOAD_THRESHOLD:
        return output, False

    # Offload
    outputs_dir = WORKSPACE_DIR / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{tool_name}_{ts}.txt"
    filepath = outputs_dir / filename

    filepath.write_text(output, encoding="utf-8", errors="ignore")

    preview = output[:500] + ("…" if len(output) > 500 else "")
    summary = f"[OUTPUT OFFLOADED: {len(output)} chars -> {filepath}]\nPreview:\n{preview}"
    return summary, True
