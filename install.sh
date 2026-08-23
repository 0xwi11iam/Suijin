#!/usr/bin/env bash
#
# Suijin installer — macOS / Linux native
#
#   curl -fsSL https://raw.githubusercontent.com/0xwi11iam/Suijin/main/install.sh | bash
#
# Interactive start (when run in a terminal): confirms your OS and pip
# command with detected defaults — just press Enter. Piped/non-interactive
# runs skip the questions and use auto-detection.
#
# Dependencies are resolved FULLY: missing git/python3 (and venv/build
# headers on apt systems) are installed automatically; nothing is left
# for the user to fix by hand.
#
# Tiers:
#   (default)  python core        — fastest: agent + 50+ pure-python tools
#   --tools    + common pentest   — nmap, gobuster, ffuf, sqlmap, ...
#   --full     + heavy arsenal    — metasploit, impacket, hashcat, ...
#
# Windows? Do NOT use this script — run install.ps1 (Docker-based).
# Existing Kali container? Use kali-setup.sh instead.
#
# Flags:  --tools | --full | --no-tools | --dev[=PATH] | -h/--help
#         --dev installs from the local source tree the script lives in
#         (or PATH when given) — live symlink, edits are picked up. Run
#         interactively from inside a checkout and dev is the DEFAULT.
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
DEV_FLAG_SOURCE=""   # set when --dev[=PATH] passed
for arg in "$@"; do
  case "$arg" in
    --tools) TIER="tools" ;;
    --full)  TIER="full" ;;
    --no-tools) TIER="core" ;;
    --dev) DEV_FLAG_SOURCE="auto" ;;
    --dev=*) DEV_FLAG_SOURCE="${arg#--dev=}" ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) printf '[suijin] unknown flag: %s (see --help)\n' "$arg"; exit 1 ;;
  esac
done

# Running from inside a checkout? (script dir has pyproject.toml + suijin/)
# Then dev mode — installing from THIS local copy — becomes the default,
# and the source path defaults to the script's dir (not $PWD).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P)"
RUNS_FROM_CHECKOUT=0
if [ -f "$SCRIPT_DIR/pyproject.toml" ] && [ -d "$SCRIPT_DIR/suijin" ]; then
  RUNS_FROM_CHECKOUT=1
fi

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

# ask_default PROMPT DEFAULT — sets ANSWER; non-TTY runs (curl | bash)
# take the detected default without blocking on stdin.
ask_default() {
  if [ -t 0 ] && [ -t 1 ]; then
    printf "  %s ${DIM}[%s]${OFF} " "$1" "$2"
    read -r ANSWER || true
    ANSWER="${ANSWER:-$2}"
  else
    ANSWER="$2"
  fi
}

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
  ┌───────────────────────────────────────────────┐
  │   install complete — ${TIER} tier             │
  │   elapsed: ${mins}m ${secs}s                  │
  ├───────────────────────────────────────────────┤
  │   start:      suijin                          │
  │   verify:     suijin doctor                   │
  │   workspace:  ${INSTALL_DIR}/repo/suijin_agent│
  │   more tools: re-run with --tools or --full.  │
  └───────────────────────────────────────────────┘
EOF
  printf "${OFF}\n"
}

# system package install helper (best effort, never aborts the install)
have() { command -v "$1" >/dev/null 2>&1; }
SUDO=""
if [ "$(uname -s)" = "Linux" ] && [ "$(id -u)" -ne 0 ]; then
  have sudo && SUDO="sudo"
fi
sys_install() {
  if have brew; then
    brew install -q "$@" 2>/dev/null || return 1
  elif have apt-get; then
    $SUDO apt-get update -qq >/dev/null 2>&1 || true
    $SUDO apt-get install -y -qq "$@" >/dev/null 2>&1 || return 1
  else
    return 1
  fi
}

banner

# ── 1/8 platform + preferences ─────────────────────────────────────────
step "install type + platform"

