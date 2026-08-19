# Suijin Docker helper — one command for everything
#
#   ./docker.sh install        build the image (pulls .env from example)
#   ./docker.sh run [args...]  run the agent (interactive, or any CLI verb)
#   ./docker.sh shell          shell into the workspace container
#   ./docker.sh doctor         environment check inside the container
#   ./docker.sh update         git pull + rebuild (state survives: named volume)
#   ./docker.sh down           stop/remove containers (workspace volume KEPT)
#
# Works with Docker Desktop OR colima; auto-detects compose v2/v1.
# Windows users: run install.ps1 instead — it drives the same flow.

set -euo pipefail

# ── pretty helpers (no emojis, colors only) ────────────────────────────
BOLD=""; CYAN=""; GREEN=""; YELLOW=""; RED=""; DIM=""; OFF=""
[ -t 1 ] && { BOLD="\033[1m"; CYAN="\033[36m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; DIM="\033[2m"; OFF="\033[0m"; }
say()  { printf "${BOLD}${CYAN}[suijin-docker]${OFF} %s\n" "$*"; }
ok()   { printf "  ${GREEN}ok${OFF} %s\n" "$*"; }
warn() { printf "  ${YELLOW}warn${OFF} %s\n" "$*"; }
die()  { printf "  ${RED}fail${OFF} %s\n" "$*"; exit 1; }

cd "$(dirname "$0")"

# ── docker + compose detection (desktop or colima) ─────────────────────
command -v docker >/dev/null 2>&1 || die "docker not found — install Docker Desktop or colima:
  macOS:  brew install --cask docker   (or: brew install colima && colima start)
  linux:  curl -fsSL https://get.docker.com | sh"

if ! docker info >/dev/null 2>&1; then
  if command -v colima >/dev/null 2>&1; then
    say "docker daemon not running — starting colima"
    colima start || die "colima start failed"
  else
    die "docker daemon not running — start Docker Desktop (or colima start)"
  fi
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  command -v docker-compose >/dev/null 2>&1 || die "docker compose plugin missing"
  COMPOSE="docker-compose"
fi

ENV_FILE=".env"
ENV_EXAMPLE=".env.example"

ensure_env() {
  if [ ! -f "$ENV_FILE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    warn "created $ENV_FILE from the example"
    say "add your API keys to $ENV_FILE, then re-run this command"
    exit 1
  fi
}

case "${1:-help}" in
  install)
    ensure_env
    say "building the suijin image (minimal kali-core footprint)"
    $COMPOSE build || die "build failed"
    $COMPOSE create >/dev/null 2>&1 || true
    ok "image built — state lives in the named volume 'suijin_workspace'"
    say "next: ./docker.sh run"
    ;;

  run)
    ensure_env
    shift
    if [ $# -gt 0 ]; then
      $COMPOSE run --rm suijin python3 /app/suijin/modules/console/lib/cli.py "$@"
    else
      $COMPOSE run --rm suijin
    fi
    ;;

  shell)
    ensure_env
    $COMPOSE run --rm suijin bash
    ;;

  doctor)
    ensure_env
    $COMPOSE run --rm suijin python3 /app/suijin/modules/console/lib/cli.py doctor
    ;;

  update)
    say "updating source"
    git pull --ff-only || warn "pull failed — continuing with current checkout"
    ensure_env
    $COMPOSE build || die "rebuild failed"
    ok "updated — workspace volume untouched (outputs/KB survive)"
    ;;

  down)
    $COMPOSE down
    ok "containers stopped — workspace volume KEPT"
    note="wipe everything: docker volume rm suijin_workspace"
    printf "      ${DIM}%s${OFF}\n" "$note"
    ;;

  *)
    sed -n '2,12p' "$0"
    ;;
esac
