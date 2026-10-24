#!/usr/bin/env bash
#
# Suijin Kali setup — run INSIDE an existing Kali container/VM:
#
#   curl -fsSL https://raw.githubusercontent.com/0xwi11iam/Suijin/main/kali-setup.sh | bash
#
# Installs Suijin itself (+ optionally the full tool arsenal) and the
# docker CLI so BOXING works from inside:
#   source  -> /opt/suijin/repo
#   venv    -> /opt/suijin/venv
#   launcher-> /usr/local/bin/suijin (already on PATH)
#
# BOXING FROM INSIDE A CONTAINER: mount the host docker socket —
#   docker run -v /var/run/docker.sock:/var/run/docker.sock ...
# — and suijin box build creates the box as a SIBLING container on
# that daemon (no docker-in-docker needed).
#
# STOPS IMMEDIATELY if the host is not Kali.
# Not Kali?  native install.sh + `suijin box build` carries the tools.
#
# Env overrides: SUIJIN_REPO (default GitHub; may be a local checkout path)
#                SUIJIN_INSTALL_DIR (default /opt/suijin)
#                SUIJIN_SKIP_ARSENAL=1  skip the apt tool arsenal (boxing
#                carries the tools — right choice inside a container)
#
set -euo pipefail

# ── output helpers ─────────────────────────────────────────────────────
BOLD=""; CYAN=""; GREEN=""; YELLOW=""; RED=""; DIM=""; OFF=""
[ -t 1 ] && { BOLD="\033[1m"; CYAN="\033[36m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; DIM="\033[2m"; OFF="\033[0m"; }
STEP=0; TOTAL=5; T0=$(date +%s)
step() { STEP=$((STEP+1)); printf "\n${BOLD}${CYAN}[ %d/%d ] %s${OFF}\n" "$STEP" "$TOTAL" "$*"; _s=$(date +%s); }
ok()   { printf "  ${GREEN}ok${OFF}   %s ${DIM}(+%ds)${OFF}\n" "$*" "$(( $(date +%s) - _s ))"; }
warn() { printf "  ${YELLOW}warn${OFF} %s\n" "$*"; }
fail() { printf "  ${RED}fail${OFF} %s\n" "$*"; exit 1; }
note() { printf "      ${DIM}%s${OFF}\n" "$*"; }

# ── 0. KALI CHECK — before touching anything ───────────────────────────
OS_ID=""
[ -r /etc/os-release ] && OS_ID=$(grep '^ID=' /etc/os-release | cut -d= -f2)
if [ -z "$OS_ID" ]; then
  printf "  ${RED}fail${OFF} no /etc/os-release — aborting.\n"
  exit 1
fi
if [ "$OS_ID" != "kali" ]; then
  # the apt ARSENAL groups are kali-packaged; on ubuntu/debian only the
  # skip-arsenal flow (the box carries the tools) is supported
  if [ "$OS_ID" = "ubuntu" ] || [ "$OS_ID" = "debian" ]; then
    if [ "${SUIJIN_SKIP_ARSENAL:-0}" != "1" ]; then
      printf "  ${RED}fail${OFF} $OS_ID is supported only with SUIJIN_SKIP_ARSENAL=1 (the box carries the tools).\n"
      exit 1
    fi
    export SUIJIN_SKIP_ARSENAL=1
  else
    printf "  ${RED}fail${OFF} unsupported distro (ID=$OS_ID) — aborting.\n"
    printf '        native installs: use install.sh, then `suijin box build`\n'
    exit 1
  fi
fi

INSTALL_DIR="${SUIJIN_INSTALL_DIR:-/opt/suijin}"
REPO_URL="${SUIJIN_REPO:-https://github.com/0xwi11iam/Suijin.git}"
REPO_DIR="$INSTALL_DIR/repo"
VENV="$INSTALL_DIR/venv"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 && SUDO="sudo" || fail "not root and no sudo — re-run as root inside the container"
  warn "running as non-root; using sudo for system packages"
fi

# ── 0.5 HOST TRUST ANCHORS (filtered networks) ─────────────────────────
# TLS-intercepting networks (school/corp filters) break pip/https in
# containers: mount or copy the HOST's CA bundle in and point this at
# it — every later https call (pip, git, the BOX's own installs) works.
HOST_CA="${SUIJIN_HOST_CA:-/tmp/host-cas.pem}"
if [ -r "$HOST_CA" ] && grep -q "BEGIN CERT" "$HOST_CA" 2>/dev/null; then
  $SUDO cp "$HOST_CA" /usr/local/share/ca-certificates/suijin-host-cas.crt
  $SUDO update-ca-certificates >/dev/null 2>&1 || warn "update-ca-certificates failed"
  printf '#!/usr/bin/env bash\nexport PIP_CERT=/etc/ssl/certs/ca-certificates.crt\nexport REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt\nexport SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt\nexport GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt\n' | $SUDO tee /etc/profile.d/suijin-ca.sh >/dev/null
  export PIP_CERT=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
  note "host trust anchors installed ($HOST_CA)"
fi

# ── 1/5 system + tool arsenal (apt) ────────────────────────────────────
step "installing system packages + docker CLI (boxing needs it)"
$SUDO apt-get update -qq || warn "apt update failed — continuing with cached indexes"

APT_CORE=(python3 python3-pip python3-venv python3-dev build-essential git curl wget dnsutils whois netcat-openbsd socat)
try_group() {
  local label="$1"; shift
  note "$label: $*"
  if $SUDO apt-get install -y -qq "$@" >/dev/null 2>&1; then
    return 0
  fi
  # group failed -> per-package, tolerate misses
  local miss=()
  for p in "$@"; do
    $SUDO apt-get install -y -qq "$p" >/dev/null 2>&1 || miss+=("$p")
  done
  [ ${#miss[@]} -gt 0 ] && warn "unavailable: ${miss[*]} (continuing)"
}
try_group "core+build"   "${APT_CORE[@]}"
# docker CLI for BOXING from inside: the daemon comes from the mounted
# host socket (/var/run/docker.sock) — cli-only package first, full
# docker.io as fallback, absence tolerated (no socket = no boxing here)
if ! $SUDO apt-get install -y -qq docker-cli >/dev/null 2>&1; then
  $SUDO apt-get install -y -qq docker.io >/dev/null 2>&1 || warn "docker CLI unavailable — boxing disabled in this container"
fi
if [ "${SUIJIN_SKIP_ARSENAL:-0}" = "1" ]; then
  note "SUIJIN_SKIP_ARSENAL=1 — the box carries the tools (recommended inside containers)"
else
  APT_WEB=(nmap masscan gobuster ffuf feroxbuster sqlmap nikto whatweb sslscan amass subfinder nuclei dirb dirbuster)
  APT_CRACK=(hydra john hashcat)
  APT_INFRA=(snmp redis-tools metasploit-framework exploitdb testssl.sh)
  try_group "web/recon"    "${APT_WEB[@]}"
  try_group "cracking"     "${APT_CRACK[@]}"
  try_group "infra/ad"     "${APT_INFRA[@]}"
  $SUDO apt-get install -y -qq crackmapexec >/dev/null 2>&1 || $SUDO apt-get install -y -qq netexec >/dev/null 2>&1 || warn "crackmapexec/netexec unavailable"
fi
$SUDO apt-get clean && $SUDO rm -rf /var/lib/apt/lists/* 2>/dev/null || true
ok "system packages done"

# ── 2/5 python extras (pip) ────────────────────────────────────────────
if [ "${SUIJIN_SKIP_ARSENAL:-0}" = "1" ]; then
  note "skipping pip extras too — the box carries the tools"
else
  step "installing python extras (impacket, dnsrecon, wafw00f, dirsearch)"
  PIPFLAGS=""
  python3 -c 'import sys; exit(0 if hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix else 1)' 2>/dev/null || PIPFLAGS="--break-system-packages"
  python3 -m pip install --quiet $PIPFLAGS impacket dnsrecon wafw00f dirsearch \
    || warn "some pip extras failed — the pure-python tools still work"
  ok "pip extras done"
fi

# ── 3/5 suijin source + venv ───────────────────────────────────────────
step "installing suijin ($REPO_URL)"
mkdir -p "$INSTALL_DIR"
if [ -d "$REPO_URL/.git" ]; then
  note "local source: $REPO_URL"
  # stream-copy with excludes: agent-vault holds LOCAL-ONLY secrets,
  # .venv is 170MB+ of host payload — neither belongs in a container
  mkdir -p "$REPO_DIR"
  tar czf - --exclude=.venv --exclude=.git --exclude=agent-vault \
    --exclude=suijin_agent --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache \
    --exclude=node_modules -C "$REPO_URL" . | tar xzf - -C "$REPO_DIR"
elif [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" pull --ff-only >/dev/null 2>&1 || warn "pull failed — keeping current checkout"
else
  git clone --depth 1 "$REPO_URL" "$REPO_DIR" >/dev/null 2>&1 || fail "clone failed (network?)"
fi
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || fail "venv creation failed"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/suijin/requirements.txt" || fail "python deps failed"
ok "source + venv ready at $INSTALL_DIR"

# ── 4/5 workspace + launcher ───────────────────────────────────────────
step "preparing workspace + launcher"
mkdir -p "$REPO_DIR/suijin_agent"
if [ -d "$REPO_DIR/suijin/suijin_agent" ] && [ ! -L "$REPO_DIR/suijin/suijin_agent" ]; then
  cp -R "$REPO_DIR/suijin/suijin_agent/." "$REPO_DIR/suijin_agent/" 2>/dev/null || true
  rm -rf "$REPO_DIR/suijin/suijin_agent"
fi
ln -sfn ../suijin_agent "$REPO_DIR/suijin/suijin_agent"
LAUNCHER=/usr/local/bin/suijin
_launcher_body() {
  printf '#!/usr/bin/env bash\n# Suijin launcher — generated by kali-setup.sh\nexec "%s/bin/python" "%s/suijin/modules/console/lib/cli.py" "$@"\n' "$VENV" "$REPO_DIR"
}
if [ "$(id -u)" -eq 0 ]; then
  _launcher_body > "$LAUNCHER"
  chmod +x "$LAUNCHER"
else
  _launcher_body | $SUDO tee "$LAUNCHER" >/dev/null
  $SUDO chmod +x "$LAUNCHER"
fi
ok "suijin on PATH ($LAUNCHER)"

# ── 5/5 verify ─────────────────────────────────────────────────────────
step "verifying (suijin doctor)"
"$VENV/bin/python" "$REPO_DIR/suijin/modules/console/lib/cli.py" doctor || warn "doctor flagged issues above (usually optional tools)"
ok "setup complete"

ELAPSED=$(( $(date +%s) - T0 ))
printf "\n${BOLD}${CYAN}"
cat <<EOF
  ┌─────────────────────────────────────────────────┐
  │   Kali setup complete                           │
  │   elapsed: ${ELAPSED}s   install: ${INSTALL_DIR}│
  ├─────────────────────────────────────────────────┤
  │   start:    suijin                              │
  │   verify:   suijin doctor                       │
  │   workspace:${REPO_DIR}/suijin_agent            │
  └─────────────────────────────────────────────────┘
EOF
printf "${OFF}\n"
