"""BootBanner — example kernel plugin (.sjp).

Implements the Module protocol (register/start/stop): prints one dim
line at boot. Reference shape for .sjp authors.
"""


class BootBannerModule:
    """Structural duck-type of the kernel Module protocol (id, tier,
    register/start/stop) — no kernel import needed in example code."""

    id = "bootbanner"
    tier = "recommended"

    def register(self, ctx) -> None:
        self._ctx = ctx

    def start(self, ctx) -> None:
        import contextlib

        with contextlib.suppress(Exception):  # a banner must never break boot
            ctx.console.print("[dim]bootbanner plugin: example .sjp online[/dim]")

    def stop(self, ctx) -> None:
        pass


MODULE = BootBannerModule()
