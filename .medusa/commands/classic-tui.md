---
description: Launch the classic Medusa Rich TUI inside this shell
agent: medusa-red
---
Launch the classic Medusa terminal interface for the user.

Use your **bash** tool (NOT the medusa MCP tools) to run this in the project
root, because the classic interface needs a real terminal:

```
python3 medusa/main.py
```

Stream its output back to the user. When the user exits the classic
interface, give a one-line summary of what they did and offer to continue
the same engagement with your medusa_* tools.
