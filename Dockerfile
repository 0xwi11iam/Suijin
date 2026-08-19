# Suijin Security — Kali-based autonomous red/blue teaming agent
#
# Build:  docker build -t suijin .
# Run:    docker run -it --rm -v suijin_ws:/app/suijin_agent suijin
# Compose:docker compose run --rm suijin          (see docker-compose.yml)
#
# The workspace volume is THE state: engagement outputs, the knowledge
# base, caches and operator configs all live there — a rebuilt container
# picks up exactly where the last one left off.

FROM kalilinux/kali-rolling:latest

LABEL org.opencontainers.image.title="Suijin" \
      org.opencontainers.image.description="Autonomous red & blue teaming agent (kernel + modules)" \
      org.opencontainers.image.version="4.2.0" \
      org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

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
    snmp \
    redis-tools \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Python extras the pure-Python packs use (impacket, dnsrecon, ...) ──
RUN pip3 install --no-cache-dir impacket dnsrecon wafw00f dirsearch medusa

# ── Go-based tools (httpx, katana — not in apt) ────────────────────────
RUN go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest && \
    go install -v github.com/projectdiscovery/katana/cmd/katana@latest
ENV PATH="/root/go/bin:${PATH}"

# ── Application code + python deps ─────────────────────────────────────
WORKDIR /app
COPY suijin/requirements.txt /app/suijin-requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/suijin-requirements.txt
COPY . /app/

# ── Workspace volume mount point ────────────────────────────────────────
# Canonical workspace: /app/suijin_agent (volume), with /app/suijin/suijin_agent
# symlinked for legacy path references. Everything the engagement produces
# (outputs/, caches/, configs) lives here and survives container recreation.
RUN mkdir -p /app/suijin_agent/outputs /app/suijin_agent/scripts \
    && ln -sfn ../suijin_agent /app/suijin/suijin_agent

VOLUME ["/app/suijin_agent"]

# ── Health: the agent's own environment check ──────────────────────────
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s --retries=2 \
    CMD python3 /app/suijin/modules/console/lib/cli.py doctor || exit 1

# ── Entrypoint ─────────────────────────────────────────────────────────
WORKDIR /app/suijin
ENTRYPOINT ["python3", "main.py"]
