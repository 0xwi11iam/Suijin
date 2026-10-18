Executes a shell command in the engagement workspace.

**Usage rules:**
- Your working directory PERSISTS between calls — no need to `cd` every time
- For file operations (read/write/search), prefer `read_file`/`write_file` instead of cat/echo
- For long-running commands (>30s), use `background: true` or spawn a job
- Output is capped at ~30KB; large output auto-offloads to toollogs/
- System-modifying commands (pip, brew, apt, sudo) require operator approval

**Common mistakes to avoid:**
- DON'T pass `auto_actions` or `thought` — those are decision fields, not tool args
- DON'T re-type `cd /path && cmd` if you already cd'd — the cwd persists
- DO quote paths with spaces: `cat "my file.txt"` not `cat my file.txt`
