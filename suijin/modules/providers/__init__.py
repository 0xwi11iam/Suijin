"""providers — the LLM abstraction module (recommended tier).

Registers generate() and the failover chain as Context services. The
platform module's temporary 'llm' service (registered during migration)
is superseded here — later registration wins on the Context.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class ProvidersModule(Module):
    id = "providers"
    tier = Tier.RECOMMENDED

    def register(self, ctx) -> None:
        import suijin.tools.providers as p

        # the single, canonical LLM service — one module object, one
        # USAGE accumulator (the Phase 0 split-brain fix made this true)
        ctx.register_service("llm.generate", lambda: p.generate)
        ctx.register_service("llm.failover", lambda: p.generate_with_failover)
        ctx.register_service("llm.usage", lambda: p.get_usage)
        # supersede platform's migration-era 'llm' with the same canonical fn
        ctx.register_service("llm", lambda: p.generate)

    def start(self, ctx) -> None:
        ctx.journal.append("providers", "LLM services registered")

    def stop(self, ctx) -> None:
        pass