# FIRST question: normal (released) or dev (local tree) install?
#   --dev[=PATH] forces dev non-interactively; run from inside a
#   checkout and dev is the interactive DEFAULT.
DEV_MODE="use"
DEV_SOURCE=""
MODE_DEFAULT="use"
[ "$RUNS_FROM_CHECKOUT" = "1" ] && MODE_DEFAULT="dev"
if [ -n "$DEV_FLAG_SOURCE" ]; then
  DEV_MODE="dev"
  if [ "$DEV_FLAG_SOURCE" = "auto" ]; then
    [ "$RUNS_FROM_CHECKOUT" = "1" ] || fail "--dev passed but the script is not inside a Suijin checkout — use --dev=/path/to/tree"
    DEV_FLAG_SOURCE="$SCRIPT_DIR"
  fi
  DEV_SOURCE="$DEV_FLAG_SOURCE"
  if [ ! -f "$DEV_SOURCE/pyproject.toml" ] || [ ! -d "$DEV_SOURCE/suijin" ]; then
    fail "not a Suijin source tree (needs pyproject.toml + suijin/): '$DEV_SOURCE'"
  fi
  note "dev install from: $DEV_SOURCE"
elif [ -t 0 ] && [ -t 1 ]; then
  printf "  first things first — just press Enter to accept the detected default\n"
  ask_default "install type: (normal) released tool, or (dev) from a local source tree?" "$MODE_DEFAULT"
  case "$ANSWER" in
    dev|d|developer)
      DEV_MODE="dev"
      PATH_DEFAULT="$PWD"
      [ "$RUNS_FROM_CHECKOUT" = "1" ] && PATH_DEFAULT="$SCRIPT_DIR"
      ask_default "path to your local Suijin source tree" "$PATH_DEFAULT"
      RAW_PATH="$ANSWER"
      # sanitize: expand, resolve, validate structure
      DEV_SOURCE="$(printf '%s' "$RAW_PATH" | sed 's/[[:space:]]*$//;s/^[[:space:]]*//')"
      DEV_SOURCE="${DEV_SOURCE/#\~/$HOME}"
      if [ -z "$DEV_SOURCE" ] || [ "$DEV_SOURCE" = "/" ] || [ "$DEV_SOURCE" = "$HOME" ]; then
        fail "invalid source path: '$DEV_SOURCE'"
      fi
      DEV_SOURCE="$(cd "$DEV_SOURCE" 2>/dev/null && pwd)" || fail "cannot access: '$RAW_PATH'"
      if [ ! -f "$DEV_SOURCE/pyproject.toml" ] || [ ! -d "$DEV_SOURCE/suijin" ]; then
        fail "not a Suijin source tree (needs pyproject.toml + suijin/): '$DEV_SOURCE'"
      fi
      note "dev install from: $DEV_SOURCE (live symlink — edits are picked up)"
      ;;
    normal|n|use|u|*)
      DEV_MODE="use"
      ;;
  esac
else
  note "non-interactive run — install type: normal (released); use --dev for a local tree"
fi

DETECTED_OS="$(uname -s)"
case "$DETECTED_OS" in
  Darwin) DETECTED_LABEL="macos" ;;
  Linux)  DETECTED_LABEL="linux" ;;
  *) fail "unsupported OS: $DETECTED_OS (macOS/Linux native; Windows uses install.ps1 + Docker)" ;;
esac

if [ -t 0 ] && [ -t 1 ]; then
  ask_default "which OS are you installing on? (macos/linux)" "$DETECTED_LABEL"
  case "$ANSWER" in
    macos|mac|darwin|osx) CHOSEN_OS="macos" ;;
    linux|gnu/linux)      CHOSEN_OS="linux" ;;
    *) CHOSEN_OS="$DETECTED_LABEL"; warn "unrecognized choice — using detected '$DETECTED_LABEL'" ;;
  esac
  if [ "$CHOSEN_OS" != "$DETECTED_LABEL" ]; then
    warn "you chose '$CHOSEN_OS' but this machine detects as '$DETECTED_LABEL' — going with your choice for package selection"
  fi
