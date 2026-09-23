"""Suijin client region — rendering only.

The client renders what the server publishes (events) and sends control
ops back. It never owns engagement state; it can crash without the run
noticing. Currently: the Rich TUI (moved here whole from
modules/redteam/lib/red/ — the old import paths shim to these modules).

Design: docs/design/rebuild.md
"""
