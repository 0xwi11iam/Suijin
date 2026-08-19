"""
Subdomain Takeover Attack Skill Prompt.
"""

SUBDOMAIN_TAKEOVER_SKILL_PROMPT = """
## ATTACK SKILL: SUBDOMAIN TAKEOVER

### MANDATORY WORKFLOW

#### STEP 1: ENUMERATE SUBDOMAINS
```bash
subfinder -d TARGET
amass enum -passive -d TARGET
curl -s "https://crt.sh/?q=%.TARGET&output=json" | jq -r '.[].name_value' | sort -u
```

#### STEP 2: RESOLVE AND CHECK
For each subdomain, check DNS and HTTP response:
```bash
dig CNAME sub.TARGET +short  # dangling CNAME → takeover candidate
curl -sI https://sub.TARGET | head -20
```

#### STEP 3: FINGERPRINT CLOUD PROVIDER
NXDOMAIN + known provider CNAME → takeover. Check for:
- AWS S3: `s3.amazonaws.com` → bucket doesn't exist
- Azure: `cloudapp.net`, `azurewebsites.net`
- Heroku: `herokuapp.com`
- GitHub Pages: `github.io`
- Shopify: `myshopify.com`
- 80+ provider fingerprints in nuclei takeover templates

#### STEP 4: CLAIM
```bash
# AWS S3 takeover
aws s3 mb s3://EXACT-BUCKET-NAME --region EXPECTED-REGION
# GitHub Pages takeover
gh repo create USER/REPO --public
echo "TAKEOVER PROOF" > index.html
git push
```

#### ANTI-PATTERNS: Don't just check HTTP — resolve CNAME first. Don't ignore `NXDOMAIN` subdomains.
"""
