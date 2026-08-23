#!/usr/bin/env python3
"""
Suijin Scope TUI (curses) — Burp-style target scope editor.
=============================================================
Edits suijin/policy.json interactively:
  - include list (allowed targets: IPs, CIDRs, hostnames)
  - exclude list (always wins over include)
  - subdomain matching toggle (*.entry style)
  - allow-unresolvable-hosts toggle (offline labs)
  - enforcement on/off (no policy file = nothing enforced)

 move   a add include   x add exclude   d delete selected
s subdomains   u unresolvable   e enforcement   q save   esc cancel
"""

import curses
import json
import os

# The package-level policy (suijin/policy.json) — the SAME file governance.py
# enforces at dispatch. Resolved from this file's location so it works from
# the dev symlink; the old dirname(__file__) path made the scope TUI save
# to modules/console/lib/policy.json, a file nothing ever read.
_POLICY_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
POLICY_PATH = os.path.join(_POLICY_DIR, "policy.json")

INCLUDE_KEYS = ("allowed_target_scopes",)
EXCLUDE_KEYS = ("excluded_scopes",)


def _load():
    if not os.path.exists(POLICY_PATH):
        return None
    try:
        with open(POLICY_PATH) as f:
            data = json.loads(f.read())
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def _save(policy):
    with open(POLICY_PATH, "w") as f:
        json.dump(policy, f, indent=2)


def _seed():
    """Enforcement on with private ranges only — the safe default."""
    return {
        "description": "Suijin engagement policy (edited via suijin scope)",
        "allowed_target_scopes": ["127.0.0.1", "localhost", "::1", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        "excluded_scopes": [],
        "allow_subdomains": True,
        "allow_unresolvable": False,
    }


def run(stdscr):
    curses.curs_set(0)
    stdscr.keypad(1)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)  # selection
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # headers
    curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)  # on
    curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)  # off/danger

    on_disk = _load()
    policy = json.loads(json.dumps(on_disk)) if on_disk else _seed()
    enforcing = on_disk is not None
    row = 0
    status = ""

    def entries():
        """Flat list of (list_key, value) rows for the two lists."""
        out = [("include", v) for v in policy.get("allowed_target_scopes", [])]
        out += [("exclude", v) for v in policy.get("excluded_scopes", [])]
        return out

    def draw():
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(1, 2, " SUIJIN — Target Scope ")
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)

        mode = "ENFORCING" if enforcing else "NOT ENFORCED (no policy file — everything allowed)"
        color = 3 if enforcing else 4
        stdscr.addstr(2, 2, " enforcement: ", curses.A_DIM)
        stdscr.addstr(f"{mode}", curses.color_pair(color) | curses.A_BOLD)

        sub = (
            "ON  (*.scope entries match subdomains)"
            if policy.get("allow_subdomains", True)
            else "OFF (exact hostnames only)"
        )
        stdscr.addstr(3, 2, " subdomains:  ", curses.A_DIM)
        stdErr = (
            "ON  (unresolvable hosts pass)" if policy.get("allow_unresolvable") else "OFF (unresolvable hosts blocked)"
        )
        stdscr.addstr(f"{sub}", curses.color_pair(3 if policy.get("allow_subdomains", True) else 4))
        stdscr.addstr(4, 2, " unresolvable:", curses.A_DIM)
        stdscr.addstr(f"{stdErr}", curses.color_pair(4 if policy.get("allow_unresolvable") else 3))

        y = 6
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(y, 2, " INCLUDE (allowed targets)")
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        y += 1
        for r, (key, val) in enumerate(entries()):
            if key == "exclude" and r == 0:
                y += 1
                stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                stdscr.addstr(y, 2, " EXCLUDE (always wins over include)")
                stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
                y += 1
            mark = "+ " if key == "include" else "- "
            color = 3 if key == "include" else 4
            line = f"  {mark}{val}"
            if r == row:
                stdscr.attron(curses.color_pair(1) | curses.A_REVERSE)
                stdscr.addstr(y, 2, line[: w - 4].ljust(w - 4))
                stdscr.attroff(curses.color_pair(1) | curses.A_REVERSE)
            else:
                stdscr.addstr(y, 2, line[: w - 4], curses.color_pair(color))
            y += 1
        if not entries():
            stdscr.addstr(y, 2, "  (empty — add with 'a')", curses.A_DIM)

        stdscr.addstr(
            h - 3,
            2,
            " select   a include   x exclude   d delete   s subdomains   u unresolvable   e enforce   q save   esc cancel",
            curses.A_DIM,
        )
        if status:
            stdscr.addstr(h - 2, 2, status[: w - 4], curses.A_BOLD)
        stdscr.refresh()

    def edit_value(prompt, initial=""):
        stdscr.clear()
        stdscr.addstr(3, 2, prompt)
        stdscr.addstr(4, 2, "(Enter to confirm, esc to cancel)")
        curses.echo()
        curses.curs_set(1)
        stdscr.move(6, 2)
        buf = stdscr.getstr(6, 2, 120).decode("utf-8").strip()
        curses.noecho()
        curses.curs_set(0)
        return buf

    while True:
        draw()
        key = stdscr.getch()
        total = len(entries())
        if key == curses.KEY_UP and row > 0:
            row -= 1
        elif key == curses.KEY_DOWN and row < max(total - 1, 0):
            row += 1
        elif key in (ord("a"), ord("A")):
            val = edit_value("Add to INCLUDE (IP, CIDR like 10.0.0.0/8, or hostname like lab.internal):")
            if val:
                policy.setdefault("allowed_target_scopes", []).append(val)
                status = f"include += {val}"
        elif key in (ord("x"), ord("X")):
            val = edit_value("Add to EXCLUDE (wins over include):")
            if val:
                policy.setdefault("excluded_scopes", []).append(val)
                status = f"exclude += {val}"
        elif key in (ord("d"), ord("D")):
            if total:
                key_name, val = entries()[row]
                lst = policy["allowed_target_scopes"] if key_name == "include" else policy["excluded_scopes"]
                if val in lst:
                    lst.remove(val)
                status = f"removed {val}"
                row = max(0, min(row, len(entries()) - 1))
        elif key in (ord("s"), ord("S")):
            policy["allow_subdomains"] = not policy.get("allow_subdomains", True)
            status = f"subdomains {'ON' if policy['allow_subdomains'] else 'OFF'}"
        elif key in (ord("u"), ord("U")):
            policy["allow_unresolvable"] = not policy.get("allow_unresolvable", False)
            status = f"allow_unresolvable {'ON' if policy['allow_unresolvable'] else 'OFF'}"
        elif key in (ord("e"), ord("E")):
            enforcing = not enforcing
            status = "enforcement ON (saved on q)" if enforcing else "enforcement OFF (file deleted on q)"
        elif key in (ord("q"), ord("Q")):
            if enforcing:
                _save(policy)
            else:
                # turning enforcement off deletes the file (no file = no policy)
                if os.path.exists(POLICY_PATH):
                    os.unlink(POLICY_PATH)
            return 0
        elif key == 27:  # esc
            return 0


def main():
    curses.wrapper(run)


if __name__ == "__main__":
    main()
