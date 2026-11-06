# suijin sandbox base image — the TEMPLATE for agent boxes.
#
# Build:  suijin box image build   (or: docker build -f suijin/server/box/sandbox.Dockerfile .)
# Slow ONCE (cached); every sandbox spawn just starts a container from
# this image (sub-second). NEVER build on the engagement path.
#
# Keep this lean: the toolchain, not the configuration. Config,
# credentials, sessions, engagement data are MOUNTED at spawn time —
# settings changes never trigger rebuilds (the plan, 2026-11-06).
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/root

# core toolchain — enough for every HTTP/DNS/JS-probing lane; heavier
# tooling ships in the -full tag later
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        ca-certificates curl git jq \
        dnsutils iputils-ping net-tools \
        sqlite3 libsqlite3-0 \
        unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

# suijin itself (the whole tree — agents run the real tool dispatch)
WORKDIR /app
COPY . /app
RUN pip3 install --break-system-packages --no-cache-dir .

# agents announce themselves on the bus; the coordinator address is
# injected per spawn (host.docker.internal:<port> — containers are not
# localhost, see the plan's networking section)
ENTRYPOINT ["python3", "-u", "-m", "suijin.server.swarm.agent"]
