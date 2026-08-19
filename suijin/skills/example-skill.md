<!-- A drop-in skill: pure markdown, no manifest, no code. Delete this file
     and drop your own .md files here — they boot into the agent's prompt. -->

## Engaging HTTP services safely

- Always fingerprint before attacking: whatweb or the `techfp` tools first.
- Check the knowledge graph (`check_knowledge`) before any payload — known
  WAF rules and blocked patterns live there.
- One class of payload per request round; log every result with `write_note`
  so the report writes itself.
