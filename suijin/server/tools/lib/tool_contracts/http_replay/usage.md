HTTP request with payload-as-data semantics: mutations, credential swapping, and 3-gate compare mode.

**Compare mode** — pass `compare` as an OBJECT, not `true`:
```
{"url": "https://t/api/data", "compare": {"mutations": [{"op": "replace", "field": "id", "value": "2"}]}}
```
This sends a baseline + the mutated request and returns a 3-gate diff (status/size/content). A diff proves the mutation had an effect — same-same = NOT a vulnerability.

**Credential swapping** — the IDOR primitive:
```
{"url": "https://t/api/user/123", "credential": "user_a"}
{"url": "https://t/api/user/123", "credential": "user_b"}
```
Different responses for different credentials = access control finding.
