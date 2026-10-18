Reads the current output of a background job (without waiting for completion).

**Usage rules:**
- Safe to call multiple times — shows whatever output exists so far
- After job_wait returns, call this to read the full result
