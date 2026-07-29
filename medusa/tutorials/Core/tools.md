# Advanced Pentesting & Analytical Tool Methodology

**⚠️ MANDATORY: Use these external CLI tools via `execute_terminal` for ALL scanning and brute-forcing. NEVER use raw `curl` loops or `http_request` when a dedicated tool exists. This is not optional — it dramatically improves speed, accuracy, and coverage. `http_request` is for manual payload testing only.**

This document outlines the standard operating procedures and terminal commands for advanced penetration testing, cloud auditing, and binary analysis tools. An autonomous agent can execute these via terminal commands to gather intelligence and audit systems.

## 1. High-Speed Network Reconnaissance
When assessing massive external subnets (e.g., /16 or /8 ranges), standard Nmap is too slow. High-speed scanners are used to identify open ports before deep-scanning.

**Masscan (Asynchronous Port Scanner)**
*   **Objective:** Rapidly scan a large IP range for a specific port or all ports.
*   **Command:** `masscan -p80,443 <target_network/CIDR> --rate=10000`
*   **Workflow:** Feed the output (list of IPs with open ports) into Nmap for deep service enumeration (`nmap -sV -sC`).

**Zmap (Single-Packet Scanner)**
*   **Objective:** Internet-scale network surveys.
*   **Command:** `zmap -p 443 <target_network> -o results.csv`

## 2. Advanced Web Content Discovery
When standard tools like `ffuf` are insufficient, recursive and highly parallelized tools are deployed.

**Feroxbuster (Recursive Fuzzing)**
*   **Objective:** Discover hidden files and directories recursively (if it finds a folder, it scans inside it automatically).
*   **Command:** `feroxbuster -u http://<target> -w /path/to/wordlist.txt -x php,html,txt,json -t 50 --silent`
*   **Workflow:** Use the discovered endpoints to map the attack surface for Nuclei or targeted SQLi/XSS validation.

**Wfuzz (Dynamic Payload Fuzzing)**
*   **Objective:** Specifically fuzz HTTP parameters, headers, or POST bodies for anomalies.
*   **Command:** `wfuzz -c -z file,/path/to/wordlist.txt --hc 404 http://<target>/page.php?id=FUZZ`

## 3. Cloud & IaC Auditing
Modern applications reside in the cloud. Assessing the IAM roles and Terraform scripts is critical.

**Terrascan (Static IaC Analysis)**
*   **Objective:** Detect security baseline violations in Terraform, Kubernetes, Helm, etc., before deployment.
*   **Command:** `terrascan scan -i terraform -d /path/to/terraform/repo`
*   **Workflow:** Analyze the JSON/text output to find hardcoded AWS keys, unencrypted S3 buckets, or over-permissive security groups.

**Cloudsplaining (AWS IAM Assessment)**
*   **Objective:** Identify violations of least privilege in AWS IAM policies.
*   **Command:** `cloudsplaining scan --policy-file policy.json`
*   **Workflow:** Review the HTML/JSON report to find policies that allow privilege escalation or data exfiltration.

## 4. Binary Analysis & Reverse Engineering
When encountering compiled executables, malware strings, or custom binaries, static and dynamic analysis is required.

**Radare2 (Static Binary Analysis)**
*   **Objective:** Disassemble and analyze binaries headless.
*   **Command:** `r2 -A -c "pdf @ main; q" /path/to/binary` (Analyzes the binary, prints the disassembly of the main function, and quits.)
*   **Workflow:** Look for insecure functions (`strcpy`, `gets`), hardcoded credentials, or interesting logic branches.

**Frida (Dynamic Instrumentation)**
*   **Objective:** Hook into a running process to intercept functions, modify arguments, or bypass client-side checks (like SSL pinning).
*   **Command:** `frida -U -f com.example.app -l script.js --no-pause`
*   **Workflow:** Use custom JavaScript (`script.js`) to hook APIs and dump cleartext buffers before they are encrypted.

## 5. API & Token Assessment
When an application uses APIs (GraphQL, REST) or JSON Web Tokens (JWT) for authentication.

**GraphQL-Cop (API Assessment)**
*   **Objective:** Assess GraphQL endpoints for common misconfigurations like Information Disclosure, Introspection left enabled, and excessive batching limits.
*   **Command:** `graphql-cop -t http://<target>/graphql`
*   **Workflow:** Evaluate if queries can bypass logic or fetch unauthenticated sensitive parameters.

**JWT Tool**
*   **Objective:** Audit, decode, and manipulate JSON Web Tokens for weak signatures.
*   **Command:** `python3 jwt_tool.py <TOKEN> -I -pc <WORDLIST>`
*   **Workflow:** Analyze if the JWT signing string is weak or vulnerable to the 'none' algorithm attack.

## 6. AI/ML Threat Auditing
Targeting the Machine Learning layers or Language Models themselves.

**Garak (LLM Vulnerability Scanner)**
*   **Objective:** Probe LLMs for prompt injection, jailbreaking, and hallucination pathways.
*   **Command:** `python3 -m garak --model_type openai --model_name gpt-3.5-turbo --probes injection`
*   **Workflow:** Identifies if an application's embedded AI chatbot can be tricked into executing arbitrary code or revealing its backend instructions.

**Adversarial Robustness Toolbox (ART)**
*   **Objective:** Write Python scripts using ART to test image/logic classification models against evasion (adversarial noise) attacks.
*   **Workflow:** Python-native scripting environment to craft robust defensive ML tests.