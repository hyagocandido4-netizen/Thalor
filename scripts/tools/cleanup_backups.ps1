# scripts/tools/cleanup_backups.ps1
#
# Remove arquivos de backup criados automaticamente por scripts/patches
# Ex.: *.bak_YYYYMMDD_HHMMSS
#
# Uso:
#   pwsh -ExecutionPolicy Bypass -File .\scripts\tools\cleanup_backups.ps1
#
# Segurança:
#  - NÃO remove nada em data/ ou runs/.
#  - Só remove padrões conhecidos de backup.

$ErrorActionPreference = 'Stop'

# scripts/tools -> repo root (2 níveis acima)
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

$patterns = @(
  '*.bak_*',
  '*.orig',
  '*.rej'
)

$deleted = 0

# 1) limpa arquivos de backup na RAIZ do repo (não-recursivo)
foreach ($pat in $patterns) {
  Get-ChildItem -Path $repoRoot -File -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object {
    try {
      Remove-Item -LiteralPath $_.FullName -Force
      $deleted += 1
    } catch {
      Write-Warning "falha ao remover: $($_.FullName) -> $($_.Exception.Message)"
    }
  }
}

# 2) limpa recursivamente apenas em src/ e scripts/
$targets = @(
  Join-Path $repoRoot 'src',
  Join-Path $repoRoot 'scripts'
)

foreach ($base in $targets) {
  if (-not (Test-Path $base)) { continue }
  foreach ($pat in $patterns) {
    Get-ChildItem -Path $base -Recurse -File -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object {
      try {
        Remove-Item -LiteralPath $_.FullName -Force
        $deleted += 1
      } catch {
        Write-Warning "falha ao remover: $($_.FullName) -> $($_.Exception.Message)"
      }
    }
  }
}

Write-Host "[cleanup_backups] deleted=$deleted"
