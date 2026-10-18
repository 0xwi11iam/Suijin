Waits for a background job to complete. Returns when the job finishes or the timeout hits.

**Usage rules:**
- Use after spawning a background command (execute_terminal with background=true)
- Check job_output after job_wait returns to read the result
- Don't wait on jobs that will obviously take longer than your timeout — use job_output to poll instead
