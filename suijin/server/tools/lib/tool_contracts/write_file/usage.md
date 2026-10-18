Writes a file (full overwrite) inside the engagement workspace.

**Usage rules:**
- Paths are RELATIVE to the engagement home — `tmp/results.txt` not the full path
- If you wrote a file via terminal with an absolute path, read_file will strip the home prefix automatically
- For large outputs, write to a file then `read_file` specific sections instead of dumping to stdout
