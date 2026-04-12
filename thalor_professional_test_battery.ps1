param(
    [string]$RepoRoot = '.',
    [switch]$IncludeDocker,
    [switch]$IncludePractice,
    [switch]$IncludeRealPreflight,
    [string]$PracticeConfig = 'config\live_controlled_practice.yaml',
    [string]$RealConfig = 'config\live_controlled_real.yaml'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path $RepoRoot).Path
Set-Location $RepoRoot

$RunId = Get-Date -Format 'yyyyMMdd_HHmmss'
$ArtifactsRoot = Join-Path $RepoRoot "test_battery\$RunId"
New-Item -ItemType Directory -Force $ArtifactsRoot | Out-Null

function New-Phase {
    param([string]$Name)
    $Dir = Join-Path $ArtifactsRoot $Name
    New-Item -ItemType Directory -Force $Dir | Out-Null
    return $Dir
}

function Close-PhaseZip {
    param([string]$Dir)
    $Zip = "$Dir.zip"
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    $items = Get-ChildItem -LiteralPath $Dir -Force -ErrorAction SilentlyContinue
    if (-not $items) {
        Set-Content -Path (Join-Path $Dir 'empty.txt') -Value 'phase produced no files' -Encoding UTF8
    }
    Compress-Archive -Path (Join-Path $Dir '*') -DestinationPath $Zip -Force
    return $Zip
}

function Write-ExitCode {
    param([string]$Path,[int]$Code)
    Set-Content -Path $Path -Value $Code -Encoding UTF8
}



function Resolve-ShellExe {
    $Pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($Pwsh) { return $Pwsh.Source }
    $Powershell = Get-Command powershell -ErrorAction SilentlyContinue
    if ($Powershell) { return $Powershell.Source }
    throw 'Nenhum PowerShell encontrado (pwsh/powershell).'
}

function Resolve-PythonExe {
    $Local = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $Local) { return (Resolve-Path $Local).Path }
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py) {
        py -3.12 -m venv .venv
        return (Resolve-Path '.\.venv\Scripts\python.exe').Path
    }
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) {
        & $Python.Source -m venv .venv
        return (Resolve-Path '.\.venv\Scripts\python.exe').Path
    }
    throw 'Nenhum Python launcher encontrado.'
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string[]]$Command
    )
    & $Command[0] $Command[1..($Command.Length-1)] *> $LogPath
    return $LASTEXITCODE
}

function Invoke-PhaseCommand {
    param(
        [Parameter(Mandatory = $true)][string]$PhaseDir,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Command
    )
    $LogPath = Join-Path $PhaseDir "$Name.log"
    $ExitPath = Join-Path $PhaseDir "$Name.exit.txt"
    try {
        $code = Invoke-LoggedCommand -LogPath $LogPath -Command $Command
    } catch {
        $_ | Out-String | Set-Content -Path $LogPath -Encoding UTF8
        $code = 998
    }
    Write-ExitCode -Path $ExitPath -Code $code
    return [pscustomobject]@{
        name = $Name
        command = $Command
        exit_code = $code
        log = [System.IO.Path]::GetFileName($LogPath)
        exit = [System.IO.Path]::GetFileName($ExitPath)
    }
}

function Save-Summary {
    param([string]$PhaseDir, $Items)
    $Items | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $PhaseDir 'summary.json') -Encoding UTF8
}

$PythonExe = Resolve-PythonExe
$ShellExe = Resolve-ShellExe
$env:PYTHONPATH = "$((Resolve-Path .\src).Path);$env:PYTHONPATH"

# install / upgrade
$InstallPhase = New-Phase '00_install'
$installResults = @()
$installResults += Invoke-PhaseCommand -PhaseDir $InstallPhase -Name 'pip_upgrade' -Command @($PythonExe, '-m', 'pip', 'install', '-U', 'pip')
$installResults += Invoke-PhaseCommand -PhaseDir $InstallPhase -Name 'pip_install_requirements_dev' -Command @($PythonExe, '-m', 'pip', 'install', '-r', 'requirements-dev.txt')
Save-Summary -PhaseDir $InstallPhase -Items $installResults
$null = Close-PhaseZip $InstallPhase

