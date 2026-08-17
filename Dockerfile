# Medusa Security — Kali-based autonomous red teaming agent
# Build: docker build -t medusa .
# Run:   docker run -it --rm -v $(pwd)/medusa_agent:/app/medusa_agent -e DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY medusa

FROM kalilinux/kali-rolling:latest

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# ── System deps + pentest tools (apt) ──────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    nmap masscan \
    gobuster ffuf feroxbuster \
    sqlmap hydra john \
    nikto sslscan \
    amass subfinder \
    nuclei \
    whatweb \
    mitmproxy \
    trufflehog \
    metasploit-framework \
    curl wget git golang \
    dirb dirbuster \
    dnsutils whois \
    netcat-openbsd socat \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Go-based tools (httpx, katana — not in apt) ─────────────────────
RUN go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest && \
    go install -v github.com/projectdiscovery/katana/cmd/katana@latest
ENV PATH="/root/go/bin:$PATH"

# ── Python environment ───────────────────────────────────────────────
WORKDIR /app
COPY medusa/requirements.txt /app/medusa-requirements.txt
RUN python3 -m venv /opt/medusa-venv
ENV PATH="/opt/medusa-venv/bin:$PATH"
RUN pip install --no-cache-dir -r /app/medusa-requirements.txt && \
    pip install --no-cache-dir requests urllib3 rich duckduckgo-search

# ── Application code ─────────────────────────────────────────────────
COPY . /app/

# ── Agent workspace ──────────────────────────────────────────────────
# Canonical workspace: /app/medusa_agent (volume-mount point), with
# /app/medusa/medusa_agent symlinked to it for legacy path references.
RUN mkdir -p /app/medusa_agent/outputs /app/medusa_agent/payloads /app/medusa_agent/scripts \
    && ln -sfn ../medusa_agent /app/medusa/medusa_agent

# ── Entrypoint ───────────────────────────────────────────────────────
WORKDIR /app/medusa
ENTRYPOINT ["python3", "main.py"]
