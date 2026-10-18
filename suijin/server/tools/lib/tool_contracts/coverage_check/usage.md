Checks and updates the coverage ledger — which vuln classes have been tested on which assets.

**Usage rules:**
- Call with no args for a summary of tested/untested
- The COMPLETION GATE reads this: untested high-priority cells block completion
- Mark cells as tested_vulnerable / tested_not_vulnerable (with evidence) / not_applicable (with reason)
- "Tested 3 endpoints and declared victory" is the failure mode this prevents