# 01 baseline
$Phase = New-Phase '01_baseline'
$r = @()
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'python_version' -Command @($PythonExe, '-VV')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'pip_list' -Command @($PythonExe, '-m', 'pip', 'list')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'pip_check' -Command @($PythonExe, '-m', 'pip', 'check')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'git_status' -Command @('git', 'status', '--short', '--branch')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'git_head' -Command @('git', 'rev-parse', 'HEAD')
Get-ChildItem . -Force | Select-Object Name,Mode,Length | Format-Table -AutoSize | Out-File (Join-Path $Phase 'repo_root_listing.log') -Encoding utf8
Save-Summary -PhaseDir $Phase -Items $r
$null = Close-PhaseZip $Phase

# 02 static integrity
$Phase = New-Phase '02_static_integrity'
$r = @()
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'selfcheck_repo' -Command @($PythonExe, 'scripts/tools/selfcheck_repo.py')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'check_hidden_unicode' -Command @($PythonExe, 'scripts/tools/check_hidden_unicode.py')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'config_smoke' -Command @($PythonExe, 'scripts/tools/config_smoke.py')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'config_consumers_smoke' -Command @($PythonExe, 'scripts/tools/config_consumers_smoke.py')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'repo_entrypoints_smoke' -Command @($PythonExe, 'scripts/tools/repo_entrypoints_smoke.py')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'health_smoke' -Command @($PythonExe, 'scripts/tools/health_smoke.py')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'compileall' -Command @($PythonExe, '-m', 'compileall', '-q', 'src', 'tests', 'scripts')
Save-Summary -PhaseDir $Phase -Items $r
$null = Close-PhaseZip $Phase

# 03 pytest full
$Phase = New-Phase '03_pytest_full'
$r = @()
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'pytest_full' -Command @($PythonExe, '-m', 'pytest', '-ra', '-vv', '--durations=50', '--junitxml', (Join-Path $Phase 'junit_full.xml'), 'tests')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'pytest_collect_only' -Command @($PythonExe, '-m', 'pytest', '--collect-only', '-q', 'tests')
Save-Summary -PhaseDir $Phase -Items $r
$null = Close-PhaseZip $Phase

# 04 pytest isolated
$Phase = New-Phase '04_pytest_isolated'
$isolated = @()
Get-ChildItem .\tests\test_*.py | Sort-Object Name | ForEach-Object {
    $name = $_.BaseName
    $xml = Join-Path $Phase "$name.xml"
    $log = Join-Path $Phase "$name.log"
    try {
        & $PythonExe -m pytest -ra -vv $_.FullName --junitxml $xml *> $log
        $code = $LASTEXITCODE
    } catch {
        $_ | Out-String | Set-Content -Path $log -Encoding UTF8
        $code = 998
    }
    Write-ExitCode -Path (Join-Path $Phase "$name.exit.txt") -Code $code
    $isolated += [pscustomobject]@{
        test_file = $_.Name
        exit_code = $code
        log = "$name.log"
        junit = "$name.xml"
    }
}
Save-Summary -PhaseDir $Phase -Items $isolated
$null = Close-PhaseZip $Phase

