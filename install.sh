#!/usr/bin/env bash
#
# Suijin installer (macOS / Linux)
#
#   curl -fsSL https://raw.githubusercontent.com/0xwi11iam/Suijin/main/install.sh | bash
#
# Tiers:
#   (default)  python core only        — fastest, agent + pure-python tools
#   --tools    + common pentest tools  — nmap, gobuster, ffuf, sqlmap, ...
#   --full     + everything            — adds metasploit, impacket suite,
#                                        hashcat, medusa, snmp, redis, ...
#
# Flags:
#   --tools / --full     tool tier (above)
#   --no-tools           force python-core only
#   SUIJIN_INSTALL_DIR   where to install (default ~/.suijin)
#   SUIJIN_BIN_DIR       launcher home   (default ~/.local/bin)
#   SUIJIN_REPO          source repo     (default GitHub; may be local)
#   SUIJIN_NO_PATH_EDIT=1  skip shell rc edits
#
# After install:
#   suijin doctor   # environment + tool availability report
#   suijin          # launch
#
set -euo pipefail

# Medusa-era env overrides still honored (rename compatibility)
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
    -h|--help)
      sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "[suijin] unknown flag: $arg (see --help)"; exit 1 ;;
  esac
done

STEP=0
TOTAL=7
info()  { printf "\033[32m[suijin %d/%d]\033[0m %s\n" "$((++STEP))" "$TOTAL" "$*"; }
warn()  { printf "\033[33m[suijin]\033[0m %s\n" "$*"; }
error() { printf "\033[31m[suijin]\033[0m %s\n" "$*"; exit 1; }
note()  { printf "         %s\n" "$*"; }

# Migrate a Medusa-era installation directory when Suijin's doesn't exist
if [ ! -d "$INSTALL_DIR" ] && [ -d "$HOME/.medusa" ]; then
  info "migrating legacy ~/.medusa -> $INSTALL_DIR"
  mv "$HOME/.medusa" "$INSTALL_DIR"
fi

# ── Platform ────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  *) error "Unsupported OS: $OS (macOS and Linux only)" ;;
esac

# ── Prerequisites ───────────────────────────────────────────────────────
for dep in git python3; do
  command -v "$dep" >/dev/null 2>&1 || error "$dep is required but not found."
done

# ── Fetch source ────────────────────────────────────────────────────────
info "fetching source"
mkdir -p "$INSTALL_DIR"
REPO_DIR="$INSTALL_DIR/repo"

if [ -d "$REPO_URL/.git" ]; then
  info "copying local source: $REPO_URL"
  rm -rf "$REPO_DIR"
  cp -R "$REPO_URL" "$REPO_DIR"
elif [ -d "$REPO_DIR/.git" ]; then
  info "updating existing checkout"
  git -C "$REPO_DIR" pull --ff-only || warn "pull failed — keeping current checkout"
else
  info "cloning $REPO_URL"
  git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

# ── Workspace layout ────────────────────────────────────────────────────
info "preparing agent workspace"
mkdir -p "$REPO_DIR/suijin_agent"
if [ -d "$REPO_DIR/suijin/suijin_agent" ] && [ ! -L "$REPO_DIR/suijin/suijin_agent" ]; then
  cp -R "$REPO_DIR/suijin/suijin_agent/." "$REPO_DIR/suijin_agent/" 2>/dev/null || true
  rm -rf "$REPO_DIR/suijin/suijin_agent"
fi
ln -sfn ../suijin_agent "$REPO_DIR/suijin/suijin_agent"

# ── Virtualenv + deps ───────────────────────────────────────────────────
info "installing python dependencies"
VENV="$INSTALL_DIR/venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/suijin/requirements.txt"

# ── Optional pentest tools ──────────────────────────────────────────────
CORE_TOOLS=()
TOOLS_TIER=(nmap gobuster ffuf sqlmap nikto whatweb sslscan amass subfinder nuclei)
FULL_TIER=(metasploit-framework hydra john hashcat medusa snmp redis impacket dnsrecon wafw00f dirsearch testssl.sh crackmapexec)

install_pkgs() {
  local pkgs=("$@")
  local miss=()
  for p in "${pkgs[@]}"; do
    command -v "$p" >/dev/null 2>&1 || miss+=("$p")
  done
  if [ ${#miss[@]} -eq 0 ]; then
    note "all present"
    return 0
  fi
  if command -v brew >/dev/null 2>&1; then
    note "brew installing: ${miss[*]}"
    brew install -q "${miss[@]}" 2>/dev/null || warn "some brew packages unavailable (names differ on macOS) — run 'suijin doctor' for hints"
  elif command -v apt-get >/dev/null 2>&1; then
    note "apt installing: ${miss[*]}"
    sudo apt-get update -qq && sudo apt-get install -y -qq "${miss[@]}" || warn "some apt packages unavailable — run 'suijin doctor' for hints"
  else
    warn "no brew/apt found; install manually: ${miss[*]}"
  fi
}

case "$TIER" in
  tools)
    info "installing common pentest tools (${TOOLS_TIER[*]})"
    install_pkgs "${TOOLS_TIER[@]}" ;;
  full)
    info "installing the FULL tool arsenal (${TOOLS_TIER[*]} + ${FULL_TIER[*]})"
    install_pkgs "${TOOLS_TIER[@]}" "${FULL_TIER[@]}" ;;
  core)
    info "python-core tier (skip tools with --tools / --full)" ;;
esac

# ── Launcher ────────────────────────────────────────────────────────────
info "installing 'suijin' launcher"
mkdir -p "$BIN_DIR"
LAUNCHER="$BIN_DIR/suijin"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Suijin launcher — generated by install.sh
exec "$VENV/bin/python" "$REPO_DIR/suijin/modules/console/lib/cli.py" "\$@"
EOF
chmod +x "$LAUNCHER"

# ── PATH ────────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  if [ "${SUIJIN_NO_PATH_EDIT:-0}" = "1" ]; then
    warn "Add $BIN_DIR to your PATH, then run 'suijin'."
  else
    for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
      if [ -f "$rc" ] && ! grep -q "suijin PATH" "$rc" 2>/dev/null; then
        printf '\n# suijin PATH\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$rc"
        info "updated $(basename "$rc")"
      fi
    done
    export PATH="$BIN_DIR:$PATH"
  fi
fi

# ── Verify + tool availability report ───────────────────────────────────
info "verifying installation"
"$VENV/bin/python" "$REPO_DIR/suijin/modules/console/lib/cli.py" doctor || true

echo
info "install complete ($TIER tier)"
echo "         Start:      suijin"
echo "             Verify: suijin doctor"
echo "             Tools:   re-run with --tools or --full to add the arsenal"
[ "$TIER" = "core" ] && note "pure-python tools (50+) work already; binary tools unlock with --tools"
