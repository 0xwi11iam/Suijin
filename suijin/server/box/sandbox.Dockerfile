# suijin sandbox base image — KALI, the full toolchain.
#
# Build:  suijin box image build
# Slow ONCE (cached); every sandbox spawn just starts a container from
# this image (sub-second). NEVER build on the engagement path.
#
# Matches the box provisioner's toolset (tool_manifest, kali plans) so
# agents have the same tools inside a sandbox as inside the box.
FROM kalilinux/kali-rolling

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/root \
    GOBIN=/usr/local/bin

# ── the toolchain (mirrors the box's kali install plans) ────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        ca-certificates curl wget git jq unzip xz-utils sudo \
        dnsutils iputils-ping net-tools \
        sqlite3 libsqlite3-0 \
        nmap sqlmap nuclei httpx subfinder katana ffuf \
        whatweb nikto masscan dirb gobuster \
        whois theHarvester dnsrecon \
        exploitdb metasploit-framework \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

# go tools not in kali's apt (kept current enough)
RUN curl -sL https://go.dev/dl/go1.23.4.linux-amd64.tar.gz | tar -C /usr/local -xz \
    && export PATH=$PATH:/usr/local/go/bin \
    && go install github.com/projectdiscovery/httpx/cmd/httpx@latest \
    && go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest \
    && go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest \
    && go install github.com/projectdiscovery/katana/cmd/katana@latest \
    && go install github.com/ffuf/ffuf/v2@ffuf@latest \
    && rm -rf /root/go /usr/local/go

# ── suijin itself (the whole tree — agents run the real tool dispatch) ──
WORKDIR /app
COPY . /app
RUN pip3 install --break-system-packages --no-cache-dir .

# agents announce themselves on the bus; the coordinator address is
# injected per spawn (host.docker.internal:<port> — containers are not
# localhost, see the plan's networking section)
ENTRYPOINT ["python3", "-u", "-m", "suijin.server.swarm.agent"]
