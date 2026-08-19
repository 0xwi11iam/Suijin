#!/usr/bin/env bash
#
# Suijin installer — macOS / Linux native
#
#   curl -fsSL https://raw.githubusercontent.com/0xwi11iam/Suijin/main/install.sh | bash
#
# Tiers:
#   (default)  python core        — fastest: agent + 50+ pure-python tools
#   --tools    + common pentest   — nmap, gobuster, ffuf, sqlmap, ...
#   --full     + heavy arsenal    — metasploit, impacket, hashcat, ...
#
# Windows? Do NOT use this script — run install.ps1 (Docker-based).
#
# Flags:  --tools | --full | --no-tools | -h/--help
# Env:    SUIJIN_INSTALL_DIR (default ~/.suijin), SUIJIN_BIN_DIR
#         (default ~/.local/bin), SUIJIN_REPO (default GitHub; may be local),
#         SUIJIN_NO_PATH_EDIT=1 skips shell rc edits
#
set -euo pipefail

REPO_URL="${SUIJIN_REPO:-${MEDUSA_REPO:-https://github.com/0xwi11iam/Suijin.git}}"
INSTALL_DIR="${SUIJIN_INSTALL_DIR:-${MEDUSA_INSTALL_DIR:-$HOME/.suijin}}"
BIN_DIR="${SUIJIN_BIN_DIR:-${MEDUSA_BIN_DIR:-$HOME/.local/bin}}"
export SUIJIN_NO_PATH_EDIT="${SUIJIN_NO_PATH_EDIT:-${MEDUSA_NO_PATH_EDIT:-0}}"

TIER="core"
for arg in "$@"; do
  case "$arg" in
    --tools) TIER="tools" ;;
    --full)  TIER="full" ;;
    --no-tools) TIER="core" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) printf '[suijin] unknown flag: %s (see --help)\n' "$arg"; exit 1 ;;
  esac
done

# ── output helpers ─────────────────────────────────────────────────────
BOLD=""; CYAN=""; GREEN=""; YELLOW=""; RED=""; DIM=""; OFF=""
if [ -t 1 ]; then
  BOLD="\033[1m"; CYAN="\033[36m"; GREEN="\033[32m"; YELLOW="\033[33m"
  RED="\033[31m"; DIM="\033[2m"; OFF="\033[0m"
fi
STEP=0; TOTAL=8; T_START=$(date +%s)

step()   { STEP=$((STEP+1)); printf "\n${BOLD}${CYAN}[ %d/%d ] %s${OFF}\n" "$STEP" "$TOTAL" "$*"; _s=$(date +%s); }
ok()     { printf "  ${GREEN}ok${OFF}   %s ${DIM}(+%ds)${OFF}\n" "$*" "$(( $(date +%s) - _s ))"; }
warn()   { printf "  ${YELLOW}warn${OFF} %s\n" "$*"; }
fail()   { printf "  ${RED}fail${OFF} %s\n" "$*"; exit 1; }
note()   { printf "      ${DIM}%s${OFF}\n" "$*"; }

banner() {
  printf "\n${BOLD}${CYAN}"
  cat <<'EOF'
  ┌─────────────────────────────────────────────┐
  │   Suijin — autonomous red & blue teaming    │
  │   native install (macOS / Linux)            │
  └─────────────────────────────────────────────┘
EOF
  printf "${OFF}\n"
}

summary() {
  local mins=$(( ($(date +%s) - T_START) / 60 ))
  local secs=$(( ($(date +%s) - T_START) % 60 ))
  printf "\n${BOLD}${CYAN}"
  cat <<EOF
  ┌─────────────────────────────────────────────┐
  │   install complete — ${TIER} tier
  │   elapsed: ${mins}m ${secs}s
  ├─────────────────────────────────────────────┤
  │   start:      suijin
  │   verify:     suijin doctor
  │   workspace:  ${INSTALL_DIR}/repo/suijin_agent
  │   more tools: re-run with --tools or --full
  └─────────────────────────────────────────────┘
EOF
  printf "${OFF}\n"
}

# ── 1/8 platform ───────────────────────────────────────────────────────
banner
step "checking platform"
OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS" in
  Darwin) PKG="brew" ;;
  Linux)  PKG="apt" ;;
  *) fail "unsupported OS: $OS (macOS/Linux native; Windows uses install.ps1 + Docker)" ;;
esac
if [ "$OS" = "Linux" ] && ! command -v apt-get >/dev/null 2>&1; then
  warn "no apt-get found — tool tiers will list manual install hints instead"
fi
ok "$OS ($ARCH) — packages via $PKG"
[ "$OS" = "Darwin" ] && note "apple silicon detected" [ "$ARCH" = "arm64" ] || true

# ── 2/8 prerequisites ──────────────────────────────────────────────────
step "checking prerequisites (git, python3)"
MISSING=()
for dep in git python3; do
  command -v "$dep" >/dev/null 2>&1 || MISSING+=("$dep")