else
  CHOSEN_OS="$DETECTED_LABEL"
  note "non-interactive run — OS auto-detected: $CHOSEN_OS"
fi

# pip preference (pip3 is the modern default; pip honored if chosen)
PIP_DEFAULT="pip3"; have pip3 || { have pip && PIP_DEFAULT="pip"; }
if [ -t 0 ] && [ -t 1 ]; then
  ask_default "which pip command do you use? (pip3/pip)" "$PIP_DEFAULT"
  case "$ANSWER" in
    pip3) PIP_BIN="pip3" ;;
    pip)  PIP_BIN="pip" ;;
    *)    PIP_BIN="$PIP_DEFAULT"; warn "unrecognized choice — using '$PIP_DEFAULT'" ;;
  esac
else
  PIP_BIN="$PIP_DEFAULT"
  note "non-interactive run — pip command: $PIP_BIN"
fi

ARCH="$(uname -m)"
PKG="none"
[ "$CHOSEN_OS" = "macos" ] && PKG="brew"
[ "$CHOSEN_OS" = "linux" ] && PKG="apt"

ok "$CHOSEN_OS ($ARCH), packages via $PKG, pip: $PIP_BIN, mode: $DEV_MODE"

# ── 2/8 prerequisites — fully resolved, nothing left to the user ───────
step "resolving prerequisites (git, python3, build headers)"

if ! have git; then
  warn "git not found — installing"
  if [ "$CHOSEN_OS" = "macos" ] && ! have brew; then
    if [ -t 0 ]; then
      ask_default "Homebrew missing — install it now? (y/n)" "y"
      case "$ANSWER" in y|Y|yes) /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || warn "brew install failed";; esac
    else
      note "no brew and non-interactive — trying xcode command line tools for git"
      xcode-select --install >/dev/null 2>&1 || true
    fi
  fi
  sys_install git || warn "could not auto-install git — install it manually and re-run"
fi
have git && ok "git $(git --version 2>/dev/null | awk '{print $3}' || echo present)" || warn "git still missing (continuing — needed for clone)"

if ! have python3; then
  warn "python3 not found — installing"
  if [ "$CHOSEN_OS" = "macos" ]; then
    sys_install python@3.14 2>/dev/null || sys_install python3 || warn "python install failed — install python3 and re-run"
  else
    sys_install python3 python3-pip python3-venv python3-dev build-essential || warn "python install failed — install python3 and re-run"
  fi
fi
have python3 || fail "python3 could not be resolved automatically — install Python 3.10+ and re-run"
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
ok "python3 ${PYV}"

# venv capability: Debian/Ubuntu ships python3-venv separately — resolve it
if ! python3 -c "import venv" >/dev/null 2>&1; then
  warn "python3 venv module missing — installing"
  sys_install "python3-venv" "python${PYV}-venv" 2>/dev/null || warn "venv package unavailable"
  python3 -c "import venv" >/dev/null 2>&1 || fail "python3 venv unavailable — install python3-venv and re-run"
fi
ok "venv module ready"

# pip bootstrap on the chosen command (best effort; the venv has its own)
if ! have "$PIP_BIN"; then
  warn "$PIP_BIN not found — trying the other, then ensurepip"
  have pip3 && PIP_BIN="pip3" || { have pip && PIP_BIN="pip"; }
  have "$PIP_BIN" || python3 -m ensurepip --upgrade >/dev/null 2>&1 || sys_install python3-pip || true
  have "$PIP_BIN" || note "system $PIP_BIN unavailable — the installer venv carries its own pip (safe to continue)"
fi
ok "pip: $PIP_BIN"

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
if [ "$DEV_MODE" = "dev" ]; then
  # dev mode: symlink the local source (live-editable; no copying)
  note "dev mode: linking $DEV_SOURCE"
  rm -rf "$REPO_DIR"
  ln -sfn "$DEV_SOURCE" "$REPO_DIR"
  ok "linked to local source (edits are live)"
