# Submission reports

The finding detail page has **Submission report** → Intigriti / HackerOne /
Bugcrowd / Other. Each choice writes one Markdown file to `~/Desktop` and
returns its path in the UI. "Other" is the combined sheet.

The report contains only what the platform needs: title, asset, endpoint,
severity/CVSS, platform selectors (CWE, VRT, asset type, CVSS version,
severity method), summary, prerequisites, root cause, numbered steps, raw
requests, impact, remediation, and evidence filenames. It never contains
Suijin internal ids, EXP ids, chat/engagement names, or audit paths.

Metadata lives in `swarm.db:finding_submission_meta` and is seeded when a
catalogued finding is recorded; old rows are derived on export. Common
classes map to a CWE/VRT primary plus a short selector list. Reports are
local deliverables and are not part of the runtime tree.
