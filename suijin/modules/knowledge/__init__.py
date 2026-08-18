"""knowledge — the offline KB module (recommended tier)."""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class KnowledgeModule(Module):
    id = "knowledge"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        ctx.register_service("kb.status", lambda: __import__("suijin.kb", fromlist=["kb_status"]).kb_status)
        ctx.register_service("kb.compile", lambda: __import__("suijin.kb", fromlist=["compile_kb"]).compile_kb)
        ctx.register_service(
            "kev.status", lambda: __import__("suijin.tools.cve_mirror", fromlist=["kev_status"]).kev_status
        )

    def start(self, ctx) -> None:
        st = ctx.service("kb.status")()
        if st:
            ctx.journal.append("knowledge", f"KB ready: {st['docs']} docs")
        else:
            ctx.journal.append("knowledge", "KB not built (pull kb to enable)")

    def stop(self, ctx) -> None:
        pass
