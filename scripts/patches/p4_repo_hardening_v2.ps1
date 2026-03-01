# P4: repo hardening (SECURITY.md + README.md + .gitattributes + .editorconfig + CI + .gitignore hygiene)
# Run from repo root with PowerShell 7:
#   pwsh -ExecutionPolicy Bypass -File .\scripts\patches\p4_repo_hardening_v2.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Utf8NoBomFile([string]$Path, [string]$Content) {
  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  $enc = [System.Text.UTF8Encoding]::new($false)
  [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

if (-not (Test-Path '.\config.yaml')) {
  throw 'Run this patch from the repo root (expected config.yaml).'
}

# -----------------------------------------------------------------------------
# 1) .gitignore (keep original intent + add sqlite WAL/SHM/JOURNAL)
# -----------------------------------------------------------------------------
$gitignore = @'
# Python / venv
.venv/
__pycache__/
*.py[cod]
*.pyd
*.egg-info/
dist/
build/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Env / secrets
.env
.env.*
!.env.example

# Data & logs (do NOT version)
data/*.csv
data/*.sqlite3
data/*.db

# SQLite sidecar files (WAL/SHM/JOURNAL)
*.sqlite3-wal
*.sqlite3-shm
*.sqlite3-journal
*.db-wal
*.db-shm
*.db-journal
*.sqlite-wal
*.sqlite-shm
*.sqlite-journal

# Runs / artifacts
runs/
*.log

# OS / editor
.DS_Store
Thumbs.db
.vscode/
.idea/

# Local backups
config_backup_*.yaml
runs_fx_backup_*/
'@
Write-Utf8NoBomFile '.gitignore' $gitignore

# -----------------------------------------------------------------------------
# 2) SECURITY.md
# -----------------------------------------------------------------------------
$security = @'
# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability, please **do not open a public issue**.

Preferred reporting channels:

1) **GitHub Security Advisory** (recommended):
   - Go to the repository **Security** tab -> **Advisories** -> **New draft security advisory**.
   - Provide a clear description, impact, and steps to reproduce.

2) **Email** (alternative):
   - Send an email to: **<ADD_CONTACT_EMAIL_HERE>**
   - Include: affected version/commit, reproduction steps, and potential impact.

## What to include

- A short summary of the issue and the potential impact
- Steps to reproduce (PoC if possible)
- Any mitigations you are aware of

## Please avoid

- Posting secrets (IQ Option credentials, tokens, personal data)
- Public disclosure before a fix is available

## Scope

This project is experimental and focused on research/engineering. We still take security reports seriously,
especially anything involving credential leakage, unintended network access, code execution, or data exfiltration.
'@
Write-Utf8NoBomFile 'SECURITY.md' $security

# -----------------------------------------------------------------------------
# 3) README.md
# -----------------------------------------------------------------------------
$readme = @'
# iq-bot

WARNING: high risk

This project deals with signals/automation for **binary options** (high risk). Nothing here is a promise of profit.
The goal is engineering + statistical validation + risk control.

## What is this?

A Windows-first pipeline to:
- collect closed candles into SQLite
- generate features/datasets
- train/select models with walk-forward / pseudo-future validation
- run a very selective LIVE observer (Top-K per day + regime gate + EV gating)
- audit LIVE performance and risk via a risk report (stake sizing with conservative statistics)

Default target (can be changed in `config.yaml`):
- Asset: `EURUSD-OTC`
- Interval: `300s` (5 minutes)
- Timezone: `America/Sao_Paulo`

## Requirements

- Windows 10/11
- PowerShell 7 (`pwsh`)
- Python 3.12

## Setup (quick)

```powershell
git clone https://github.com/hyagocandido4-netizen/iq-bot.git
cd iq-bot

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env
# Edit .env with your credentials (DO NOT COMMIT)
```

## Key commands

### Observe loop (LIVE)

Run once (debug):

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop.ps1 -Once
```

### Risk report (P3)

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000
```

## Repository layout

- `src/natbin/` - Python modules
- `scripts/` - PowerShell automation (setup/scheduler/tools/patches)
- `data/` - local SQLite DBs (ignored by git)
- `runs/` - live logs / model caches / run artifacts (ignored by git)

## Notes

