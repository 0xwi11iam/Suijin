# Suijin Security — Kali-based autonomous red/blue teaming agent
#
# MINIMAL FOOTPRINT policy:
#   - base image + the kali-linux-core metapackage ONLY (never the full
#     kali-linux meta — it is gigabytes)
#   - our individual tool list is curated; the heavyweights are behind
#     build args (off by default)
#   - Build:  docker build -t suijin .
#             docker build --build-arg WITH_METASPLOIT=1 .   # add msf (~700MB)
#
# Run:       ./docker.sh run          (see docker.sh / install.ps1)

FROM kalilinux/kali-rolling:latest

# Version is injected at build time (CI passes the git tag; local builds say dev)
ARG VERSION=dev

LABEL org.opencontainers.image.title="Suijin" \
      org.opencontainers.image.description="Autonomous red & blue teaming agent (kernel + modules)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/0xwi11iam/Suijin"

# Heavy extras are opt-in at build time (minimum footprint by default)
ARG WITH_METASPLOIT=0

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

# ── kali-linux-core ONLY (core utilities; NOT the kali-linux meta) + our
#    curated, individually-small toolset ─────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    kali-linux-core \
    python3 python3-pip python3-venv python3-dev \
    nmap masscan \
    gobuster ffuf \
    sqlmap nikto sslscan whatweb \
    subfinder \
    nuclei \
    dnsutils whois netcat-openbsd socat curl wget git \
    snmp redis-tools \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── optional heavyweight (default OFF — minimum footprint) ─────────────
RUN if [ "$WITH_METASPLOIT" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends metasploit-framework \
        && apt-get clean && rm -rf /var/lib/apt/lists/*; \
    fi

# ── python extras the binary-wrapped packs use (small, pure installs) ──
RUN pip3 install --no-cache-dir impacket dnsrecon wafw00f dirsearch

# ── application code + python deps ─────────────────────────────────────
WORKDIR /app
COPY suijin/requirements.txt /app/suijin-requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/suijin-requirements.txt
COPY . /app/

# ── workspace volume mount point ────────────────────────────────────────
# Canonical workspace: /app/suijin_agent (volume), symlinked from
# /app/suijin/suijin_agent for legacy path references. Everything the
# engagement produces (outputs/, caches/, configs) lives here and
# survives container recreation.
RUN mkdir -p /app/suijin_agent/outputs /app/suijin_agent/scripts \
    && ln -sfn ../suijin_agent /app/suijin/suijin_agent

VOLUME ["/app/suijin_agent"]

# ── health: the agent's own environment check ──────────────────────────
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s --retries=2 \
    CMD python3 /app/suijin/modules/console/lib/cli.py doctor || exit 1

# ── entrypoint ─────────────────────────────────────────────────────────
WORKDIR /app/suijin
ENTRYPOINT ["python3", "main.py"]
