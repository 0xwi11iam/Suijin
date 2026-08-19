#!/usr/bin/env bash
#
# Suijin Kali setup — run INSIDE an existing Kali container/VM:
#
#   curl -fsSL https://raw.githubusercontent.com/0xwi11iam/Suijin/main/kali-setup.sh | bash
#
# Installs the full tool arsenal (apt + pip) and Suijin itself:
#   source  -> /opt/suijin/repo
#   venv    -> /opt/suijin/venv
#   launcher-> /usr/local/bin/suijin (already on PATH)
#
# STOPS IMMEDIATELY if the host is not Kali.
# Not Kali?  macOS/Linux native: install.sh  |  Windows: install.ps1 (Docker)
#
# Env overrides: SUIJIN_REPO (default GitHub; may be a local checkout path)
#                SUIJIN_INSTALL_DIR (default /opt/suijin)
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
if [ ! -r /etc/os-release ] || ! grep -q '^ID=kali' /etc/os-release 2>/dev/null; then
  printf "  ${RED}fail${OFF} this host is not Kali (no ID=kali in /etc/os-release) — aborting.\n"
  printf '        native macOS/Linux: use install.sh\n'
  printf '        Windows / other:   use install.ps1 (Docker)\n'
  exit 1
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

# ── 1/5 system + tool arsenal (apt) ────────────────────────────────────
step "installing Kali tool arsenal (apt)"
$SUDO apt-get update -qq || warn "apt update failed — continuing with cached indexes"

APT_CORE=(python3 python3-pip python3-venv python3-dev build-essential git curl wget dnsutils whois netcat-openbsd socat)
APT_WEB=(nmap masscan gobuster ffuf feroxbuster sqlmap nikto whatweb sslscan amass subfinder nuclei dirb dirbuster)
APT_CRACK=(hydra john hashcat)
APT_INFRA=(snmp redis-tools metasploit-framework exploitdb testssl.sh)
# crackmapexec was renamed netexec on newer Kali — try both, warn on neither
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
try_group "web/recon"    "${APT_WEB[@]}"
try_group "cracking"     "${APT_CRACK[@]}"
try_group "infra/ad"     "${APT_INFRA[@]}"
$SUDO apt-get install -y -qq crackmapexec >/dev/null 2>&1 || $SUDO apt-get install -y -qq netexec >/dev/null 2>&1 || warn "crackmapexec/netexec unavailable"
$SUDO apt-get clean && $SUDO rm -rf /var/lib/apt/lists/* 2>/dev/null || true
ok "apt arsenal installed"

# ── 2/5 python extras (pip) ────────────────────────────────────────────
step "installing python extras (impacket, dnsrecon, wafw00f, dirsearch)"
PIPFLAGS=""
python3 -c 'import sys; exit(0 if hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix else 1)' 2>/dev/null || PIPFLAGS="--break-system-packages"
python3 -m pip install --quiet $PIPFLAGS impacket dnsrecon wafw00f dirsearch \
  || warn "some pip extras failed — the pure-python tools still work"
ok "pip extras done"

# ── 3/5 suijin source + venv ───────────────────────────────────────────
step "installing suijin ($REPO_URL)"
mkdir -p "$INSTALL_DIR"
if [ -d "$REPO_URL/.git" ]; then
  note "local source: $REPO_URL"
  rm -rf "$REPO_DIR"; cp -R "$REPO_URL" "$REPO_DIR"
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
  ┌──────────────────────────────────────────────┐
  │   Kali setup complete — full arsenal         │
  │   elapsed: ${ELAPSED}s   install: ${INSTALL_DIR}
  ├──────────────────────────────────────────────┤
  │   start:    suijin                            │
  │   verify:   suijin doctor                     │
  │   workspace:${REPO_DIR}/suijin_agent
  └──────────────────────────────────────────────┘
EOF
printf "${OFF}\n"
