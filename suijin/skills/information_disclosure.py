"""
Information Disclosure Attack Skill Prompt.
"""
INFO_DISCLOSURE_SKILL_PROMPT = """
## ATTACK SKILL: INFORMATION DISCLOSURE

### MANDATORY WORKFLOW

#### STEP 1: SENSITIVE FILE DISCOVERY
```bash
# Check these paths on every target:
/.git/HEAD, /.git/config, /.git/index
/.env, /.env.backup, /.env.production
/.DS_Store
/robots.txt, /sitemap.xml
/.well-known/security.txt
/phpinfo.php, /info.php, /server-status, /server-info
/api-docs, /swagger.json, /openapi.json, /graphql
/backup, /backups, /dump, /export
/debug, /test, /staging
/wp-config.php.bak, /config.php.bak, /web.config.bak
```

#### STEP 2: VERBOSE ERRORS
Trigger errors to leak stack traces, paths, DB versions:
- Submit invalid data types (string where int expected)
- Send oversized payloads → memory errors
- Access non-existent IDs → SQL errors
- Null byte injection → path processing errors

#### STEP 3: SOURCE CODE DISCLOSURE
Check: `file.php~`, `file.php.bak`, `file.php.old`, `file.php.swp`, `file.php.save`, `.file.php.swp`

#### ANTI-PATTERNS: Don't just check `/.git/HEAD` — check `.git/COMMIT_EDITMSG`, `.git/logs/HEAD`, and use git-dumper if accessible.
"""