# 05 all smokes
$Phase = New-Phase '05_smokes'
$smokeResults = @()
$smokes = @()
$smokes += Get-ChildItem .\scripts\tools\*_smoke.py -ErrorAction SilentlyContinue
$smokes += Get-ChildItem .\scripts\ci\smoke_*.py -ErrorAction SilentlyContinue
$smokes = $smokes | Sort-Object FullName -Unique
foreach ($s in $smokes) {
    $name = $s.BaseName
    $log = Join-Path $Phase "$name.log"
    try {
        & $PythonExe $s.FullName *> $log
        $code = $LASTEXITCODE
    } catch {
        $_ | Out-String | Set-Content -Path $log -Encoding UTF8
        $code = 998
    }
    Write-ExitCode -Path (Join-Path $Phase "$name.exit.txt") -Code $code
    $smokeResults += [pscustomobject]@{
        smoke_script = $s.FullName
        exit_code = $code
        log = "$name.log"
    }
}
Save-Summary -PhaseDir $Phase -Items $smokeResults
$null = Close-PhaseZip $Phase

# 06 control plane
$Phase = New-Phase '06_control_plane'
$controlCommands = @(
    @('status','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('plan','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('quota','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('precheck','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('healthcheck','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('health','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('security','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('protection','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('monte-carlo','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('sync','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('intelligence','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('doctor','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('retention','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('release','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('practice','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('incidents','status','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('incidents','report','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('incidents','drill','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('alerts','status','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('orders','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('execution-hardening','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('retrain','status','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('portfolio','status','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('portfolio','plan','--repo-root','.','--config','config/multi_asset.yaml','--json'),
    @('backup','--repo-root','.','--config','config/multi_asset.yaml','--json','--dry-run')
)
$controlResults = @()
$idx = 0
foreach ($args in $controlCommands) {
    $idx += 1
    $name = ('{0:D2}_{1}' -f $idx, ($args -join '_').Replace('--','').Replace('/','_').Replace('\','_').Replace(':','_'))
    $controlResults += Invoke-PhaseCommand -PhaseDir $Phase -Name $name -Command (@($PythonExe, '-m', 'natbin.runtime_app') + $args)
}
Save-Summary -PhaseDir $Phase -Items $controlResults
$null = Close-PhaseZip $Phase

# 07 local suite and soak
$Phase = New-Phase '07_local_suite_and_soak'
$r = @()
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'local_test_suite_full' -Command @($PythonExe, 'scripts/tools/local_test_suite.py', '--repo-root', '.', '--preset', 'full')
$r += Invoke-PhaseCommand -PhaseDir $Phase -Name 'runtime_soak' -Command @($PythonExe, 'scripts/tools/runtime_soak.py', '--repo-root', '.', '--config', 'config/multi_asset.yaml', '--max-cycles', '12', '--topk', '3', '--lookback-candles', '2000')
Get-ChildItem .\runs\tests\local_test_suite_*.json -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1 |
    Copy-Item -Destination (Join-Path $Phase 'local_test_suite_report.json') -Force -ErrorAction SilentlyContinue
Get-ChildItem .\runs\control -Recurse -Filter runtime_soak_summary.json -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1 |
    Copy-Item -Destination (Join-Path $Phase 'runtime_soak_summary.json') -Force -ErrorAction SilentlyContinue
Save-Summary -PhaseDir $Phase -Items $r
$null = Close-PhaseZip $Phase

# 08 offline diag
$Phase = New-Phase '08_offline_diag'
$diagResults = @()
$diagResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'offline_diag' -Command @($ShellExe, '-ExecutionPolicy', 'Bypass', '-File', '.\scripts\tools\run_thalor_diagnostics_v4_1.ps1', '-Mode', 'Offline', '-StrictWarnings', '-IncludeIsolatedPytest', '-IncludeDocker')
Get-ChildItem .\diag_zips\diag_bundle_*.zip -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1 |
    Copy-Item -Destination (Join-Path $Phase 'diag_bundle_latest.zip') -Force -ErrorAction SilentlyContinue
Get-ChildItem .\diag_zips\LATEST_SESSION.txt -ErrorAction SilentlyContinue |
    Copy-Item -Destination (Join-Path $Phase 'LATEST_SESSION.txt') -Force -ErrorAction SilentlyContinue
Save-Summary -PhaseDir $Phase -Items $diagResults
$null = Close-PhaseZip $Phase

# 09 docker
if ($IncludeDocker) {
    $Phase = New-Phase '09_docker'
    $dockerResults = @()
    $dockerResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'docker_version' -Command @('docker', 'version')
    $dockerResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'docker_compose_version' -Command @('docker', 'compose', 'version')
    $dockerResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'docker_compose_base_config' -Command @('docker', 'compose', '-f', 'docker-compose.yml', 'config')
    $dockerResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'docker_compose_vps_config' -Command @('docker', 'compose', '-f', 'docker-compose.vps.yml', 'config')
    $dockerResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'docker_compose_prod_config' -Command @('docker', 'compose', '-f', 'docker-compose.prod.yml', 'config')
    $dockerResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'docker_build' -Command @('docker', 'build', '--progress=plain', '-t', 'thalor:test', '.')
    $dockerResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'docker_pytest' -Command @('docker', 'run', '--rm', 'thalor:test', 'python', '-m', 'pytest', '-q')
    $dockerResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'docker_runtime_status' -Command @('docker', 'run', '--rm', 'thalor:test', 'python', '-m', 'natbin.runtime_app', 'status', '--repo-root', '/app', '--config', 'config/multi_asset.yaml', '--json')
    Save-Summary -PhaseDir $Phase -Items $dockerResults
    $null = Close-PhaseZip $Phase
}

# 10 practice integration
if ($IncludePractice) {
    $Phase = New-Phase '10_practice_integration'
    $practiceResults = @()
    $practiceResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'practice_diag' -Command @($ShellExe, '-ExecutionPolicy', 'Bypass', '-File', '.\scripts\tools\run_thalor_diagnostics_v4_1.ps1', '-Mode', 'Practice', '-StrictWarnings', '-PracticeConfig', $PracticeConfig)
    Get-ChildItem .\diag_zips\diag_bundle_*.zip -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1 |
        Copy-Item -Destination (Join-Path $Phase 'practice_diag_bundle_latest.zip') -Force -ErrorAction SilentlyContinue
    Save-Summary -PhaseDir $Phase -Items $practiceResults
    $null = Close-PhaseZip $Phase
}

# 11 real preflight
if ($IncludeRealPreflight) {
    $Phase = New-Phase '11_real_preflight'
    $realResults = @()
    $realResults += Invoke-PhaseCommand -PhaseDir $Phase -Name 'real_preflight_diag' -Command @($ShellExe, '-ExecutionPolicy', 'Bypass', '-File', '.\scripts\tools\run_thalor_diagnostics_v4_1.ps1', '-Mode', 'RealPreflight', '-StrictWarnings', '-RealConfig', $RealConfig)
    Get-ChildItem .\diag_zips\diag_bundle_*.zip -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1 |
        Copy-Item -Destination (Join-Path $Phase 'real_preflight_diag_bundle_latest.zip') -Force -ErrorAction SilentlyContinue
    Save-Summary -PhaseDir $Phase -Items $realResults
    $null = Close-PhaseZip $Phase
}

$summary = [pscustomobject]@{
    run_id = $RunId
    repo_root = $RepoRoot
    artifacts_root = $ArtifactsRoot
    include_docker = [bool]$IncludeDocker
    include_practice = [bool]$IncludePractice
    include_real_preflight = [bool]$IncludeRealPreflight
    generated_at = (Get-Date).ToString('s')
    zip_files = (Get-ChildItem $ArtifactsRoot -Filter *.zip | Sort-Object Name | Select-Object -ExpandProperty FullName)
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $ArtifactsRoot 'battery_summary.json') -Encoding UTF8

Write-Host ""
Write-Host "Concluído."
Write-Host "Artifacts root: $ArtifactsRoot"
Get-ChildItem $ArtifactsRoot -Filter *.zip | Sort-Object Name | ForEach-Object {
    Write-Host "ZIP:" $_.FullName
}
