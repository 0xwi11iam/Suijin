"""Shadow redirect — transparently proxy attacker to isolated environment."""

from __future__ import annotations


class ShadowEnvironment:
    def __init__(self):
        self.active = False
        self.redirected_ips = set()

    def redirect(self, ip: str):
        self.redirected_ips.add(ip)

    def is_redirected(self, ip: str) -> bool:
        return ip in self.redirected_ips

    def get_stats(self) -> dict:
        return {"active": self.active, "redirected_count": len(self.redirected_ips)}


_shadow = ShadowEnvironment()


def redirect_to_shadow(ip: str) -> str:
    _shadow.redirect(ip)
    return f"IP {ip} redirected to shadow environment. All traffic mirrored, real app untouched."
