$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Enable-RepoPythonPath {
  param([Parameter(Mandatory = $true)][string]$RepoRoot)

  $srcPath = Join-Path $RepoRoot 'src'
  if (-not (Test-Path $srcPath)) {
    return
  }

  $sep = [System.IO.Path]::PathSeparator
  $entries = New-Object System.Collections.Generic.List[string]
  $entries.Add($srcPath) | Out-Null

  if ($env:PYTHONPATH) {
    foreach ($item in ($env:PYTHONPATH -split [regex]::Escape([string]$sep))) {
      if (-not [string]::IsNullOrWhiteSpace($item)) {
        $entries.Add($item.Trim()) | Out-Null
      }
    }
  }

  $env:PYTHONPATH = ($entries | Select-Object -Unique) -join $sep
}

function Invoke-RepoPythonModule {
  param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Module,
    [string[]]$ModuleArgs = @(),
    [string]$Python = ''
  )

  Enable-RepoPythonPath -RepoRoot $RepoRoot

  if ($Python) {
    & $Python -m $Module @ModuleArgs
    return
  }

  $venvWin = Join-Path $RepoRoot '.venv\Scripts\python.exe'
  if (Test-Path $venvWin) {
    & $venvWin -m $Module @ModuleArgs
    return
  }

  $venvPosix = Join-Path $RepoRoot '.venv/bin/python'
  if (Test-Path $venvPosix) {
    & $venvPosix -m $Module @ModuleArgs
    return
  }

  $pyCmd = Get-Command py -ErrorAction SilentlyContinue
  if ($pyCmd) {
    & py -3 -m $Module @ModuleArgs
    return
  }

  & python -m $Module @ModuleArgs
}