elif [ -d "$REPO_URL/.git" ]; then
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
step "preparing agent workspace (durable — survives reinstalls)"
# The workspace lives at ~/.suijin/workspace, OUTSIDE any repo copy:
# sessions, memory, the authorization ledger, bugscope pulls and reports
# survive re-clones, reinstalls and dev-tree wipes. The repo-local
# suijin_agent path symlinks to it for back-compat.
DURABLE_WS="$INSTALL_DIR/workspace"
mkdir -p "$DURABLE_WS"
# migrate legacy repo-local workspace content into the durable home (merge)
for _ws in "$REPO_DIR/suijin_agent" "$REPO_DIR/suijin/suijin_agent"; do
  if [ -d "$_ws" ] && [ ! -L "$_ws" ] && [ "$(ls -A "$_ws" 2>/dev/null)" ]; then
    cp -R "$_ws/." "$DURABLE_WS/" 2>/dev/null || true
  fi
done
rm -rf "$REPO_DIR/suijin_agent"
ln -sfn "$DURABLE_WS" "$REPO_DIR/suijin_agent"
if [ -d "$REPO_DIR/suijin/suijin_agent" ] && [ ! -L "$REPO_DIR/suijin/suijin_agent" ]; then
  rm -rf "$REPO_DIR/suijin/suijin_agent"
fi
ln -sfn ../../suijin_agent "$REPO_DIR/suijin/suijin_agent" 2>/dev/null || \
  ln -sfn "$DURABLE_WS" "$REPO_DIR/suijin/suijin_agent"
ok "workspace ready at $DURABLE_WS (reinstall-safe)"

# ── 5/8 python deps (venv health check + build-header retry) ───────────
step "creating virtualenv + installing python deps"
VENV="$INSTALL_DIR/venv"
# a venv inherited from a migration (or an OS python upgrade) can carry a
# stale interpreter shebang — detect and rebuild instead of failing on it
if [ -x "$VENV/bin/python" ] && ! "$VENV/bin/python" --version >/dev/null 2>&1; then
  warn "existing venv is broken (stale interpreter) — rebuilding it"
  rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || fail "venv creation failed"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
if ! "$VENV/bin/pip" install --quiet -r "$REPO_DIR/suijin/requirements.txt"; then
  warn "dependency install failed — resolving build headers and retrying"
  if [ "$CHOSEN_OS" = "linux" ]; then
    sys_install build-essential "python${PYV}-dev" python3-dev || true
  else
    note "macOS: ensure Xcode command line tools are current (xcode-select --install)"
  fi
  "$VENV/bin/pip" install --quiet -r "$REPO_DIR/suijin/requirements.txt" \
    || fail "python deps failed after retry — see output above"
fi
NPKGS=$( "$VENV/bin/pip" list 2>/dev/null | wc -l | tr -d ' ' )
ok "venv ready with ${NPKGS} packages"

# ── 6/8 optional tools ─────────────────────────────────────────────────
TOOLS_TIER=(nmap gobuster ffuf sqlmap nikto whatweb sslscan amass subfinder nuclei)
FULL_TIER=(metasploit-framework hydra john hashcat medusa snmp redis impacket dnsrecon wafw00f dirsearch testssl.sh crackmapexec)

install_pkgs() {
  local want=("$@") miss=() have_now=()
  for p in "${want[@]}"; do
    if command -v "$p" >/dev/null 2>&1; then have_now+=("$p"); else miss+=("$p"); fi
  done
  [ ${#have_now[@]} -gt 0 ] && note "already present: ${have_now[*]}"
  [ ${#miss[@]} -eq 0 ] && return 0
  note "installing: ${miss[*]}"
  if have brew; then
    brew install -q "${miss[@]}" 2>/dev/null \
      || warn "some brew names differ on macOS — run 'suijin doctor' for hints"
  elif have apt-get; then
    $SUDO apt-get update -qq >/dev/null 2>&1 || true
    $SUDO apt-get install -y -qq "${miss[@]}" >/dev/null 2>&1 \
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
