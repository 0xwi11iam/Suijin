"""Threat feed consumer — Emerging Threats, OWASP rules."""
def load_owasp_rules() -> list:
    return [{"id":"OWASP-001","pattern":"' OR '1'='1","type":"sqli","severity":"critical"},
            {"id":"OWASP-002","pattern":"<script>alert","type":"xss","severity":"high"}]
