"""Medusa WebUI — local-first web dashboard for the operator.

`medusa ui` serves a React dashboard on 127.0.0.1 (bound to loopback only —
this is an operator console, not a public service). The Flask backend exposes
a read-mostly REST API over the same data the CLI commands surface, plus an
SSE stream (`/api/events`) that pushes refreshed state every few seconds.

No new Python dependencies: Flask + flask_cors are already core deps. The
built frontend lives in medusa/ui/dist/ (committed); sources in webui/.
"""
