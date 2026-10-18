Deploys a sub-agent for parallel work. This is an ACTION (not a tool_name).

**Usage rules:**
- This goes in the `action` field: `{"action": "deploy_subagent", "subagent_task": "..."}`
- NOT `{"action": "use_tool", "tool_name": "deploy_subagent"}`
- Separate parallel tasks with `||`: `"SQLi test on /login || XSS test on /search"`
- Sub-agents run with the same tools but a fresh context — give them SPECIFIC instructions
