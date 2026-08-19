"""knowledge — the offline KB module (recommended tier)."""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class KnowledgeModule(Module):
    id = "knowledge"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        from suijin.modules.knowledge.lib import cve_mirror, kb

        ctx.register_service("kb.status", kb.kb_status)
        ctx.register_service("kb.compile", kb.compile_kb)
        ctx.register_service("kev.status", cve_mirror.kev_status)

    def start(self, ctx) -> None:
        from suijin.modules.knowledge.lib.kb import kb_status

        st = kb_status()
        if st:
            ctx.journal.append("knowledge", f"KB ready: {st['docs']} docs")
        else:
            ctx.journal.append("knowledge", "KB not built (pull kb to enable)")

    def stop(self, ctx) -> None:
        pass
