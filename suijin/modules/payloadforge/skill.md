# payloadforge

Real payload generation for authorized testing. Every tool produces
runnable output:

- rev_shell: context-aware reverse shell one-liners (bash, python,
  nc, php, powershell) — the exact command to paste into the target
- encode_chain: layered encoding (base64+gzip+hex) for filter bypass
- stager: download-and-execute commands (curl/wget/python fetch)

Pair with the wordlistio/genkit tools for custom payload sets.