done
[ ${#MISSING[@]} -gt 0 ] && fail "missing: ${MISSING[*]} — install them and re-run"
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
ok "git + python3 ${PYV}"

# ── 3/8 source ─────────────────────────────────────────────────────────
step "fetching source"
# Medusa-era install migration: rename the old dir wholesale (marker kept).
# MUST run before mkdir -p $INSTALL_DIR (the guard checks non-existence).
if [ ! -d "$INSTALL_DIR" ] && [ -d "$HOME/.medusa" ]; then
  note "migrating legacy ~/.medusa -> $INSTALL_DIR"
  mv "$HOME/.medusa" "$INSTALL_DIR"
fi
mkdir -p "$INSTALL_DIR"
REPO_DIR="$INSTALL_DIR/repo"
if [ -d "$REPO_URL/.git" ]; then
  note "local source: $REPO_URL"
  rm -rf "$REPO_DIR"; cp -R "$REPO_URL" "$REPO_DIR"
  ok "copied local checkout"
elif [ -d "$REPO_DIR/.git" ]; then
  if git -C "$REPO_DIR" pull --ff-only >/dev/null 2>&1; then
    ok "updated existing checkout"
  else
    warn "pull failed (diverged?) — keeping current checkout"
  fi
else
  note "cloning $REPO_URL"
  git clone --depth 1 "$REPO_URL" "$REPO_DIR" >/dev/null 2>&1 \
    || fail "clone failed — check network or set SUIJIN_REPO"
  ok "cloned"
fi

# ── 4/8 workspace ──────────────────────────────────────────────────────
step "preparing agent workspace"
mkdir -p "$REPO_DIR/suijin_agent"
if [ -d "$REPO_DIR/suijin/suijin_agent" ] && [ ! -L "$REPO_DIR/suijin/suijin_agent" ]; then
  cp -R "$REPO_DIR/suijin/suijin_agent/." "$REPO_DIR/suijin_agent/" 2>/dev/null || true
  rm -rf "$REPO_DIR/suijin/suijin_agent"
fi
ln -sfn ../suijin_agent "$REPO_DIR/suijin/suijin_agent"
ok "workspace ready at suijin_agent/"

# ── 5/8 python deps ────────────────────────────────────────────────────
step "creating virtualenv + installing python deps"
VENV="$INSTALL_DIR/venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || fail "venv creation failed"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/suijin/requirements.txt" \
  || fail "python deps failed — see output above"
ok "$(basename "$VENV") ready with $( "$VENV/bin/pip" list 2>/dev/null | wc -l | tr -d ' ' ) packages"

# ── 6/8 optional tools ─────────────────────────────────────────────────
CORE_TOOLS=()
TOOLS_TIER=(nmap gobuster ffuf sqlmap nikto whatweb sslscan amass subfinder nuclei)
FULL_TIER=(metasploit-framework hydra john hashcat medusa snmp redis impacket dnsrecon wafw00f dirsearch testssl.sh crackmapexec)

install_pkgs() {
  local want=("$@") miss=() have=()
  for p in "${want[@]}"; do
    if command -v "$p" >/dev/null 2>&1; then have+=("$p"); else miss+=("$p"); fi
  done
  [ ${#have[@]} -gt 0 ] && note "already present: ${have[*]}"
  [ ${#miss[@]} -eq 0 ] && return 0
  note "installing: ${miss[*]}"
  if command -v brew >/dev/null 2>&1; then
    brew install -q "${miss[@]}" 2>/dev/null \
      || warn "some brew names differ on macOS — run 'suijin doctor' for hints"
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq >/dev/null 2>&1 || true
    sudo apt-get install -y -qq "${miss[@]}" >/dev/null 2>&1 \
      || warn "some apt packages unavailable — run 'suijin doctor' for hints"
  else
    warn "no brew/apt — install manually: ${miss[*]}"
  fi
}

case "$TIER" in
  tools)
    step "installing common pentest tools (${#TOOLS_TIER[@]})"
    install_pkgs "${TOOLS_TIER[@]}"
    ok "tools tier done (missing ones will show hints in doctor)" ;;
  full)
    step "installing the FULL arsenal (${#TOOLS_TIER[@]} common + ${#FULL_TIER[@]} heavy)"
    install_pkgs "${TOOLS_TIER[@]}" "${FULL_TIER[@]}"
    ok "full tier done" ;;
  core)
    step "python-core tier — skipping binary tools"
    note "50+ pure-python tools work out of the box"
    note "add the arsenal later: re-run with --tools or --full" ;;
esac

# ── 7/8 launcher + PATH ────────────────────────────────────────────────
step "installing 'suijin' launcher"
mkdir -p "$BIN_DIR"
LAUNCHER="$BIN_DIR/suijin"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Suijin launcher — generated by install.sh
exec "$VENV/bin/python" "$REPO_DIR/suijin/modules/console/lib/cli.py" "\$@"
EOF
chmod +x "$LAUNCHER"
ok "launcher at $LAUNCHER"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  if [ "${SUIJIN_NO_PATH_EDIT:-0}" = "1" ]; then
    warn "PATH not edited (SUIJIN_NO_PATH_EDIT=1) — add $BIN_DIR manually"
  else
    for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
      if [ -f "$rc" ] && ! grep -q "suijin PATH" "$rc" 2>/dev/null; then
        printf '\n# suijin PATH\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$rc"
        note "updated $(basename "$rc")"
      fi
    done
    export PATH="$BIN_DIR:$PATH"
  fi
fi

# ── 8/8 verify ─────────────────────────────────────────────────────────
step "verifying installation (suijin doctor)"
if "$VENV/bin/python" "$REPO_DIR/suijin/modules/console/lib/cli.py" doctor; then
  ok "doctor passed"
else
  warn "doctor reported issues above — usually missing optional tools"
fi

summary