- This repo does not ship a license file yet. Until a license is added, default copyright applies.
- Do not commit DBs, WAL/SHM files, or credentials.
'@
Write-Utf8NoBomFile 'README.md' $readme

# -----------------------------------------------------------------------------
# 4) .gitattributes
# -----------------------------------------------------------------------------
$gitattributes = @'
# Normalize text files
* text=auto eol=lf

# Explicit text types
*.py   text eol=lf
*.ps1  text eol=lf
*.psm1 text eol=lf
*.yml  text eol=lf
*.yaml text eol=lf
*.md   text eol=lf
*.toml text eol=lf
*.cfg  text eol=lf
*.ini  text eol=lf

# Binary / large artifacts
*.sqlite3         binary
*.sqlite3-wal     binary
*.sqlite3-shm     binary
*.sqlite3-journal binary
*.db              binary
*.db-wal          binary
*.db-shm          binary
*.db-journal      binary
*.png             binary
*.jpg             binary
*.jpeg            binary
*.gif             binary
*.pdf             binary
'@
Write-Utf8NoBomFile '.gitattributes' $gitattributes

# -----------------------------------------------------------------------------
# 5) .editorconfig
# -----------------------------------------------------------------------------
$editorconfig = @'
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 4
max_line_length = 120

[*.{yml,yaml}]
indent_size = 2

[*.md]
trim_trailing_whitespace = false
'@
Write-Utf8NoBomFile '.editorconfig' $editorconfig

# -----------------------------------------------------------------------------
# 6) GitHub Actions CI (minimal)
# -----------------------------------------------------------------------------
$ci = @'
name: CI

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  ci:
    runs-on: windows-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Python syntax (compileall)
        run: |
          python -m compileall -q src/natbin

      - name: PowerShell syntax (parse)
        shell: pwsh
        run: |
          $ErrorActionPreference = 'Stop'
          $files = Get-ChildItem -Recurse -Filter *.ps1 -Path scripts
          $all = @()
          foreach ($f in $files) {
            $tokens = $null
            $errs = $null
            [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$tokens, [ref]$errs) | Out-Null
            if ($errs -and $errs.Count -gt 0) {
              foreach ($e in $errs) {
                $all += "$($f.FullName): $($e.Message) (line $($e.Extent.StartLineNumber))"
              }
            }
          }
          if ($all.Count -gt 0) {
            $all -join "`n" | Write-Error
            exit 1
          }

      - name: Hidden unicode / bidi guard
        shell: pwsh
        run: |
          $ErrorActionPreference = 'Stop'
          $pattern = '[\uFEFF\u200E\u200F\u061C\u202A-\u202E\u2066-\u2069]'
          $exts = @('*.py','*.ps1','*.md','*.yml','*.yaml','*.toml','*.cfg','*.ini','*.txt','.gitignore','.gitattributes','.editorconfig')
          $hits = @()
          foreach ($ext in $exts) {
            $hits += Get-ChildItem -Recurse -File -Filter $ext -ErrorAction SilentlyContinue |
              Select-String -Pattern $pattern -AllMatches -ErrorAction SilentlyContinue |
              ForEach-Object { "$($_.Path):$($_.LineNumber)" }
          }
          if ($hits.Count -gt 0) {
            'Found hidden/bidi unicode control characters in:' | Write-Error
            ($hits -join "`n") | Write-Error
            exit 1
          }
'@
Write-Utf8NoBomFile '.github/workflows/ci.yml' $ci

# -----------------------------------------------------------------------------
# 7) Optional: if sqlite sidecars are tracked by git, untrack them now
# -----------------------------------------------------------------------------
try {
  $tracked = @(git ls-files 2>$null | Select-String -Pattern '(?i)\.(sqlite3|db)-(wal|shm|journal)$')
  foreach ($m in $tracked) {
    git rm --cached --ignore-unmatch $m.Line | Out-Null
  }
  if ($tracked.Count -gt 0) {
    Write-Host ('Untracked sqlite sidecar(s) from git index: ' + $tracked.Count)
  }
} catch {
  # ignore if git isn't available
}

Write-Host 'P4 repo hardening applied:'
Write-Host ' - updated .gitignore (sqlite wal/shm guarded)'
Write-Host ' - added SECURITY.md, README.md, .gitattributes, .editorconfig'
Write-Host ' - added .github/workflows/ci.yml'
