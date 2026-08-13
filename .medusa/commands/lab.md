---
description: Start the vulnerable lab on port 5906
---
Start the Medusa vulnerable lab so recon and exploitation tools have a
target. Use your bash tool to run in the project root:

```
python3 medusa/lab/blue_target/vulnerable_app.py
```

Run it in the background, then confirm it is listening on port 5906 and
report the health check endpoint to the user.
