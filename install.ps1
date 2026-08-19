# Suijin installer for Windows — DOCKER-ONLY
#
#   powershell -ExecutionPolicy Bypass -File install.ps1 install
#
# Subcommands (parity with docker.sh on macOS/Linux):
#   install        Docker check -> clone -> .env -> build -> verify
#   run [args...]  run the agent (interactive, or any CLI verb)
#   shell          shell into the workspace container
#   doctor         environment check inside the container
#   update         git pull + rebuild (workspace state survives)
#   down           stop containers (workspace volume KEPT)
#
# Native Windows Python is NOT supported — the tool ecosystem is
# Linux-based; Suijin runs in a Kali container with your workspace on a
# named volume.

param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "run", "shell", "doctor", "update", "down")]
    [string]$Action = "install",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$Repo = "https://github.com/0xwi11iam/Suijin.git"
$InstallDir = Join-Path $env:USERPROFILE ".suijin"
$RepoDir = Join-Path $InstallDir "repo"

function Say($msg)  { Write-Host "[suijin-docker] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  ok   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  warn $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "  fail $msg" -ForegroundColor Red; exit 1 }

function Ensure-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Die "docker not found. Install Docker Desktop: https://www.docker.com/products/docker-desktop/ then re-run."
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Die "docker daemon not running - start Docker Desktop and re-run."
    }
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Die "docker compose plugin missing - update Docker Desktop."
    }
    Ok "docker + compose ready"
}

function Ensure-Env {
    if (-not (Test-Path ".env")) {
        if (-not (Test-Path ".env.example")) { Die ".env.example missing - run from the repo root" }
        Copy-Item ".env.example" ".env"
        Warn "created .env from the example"
        Say "add your API keys to .env, then re-run this command"
        exit 1
    }
}

Push-Location $PSScriptRoot
try {
    switch ($Action) {
        "install" {
            Say "checking docker"
            Ensure-Docker

            Say "fetching source"
            if (Test-Path (Join-Path $RepoDir ".git")) {
                git -C $RepoDir pull --ff-only
                if ($LASTEXITCODE -ne 0) { Warn "pull failed - keeping current checkout" }
                Ok "updated existing checkout"
            } else {
                New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
                git clone --depth 1 $Repo $RepoDir
                if ($LASTEXITCODE -ne 0) { Die "clone failed - check network" }
                Ok "cloned to $RepoDir"
            }
            Push-Location $RepoDir
            try {
                Ensure-Env
                Say "building the suijin image (minimal kali-core footprint)"
                docker compose build
                if ($LASTEXITCODE -ne 0) { Die "build failed" }
                Ok "image built - state lives in the named volume 'suijin_workspace'"

                Say "verifying (doctor)"
                docker compose run --rm suijin python3 /app/suijin/modules/console/lib/cli.py doctor
                Ok "install complete"
                Write-Host ""
                Write-Host "  next:   .\docker.ps1 run        (or: powershell -File install.ps1 run)" -ForegroundColor White
                Write-Host "  shell:  .\docker.ps1 shell"
                Write-Host "  keys:   edit .env"
            } finally { Pop-Location }
        }

        "run" {
            Ensure-Docker; Ensure-Env
            if ($ExtraArgs -and $ExtraArgs.Count -gt 0) {
                docker compose run --rm suijin python3 /app/suijin/modules/console/lib/cli.py @ExtraArgs
            } else {
                docker compose run --rm suijin
            }
        }

        "shell"   { Ensure-Docker; Ensure-Env; docker compose run --rm suijin bash }
        "doctor"  { Ensure-Docker; Ensure-Env; docker compose run --rm suijin python3 /app/suijin/modules/console/lib/cli.py doctor }

        "update" {
            Ensure-Docker
            git pull --ff-only
            if ($LASTEXITCODE -ne 0) { Warn "pull failed - continuing" }
            Ensure-Env
            docker compose build
            if ($LASTEXITCODE -ne 0) { Die "rebuild failed" }
            Ok "updated - workspace volume untouched (outputs/KB survive)"
        }

        "down" {
            docker compose down
            Ok "containers stopped - workspace volume KEPT"
            Warn "wipe everything: docker volume rm suijin_workspace"
        }
    }
} finally { Pop-Location }
