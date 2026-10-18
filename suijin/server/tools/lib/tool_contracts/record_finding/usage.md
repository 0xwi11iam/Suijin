Records a finding to the knowledge graph. Call after EVERY confirmed result — this is how institutional knowledge builds.

**Usage rules:**
- The `evidence` must be actual output (response body, command result), not a description
- Findings are keyed by target — cross-target credentials are auto-rejected
- Call this even for dead ends: "WAF blocks XSS on /search" is valuable knowledge
