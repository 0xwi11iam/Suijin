Reads a file from the engagement workspace.

**Usage rules:**
- Paths resolve relative to the engagement home directory
- If a terminal command wrote to an absolute path inside the engagement, read_file auto-strips the duplicated home prefix
- For binary files, use execute_terminal with `file` or `xxd` instead
