param(
    [string]$RepoRoot = '.',
    [ValidateSet('Offline','Practice','RealPreflight','RealSubmit','All')]
    [string]$Mode = 'Offline',
    [int]$DashboardSeconds = 45,
    [switch]$IncludeIsolatedPytest,
    [switch]$IncludeDocker,
    [switch]$StopOnRequiredFailure,
    [string]$PracticeConfig = 'config\live_controlled_practice.yaml',
    [string]$RealConfig = 'config\live_controlled_real.yaml',
    [switch]$AllowLiveSubmit,
    [string]$LiveAck = '',
    [switch]$StrictWarnings
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path $RepoRoot).Path
$ArtifactsRoot = Join-Path $RepoRoot 'runs\diag_zips'
New-Item -ItemType Directory -Force $ArtifactsRoot | Out-Null

$script:Results = New-Object System.Collections.ArrayList
$script:Failures = New-Object System.Collections.ArrayList

function Get-RepoRelativePath {
    param([Parameter(Mandatory = $true)][string]$FullName)
    $repoUri = [System.Uri]::new(($RepoRoot.TrimEnd('\\') + '\\'))
    $fileUri = [System.Uri]::new([System.IO.Path]::GetFullPath($FullName))
    return [System.Uri]::UnescapeDataString($repoUri.MakeRelativeUri($fileUri).ToString()).Replace('/', '\\')
}

function Resolve-PythonLauncher {
    $local = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $local) {
        return @{
            FilePath = $local
            BaseArgs = @()
            Display  = $local
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            FilePath = $py.Source
            BaseArgs = @('-3.12')
            Display  = 'py -3.12'
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            FilePath = $python.Source
            BaseArgs = @()
            Display  = $python.Source
        }
    }

    throw 'Nenhum launcher Python encontrado (.venv, py ou python).'
}

function Resolve-PreferredShell {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwsh) { return $pwsh.Source }
    $powershell = Get-Command powershell -ErrorAction SilentlyContinue
    if ($powershell) { return $powershell.Source }
    return $null
}

function Test-ExternalAvailable {
    param([Parameter(Mandatory = $true)][string]$FilePath)
    if ([System.IO.Path]::IsPathRooted($FilePath)) {
        return (Test-Path $FilePath)
    }
    return [bool](Get-Command $FilePath -ErrorAction SilentlyContinue)
}

function Test-SafeArtifactPath {
    param([Parameter(Mandatory = $true)][string]$FullName)

    $full = [System.IO.Path]::GetFullPath($FullName)
    $blockedExact = @(
        [System.IO.Path]::GetFullPath((Join-Path $RepoRoot '.env')),
        [System.IO.Path]::GetFullPath((Join-Path $RepoRoot 'config\broker_secrets.yaml'))
    )

    if ($blockedExact -contains $full) { return $false }
    if ($full -match '[\\/]\.env(\..+)?$') { return $false }
    if ($full -match '[\\/]broker_secrets\.ya?ml$') { return $false }
    if ($full -match '[\\/]__pycache__[\\/]') { return $false }
    if ($full -match '[\\/]\.git[\\/]') { return $false }
    if ($full -match '[\\/]\.venv[\\/]') { return $false }
    if ($full -match '[\\/]runs[\\/]diag_zips[\\/]') { return $false }
    if ($full -match '[\\/]runs[\\/]backups[\\/]') { return $false }
    if ($full -match '[\\/]data[\\/].+\.sqlite3(-wal|-shm)?$') { return $false }
    if ($full -match '[\\/]\.mypy_cache[\\/]') { return $false }
    if ($full -match '[\\/]\.ruff_cache[\\/]') { return $false }
    if ($full -match '[\\/]\.hypothesis[\\/]') { return $false }

    return $true
}

function Get-ExpandedEntries {
    param([string[]]$InputPaths)
    $entries = New-Object System.Collections.ArrayList
    foreach ($inputPath in ($InputPaths | Where-Object { $_ })) {
        $candidate = $inputPath
        if (-not [System.IO.Path]::IsPathRooted($candidate)) {
            $candidate = Join-Path $RepoRoot $candidate
        }
        $items = Get-ChildItem -Path $candidate -Force -ErrorAction SilentlyContinue
        foreach ($item in $items) {
            [void]$entries.Add($item)
        }
    }
    return $entries
}

function Get-FileSnapshot {
    param([string[]]$TrackRoots)
    $snapshot = @{}
    $entries = Get-ExpandedEntries -InputPaths $TrackRoots
    foreach ($entry in $entries) {
        if ($entry.PSIsContainer) {
            $files = Get-ChildItem -LiteralPath $entry.FullName -Recurse -File -Force -ErrorAction SilentlyContinue
        }
        else {
            $files = @($entry)
        }
        foreach ($file in $files) {
            if (-not (Test-SafeArtifactPath $file.FullName)) { continue }
            $rel = Get-RepoRelativePath $file.FullName
            $snapshot[$rel] = [pscustomobject]@{
                Length = $file.Length
                Ticks  = $file.LastWriteTimeUtc.Ticks
            }
        }
    }
    return $snapshot
}

function Get-ChangedRelativePaths {
    param($Before, $After)
    $changed = New-Object System.Collections.ArrayList
    foreach ($key in $After.Keys) {
        if (-not $Before.ContainsKey($key)) {
            [void]$changed.Add($key)
            continue
        }
        if (($After[$key].Ticks -ne $Before[$key].Ticks) -or ($After[$key].Length -ne $Before[$key].Length)) {
            [void]$changed.Add($key)
        }
    }
    return $changed
}

function Copy-FileToStage {
    param(
        [Parameter(Mandatory = $true)][string]$SourceFullName,
        [Parameter(Mandatory = $true)][string]$StagePayloadRoot
    )
    if (-not (Test-Path $SourceFullName)) { return }
    if (-not (Test-SafeArtifactPath $SourceFullName)) { return }
    $rel = Get-RepoRelativePath $SourceFullName
    $dest = Join-Path $StagePayloadRoot $rel
    New-Item -ItemType Directory -Force (Split-Path $dest -Parent) | Out-Null
    Copy-Item -LiteralPath $SourceFullName -Destination $dest -Force
}

function Copy-AlwaysIncludePaths {
    param(
        [string[]]$AlwaysInclude,
        [string]$StagePayloadRoot
    )
    $entries = Get-ExpandedEntries -InputPaths $AlwaysInclude
    foreach ($entry in $entries) {
        if ($entry.PSIsContainer) {
            Get-ChildItem -LiteralPath $entry.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
                Copy-FileToStage -SourceFullName $_.FullName -StagePayloadRoot $StagePayloadRoot
            }
        }
        else {
            Copy-FileToStage -SourceFullName $entry.FullName -StagePayloadRoot $StagePayloadRoot
        }
    }
}

function New-ZipFromStage {
    param(
        [Parameter(Mandatory = $true)][string]$StageDir,
        [Parameter(Mandatory = $true)][string]$ZipPath
    )
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($StageDir, $ZipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
}

function Join-CommandText {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    $parts = New-Object System.Collections.ArrayList
    [void]$parts.Add($FilePath)
    foreach ($arg in ($Arguments | Where-Object { $_ -ne $null })) {
        if ($arg -match '[\s"]') {
            [void]$parts.Add(('"' + ($arg -replace '"', '\\"') + '"'))
        }
        else {
            [void]$parts.Add($arg)
        }
    }
    return ($parts -join ' ')
}

function Write-SkippedStep {
    param(
        [string]$Name,
        [string]$Reason,
        [string]$CommandText,
        [bool]$Optional,
        [string[]]$AlwaysInclude = @()
    )
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $safeName = ($Name -replace '[^A-Za-z0-9._-]', '_')
    $stepRoot = Join-Path $ArtifactsRoot ($stamp + '_' + $safeName)
    $stageDir = Join-Path $stepRoot 'stage'
    $ctxDir = Join-Path $stageDir 'context'
    $payloadDir = Join-Path $stageDir 'payload'
    New-Item -ItemType Directory -Force $ctxDir | Out-Null
    New-Item -ItemType Directory -Force $payloadDir | Out-Null

    $meta = [pscustomobject]@{
        name       = $Name
        status     = 'skipped'
        optional   = $Optional
        reason     = $Reason
        command    = $CommandText
        started_at = (Get-Date).ToString('o')
        ended_at   = (Get-Date).ToString('o')
        exit_code  = $null
    }
    $meta | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $ctxDir 'meta.json') -Encoding UTF8
    Set-Content (Join-Path $ctxDir 'combined.log') $Reason -Encoding UTF8
    Set-Content (Join-Path $ctxDir 'command.txt') $CommandText -Encoding UTF8
    Copy-AlwaysIncludePaths -AlwaysInclude $AlwaysInclude -StagePayloadRoot $payloadDir

    $zipPath = Join-Path $ArtifactsRoot ($stamp + '_' + $safeName + '.zip')
    New-ZipFromStage -StageDir $stageDir -ZipPath $zipPath

    $row = [pscustomobject]@{ name = $Name; status = 'skipped'; optional = $Optional; zip = $zipPath; exit_code = $null }
    [void]$script:Results.Add($row)
    Write-Host ("SKIP -> {0} | {1}" -f $Name, $zipPath) -ForegroundColor Yellow
}

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string[]]$TrackRoots = @(),
        [string[]]$AlwaysInclude = @(),
        [bool]$Optional = $false,
        [int]$TimeoutSec = 0
    )

    $commandText = Join-CommandText -FilePath $FilePath -Arguments $Arguments
    if (-not (Test-ExternalAvailable $FilePath)) {
        Write-SkippedStep -Name $Name -Reason ("Executável não encontrado: {0}" -f $FilePath) -CommandText $commandText -Optional $Optional -AlwaysInclude $AlwaysInclude
        return
    }

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $safeName = ($Name -replace '[^A-Za-z0-9._-]', '_')
    $stepRoot = Join-Path $ArtifactsRoot ($stamp + '_' + $safeName)
    $stageDir = Join-Path $stepRoot 'stage'
    $ctxDir = Join-Path $stageDir 'context'
    $payloadDir = Join-Path $stageDir 'payload'
    New-Item -ItemType Directory -Force $ctxDir | Out-Null
    New-Item -ItemType Directory -Force $payloadDir | Out-Null

    $stdoutFile = Join-Path $ctxDir 'stdout.log'
    $stderrFile = Join-Path $ctxDir 'stderr.log'
    $combinedLog = Join-Path $ctxDir 'combined.log'
    $commandFile = Join-Path $ctxDir 'command.txt'
    $metaFile = Join-Path $ctxDir 'meta.json'

    Set-Content $commandFile $commandText -Encoding UTF8

    $before = Get-FileSnapshot -TrackRoots $TrackRoots
    $start = Get-Date
    $timedOut = $false
    $exitCode = 1

    try {
        $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $RepoRoot -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
        if ($TimeoutSec -gt 0) {
            if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
                $timedOut = $true
                try {
                    & taskkill /PID $proc.Id /T /F | Out-Null
                }
                catch {
                    try { $proc.Kill() } catch {}
                }
                Start-Sleep -Seconds 1
            }
            else {
                $exitCode = $proc.ExitCode
            }
        }
        else {
            $proc.WaitForExit()
            $exitCode = $proc.ExitCode
        }
    }
    catch {
        $_ | Out-String | Set-Content $stderrFile -Encoding UTF8
        $exitCode = 1
    }

    if ($timedOut) { $exitCode = 124 }

    $stdoutText = if (Test-Path $stdoutFile) { Get-Content $stdoutFile -Raw } else { '' }
    $stderrText = if (Test-Path $stderrFile) { Get-Content $stderrFile -Raw } else { '' }
    $combined = @(
        '=== COMMAND ===',
        $commandText,
        '',
        '=== STDOUT ===',
        $stdoutText,
        '',
        '=== STDERR ===',
        $stderrText
    ) -join [Environment]::NewLine
    Set-Content $combinedLog $combined -Encoding UTF8

    $after = Get-FileSnapshot -TrackRoots $TrackRoots
    $changed = Get-ChangedRelativePaths -Before $before -After $after
    foreach ($rel in $changed) {
        Copy-FileToStage -SourceFullName (Join-Path $RepoRoot $rel) -StagePayloadRoot $payloadDir
    }
    Copy-AlwaysIncludePaths -AlwaysInclude $AlwaysInclude -StagePayloadRoot $payloadDir

    $meta = [pscustomobject]@{
        name       = $Name
        status     = if ($exitCode -eq 0) { 'ok' } elseif ($Optional) { 'warn' } else { 'fail' }
        optional   = $Optional
        command    = $commandText
        started_at = $start.ToString('o')
        ended_at   = (Get-Date).ToString('o')
        exit_code  = $exitCode
        timeout    = $TimeoutSec
        timed_out  = $timedOut
        track_roots = $TrackRoots
        always_include = $AlwaysInclude
    }
    $meta | ConvertTo-Json -Depth 8 | Set-Content $metaFile -Encoding UTF8

    $zipPath = Join-Path $ArtifactsRoot ($stamp + '_' + $safeName + '.zip')
    New-ZipFromStage -StageDir $stageDir -ZipPath $zipPath

    $row = [pscustomobject]@{ name = $Name; status = $meta.status; optional = $Optional; zip = $zipPath; exit_code = $exitCode }
    [void]$script:Results.Add($row)

    if ($exitCode -ne 0 -and -not $Optional) {
        [void]$script:Failures.Add($row)
        Write-Warning ("FAIL -> {0} | {1}" -f $Name, $zipPath)
        if ($StopOnRequiredFailure) {
            throw ("Step obrigatório falhou: {0}" -f $Name)
        }
    }
    elseif ($exitCode -ne 0 -and $Optional) {
        Write-Warning ("WARN -> {0} | {1}" -f $Name, $zipPath)
    }
    else {
        Write-Host ("OK -> {0} | {1}" -f $Name, $zipPath) -ForegroundColor Green
    }
}

function Invoke-PowerShellStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock,
        [string]$CommandText,
        [string[]]$TrackRoots = @(),
        [string[]]$AlwaysInclude = @(),
        [bool]$Optional = $false
    )

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $safeName = ($Name -replace '[^A-Za-z0-9._-]', '_')
    $stepRoot = Join-Path $ArtifactsRoot ($stamp + '_' + $safeName)
    $stageDir = Join-Path $stepRoot 'stage'
    $ctxDir = Join-Path $stageDir 'context'
    $payloadDir = Join-Path $stageDir 'payload'
    New-Item -ItemType Directory -Force $ctxDir | Out-Null
    New-Item -ItemType Directory -Force $payloadDir | Out-Null

    $combinedLog = Join-Path $ctxDir 'combined.log'
    $commandFile = Join-Path $ctxDir 'command.txt'
    $metaFile = Join-Path $ctxDir 'meta.json'
    Set-Content $commandFile $CommandText -Encoding UTF8

    $before = Get-FileSnapshot -TrackRoots $TrackRoots
    $start = Get-Date
    $exitCode = 0

    try {
        & $ScriptBlock *>&1 | Out-File -FilePath $combinedLog -Encoding UTF8
    }
    catch {
        $_ | Out-String | Out-File -FilePath $combinedLog -Encoding UTF8 -Append
        $exitCode = 1
    }

    $after = Get-FileSnapshot -TrackRoots $TrackRoots
    $changed = Get-ChangedRelativePaths -Before $before -After $after
    foreach ($rel in $changed) {
        Copy-FileToStage -SourceFullName (Join-Path $RepoRoot $rel) -StagePayloadRoot $payloadDir
    }
    Copy-AlwaysIncludePaths -AlwaysInclude $AlwaysInclude -StagePayloadRoot $payloadDir

    $meta = [pscustomobject]@{
        name       = $Name
        status     = if ($exitCode -eq 0) { 'ok' } elseif ($Optional) { 'warn' } else { 'fail' }
        optional   = $Optional
        command    = $CommandText
        started_at = $start.ToString('o')
        ended_at   = (Get-Date).ToString('o')
        exit_code  = $exitCode
        track_roots = $TrackRoots
        always_include = $AlwaysInclude
    }
    $meta | ConvertTo-Json -Depth 8 | Set-Content $metaFile -Encoding UTF8

    $zipPath = Join-Path $ArtifactsRoot ($stamp + '_' + $safeName + '.zip')
    New-ZipFromStage -StageDir $stageDir -ZipPath $zipPath

    $row = [pscustomobject]@{ name = $Name; status = $meta.status; optional = $Optional; zip = $zipPath; exit_code = $exitCode }
    [void]$script:Results.Add($row)

    if ($exitCode -ne 0 -and -not $Optional) {
        [void]$script:Failures.Add($row)
        Write-Warning ("FAIL -> {0} | {1}" -f $Name, $zipPath)
        if ($StopOnRequiredFailure) {
            throw ("Step obrigatório falhou: {0}" -f $Name)
        }
    }
    elseif ($exitCode -ne 0 -and $Optional) {
        Write-Warning ("WARN -> {0} | {1}" -f $Name, $zipPath)
    }
    else {
        Write-Host ("OK -> {0} | {1}" -f $Name, $zipPath) -ForegroundColor Green
    }
}

function Write-SummaryFile {
    $summaryPath = Join-Path $ArtifactsRoot 'SUMMARY.json'
    $payload = [pscustomobject]@{
        generated_at = (Get-Date).ToString('o')
        repo_root    = $RepoRoot
        artifact_root = $ArtifactsRoot
        total        = $script:Results.Count
        failures     = $script:Failures.Count
        results      = $script:Results
    }
    $payload | ConvertTo-Json -Depth 8 | Set-Content $summaryPath -Encoding UTF8
    Write-Host ("SUMMARY -> {0}" -f $summaryPath) -ForegroundColor Cyan
}

$python = Resolve-PythonLauncher
$shellExe = Resolve-PreferredShell
$dockerExe = (Get-Command docker -ErrorAction SilentlyContinue)

$srcPath = Join-Path $RepoRoot 'src'
if ($env:PYTHONPATH) {
    if (-not ($env:PYTHONPATH -split [System.IO.Path]::PathSeparator | Where-Object { $_ -eq $srcPath })) {
        $env:PYTHONPATH = $srcPath + [System.IO.Path]::PathSeparator + $env:PYTHONPATH
    }
}
else {
    $env:PYTHONPATH = $srcPath
}

$CommonTrack = @('runs', 'logs', 'reports', 'coverage.xml', 'htmlcov', '.pytest_cache')
$BaseConfig = @('config\base.yaml')
$MultiConfig = @('config\multi_asset.yaml')

if ($Mode -in @('Offline','All')) {
Invoke-PowerShellStep -Name '00_env_bootstrap' -CommandText 'Bootstrap local environment snapshot' -TrackRoots $CommonTrack -AlwaysInclude @('pyproject.toml', 'requirements.txt', 'requirements-dev.txt', 'requirements-dashboard.txt', 'pytest.ini', 'README.md') -ScriptBlock {
        Write-Output ("RepoRoot=" + $RepoRoot)
        Write-Output ("PYTHONPATH=" + $env:PYTHONPATH)
        Write-Output ("PythonDisplay=" + $python.Display)
        if (Test-Path (Join-Path $RepoRoot '.env.example')) { Write-Output '.env.example present' }
        if ((-not (Test-Path (Join-Path $RepoRoot '.env'))) -and (Test-Path (Join-Path $RepoRoot '.env.example'))) {
            Copy-Item (Join-Path $RepoRoot '.env.example') (Join-Path $RepoRoot '.env') -Force
            Write-Output 'Created local .env from .env.example'
        }
        if ((-not (Test-Path (Join-Path $RepoRoot 'config\broker_secrets.yaml'))) -and (Test-Path (Join-Path $RepoRoot 'config\broker_secrets.yaml.example'))) {
            Copy-Item (Join-Path $RepoRoot 'config\broker_secrets.yaml.example') (Join-Path $RepoRoot 'config\broker_secrets.yaml') -Force
            Write-Output 'Created config\\broker_secrets.yaml from example'
        }
        New-Item -ItemType Directory -Force (Join-Path $RepoRoot 'runs\tests') | Out-Null
    }
    
    Invoke-NativeStep -Name '01_python_version' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('--version')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '02_pip_check' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pip','check')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '03_compileall' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','compileall','-q','src','tests','scripts')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '04_pytest_collect' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','--collect-only','-q')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '05_selfcheck_repo' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/selfcheck_repo.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '06_hidden_unicode' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/check_hidden_unicode.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '07_leak_check' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.leak_check')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '08_release_hygiene_smoke' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/release_hygiene_smoke.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '09_release_hygiene_dryrun' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.release_hygiene','--repo-root','.','--dry-run','--json')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    
    $pytestStrictArgs = @('-m','pytest','-q','-ra','--durations=25')
    if ($StrictWarnings) { $pytestStrictArgs += @('-W','error') }
    Invoke-NativeStep -Name '10_pytest_full' -FilePath $python.FilePath -Arguments ($python.BaseArgs + $pytestStrictArgs) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '11_pytest_final_fix_config' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q','tests\test_final_fix_config.py') + ($(if ($StrictWarnings) { @('-W','error') } else { @() }))) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '12_pytest_final_fix_sklearn' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q','tests\test_final_fix_sklearn_warnings.py') + ($(if ($StrictWarnings) { @('-W','error') } else { @() }))) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    $intelligenceTestFiles = @(Get-ChildItem -Path (Join-Path $RepoRoot 'tests\test_intelligence_*.py') -File | Sort-Object Name | ForEach-Object { $_.FullName })
    if ($intelligenceTestFiles.Count -gt 0) {
        Invoke-NativeStep -Name '13_pytest_intelligence' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q') + $intelligenceTestFiles + ($(if ($StrictWarnings) { @('-W','error') } else { @() }))) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    }
    else {
        Write-SkippedStep -Name '13_pytest_intelligence' -Reason 'Nenhum arquivo tests\test_intelligence_*.py encontrado.' -CommandText 'pytest intelligence' -Optional $false -AlwaysInclude @()
    }
    Invoke-NativeStep -Name '14_pytest_multi_exec_dashboard' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q','tests\test_multi_asset_package_2.py','tests\test_execution_layer_21.py','tests\test_dashboard_package_3.py') + ($(if ($StrictWarnings) { @('-W','error') } else { @() }))) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    
    if ($IncludeIsolatedPytest) {
        Get-ChildItem -Path (Join-Path $RepoRoot 'tests\test_*.py') -File | Sort-Object Name | ForEach-Object {
            $testPath = $_.FullName
            $testName = '15_single_' + $_.BaseName
            Invoke-NativeStep -Name $testName -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q',$testPath) + ($(if ($StrictWarnings) { @('-W','error') } else { @() }))) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
        }
    }
    
    Invoke-NativeStep -Name '16_local_suite_full' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/local_test_suite.py','--repo-root','.','--preset','full','--include-soak')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '17_smoke_runtime_app' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/ci/smoke_runtime_app.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '18_smoke_execution_layer' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/ci/smoke_execution_layer.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    
    Get-ChildItem -Path (Join-Path $RepoRoot 'scripts\tools\*_smoke.py') -File | Sort-Object Name | ForEach-Object {
        $smokePath = $_.FullName
        $smokeName = '19_smoke_' + $_.BaseName
        Invoke-NativeStep -Name $smokeName -FilePath $python.FilePath -Arguments ($python.BaseArgs + @($smokePath)) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    }
    
    Invoke-NativeStep -Name '20_rt_status' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','status','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '21_rt_plan' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','plan','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '22_rt_quota' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','quota','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '23_rt_precheck' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','precheck','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '24_rt_healthcheck' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','healthcheck','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '25_rt_health' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','health','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '26_rt_security' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','security','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '27_rt_protection' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','protection','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '28_rt_doctor' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','doctor','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '29_rt_release' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','release','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '30_rt_sync' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','sync','--repo-root','.','--json')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    Invoke-NativeStep -Name '31_rt_incidents_status' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','incidents','status','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '32_rt_incidents_drill' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','incidents','drill','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '33_rt_alerts_status' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','alerts','status','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '34_rt_orders' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','orders','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false
    Invoke-NativeStep -Name '35_runtime_health_report' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/runtime_health_report.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false
    
    Invoke-NativeStep -Name '36_pf_status' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','portfolio','status','--repo-root','.','--config','config/multi_asset.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $MultiConfig -Optional $false
    Invoke-NativeStep -Name '37_pf_plan' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','portfolio','plan','--repo-root','.','--config','config/multi_asset.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $MultiConfig -Optional $false
    Invoke-NativeStep -Name '38_pf_observe' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','portfolio','observe','--repo-root','.','--config','config/multi_asset.yaml','--once','--topk','3','--lookback-candles','2000','--json')) -TrackRoots $CommonTrack -AlwaysInclude $MultiConfig -Optional $false
    
    if ($shellExe) {
        Invoke-NativeStep -Name '39_dashboard_45s' -FilePath $shellExe -Arguments @('-NoProfile','-ExecutionPolicy','Bypass','-File', (Join-Path $RepoRoot 'scripts\tools\run_dashboard.ps1'), '-RepoRoot', '.', '-Config', 'config/multi_asset.yaml', '-Port', '8501') -TrackRoots $CommonTrack -AlwaysInclude $MultiConfig -Optional $true -TimeoutSec $DashboardSeconds
    }
    else {
        Write-SkippedStep -Name '39_dashboard_45s' -Reason 'Nenhum shell PowerShell encontrado (pwsh/powershell).' -CommandText 'run_dashboard.ps1' -Optional $true -AlwaysInclude $MultiConfig
    }
    
    if ($IncludeDocker) {
        if ($dockerExe) {
            Invoke-NativeStep -Name '40_docker_build' -FilePath $dockerExe.Source -Arguments @('build','-t','thalor:ci','.') -TrackRoots $CommonTrack -AlwaysInclude @('Dockerfile','docker-compose.yml','docker-compose.prod.yml','docker-compose.vps.yml') -Optional $true
            Invoke-NativeStep -Name '41_compose_base' -FilePath $dockerExe.Source -Arguments @('compose','-f','docker-compose.yml','config') -TrackRoots $CommonTrack -AlwaysInclude @('docker-compose.yml') -Optional $true
            Invoke-NativeStep -Name '42_compose_prod' -FilePath $dockerExe.Source -Arguments @('compose','-f','docker-compose.yml','-f','docker-compose.prod.yml','config') -TrackRoots $CommonTrack -AlwaysInclude @('docker-compose.yml','docker-compose.prod.yml') -Optional $true
            Invoke-NativeStep -Name '43_compose_vps' -FilePath $dockerExe.Source -Arguments @('compose','-f','docker-compose.yml','-f','docker-compose.vps.yml','config') -TrackRoots $CommonTrack -AlwaysInclude @('docker-compose.yml','docker-compose.vps.yml') -Optional $true
        }
        else {
            Write-SkippedStep -Name '40_docker_bundle' -Reason 'Docker não encontrado.' -CommandText 'docker build/compose' -Optional $true -AlwaysInclude @('Dockerfile','docker-compose.yml','docker-compose.prod.yml','docker-compose.vps.yml')
        }
    }
    
    }

if ($Mode -in @('Practice','All')) {
    Invoke-PowerShellStep -Name '50_prepare_practice_config' -CommandText 'Ensure practice config exists' -TrackRoots $CommonTrack -AlwaysInclude @('config\live_controlled_practice.yaml.example') -ScriptBlock {
        $target = Join-Path $RepoRoot $PracticeConfig
        if (-not (Test-Path $target)) {
            Copy-Item (Join-Path $RepoRoot 'config\live_controlled_practice.yaml.example') $target -Force
            Write-Output ('Created ' + $target)
        }
        if (Test-Path (Join-Path $RepoRoot 'config\broker_secrets.yaml')) {
            $env:THALOR_SECRETS_FILE = (Join-Path $RepoRoot 'config\broker_secrets.yaml')
            Write-Output ('THALOR_SECRETS_FILE=' + $env:THALOR_SECRETS_FILE)
        }
    }
    Invoke-NativeStep -Name '51_live_validation_baseline' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_live_validation.py','--repo-root','.','--stage','baseline')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false
    Invoke-NativeStep -Name '52_live_validation_practice' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_live_validation.py','--repo-root','.','--stage','practice','--config',$PracticeConfig)) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false
    Invoke-NativeStep -Name '53_runtime_soak_practice' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/runtime_soak.py','--repo-root','.','--config',$PracticeConfig,'--max-cycles','3')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false
    Invoke-NativeStep -Name '54_rt_practice' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','practice','--repo-root','.','--config',$PracticeConfig,'--json')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false
    Invoke-NativeStep -Name '55_rt_practice_bootstrap' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','practice-bootstrap','--repo-root','.','--config',$PracticeConfig,'--json')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false
    Invoke-NativeStep -Name '56_rt_practice_round' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','practice-round','--repo-root','.','--config',$PracticeConfig,'--json')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false
    Invoke-NativeStep -Name '57_controlled_practice_round' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_practice_round.py','--repo-root','.','--config',$PracticeConfig,'--json')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false
}

if ($Mode -in @('RealPreflight','All')) {
    Invoke-PowerShellStep -Name '60_prepare_real_config' -CommandText 'Ensure real preflight config exists' -TrackRoots $CommonTrack -AlwaysInclude @('config\live_controlled_real.yaml.example') -ScriptBlock {
        $target = Join-Path $RepoRoot $RealConfig
        if (-not (Test-Path $target)) {
            Copy-Item (Join-Path $RepoRoot 'config\live_controlled_real.yaml.example') $target -Force
            Write-Output ('Created ' + $target)
        }
        if (Test-Path (Join-Path $RepoRoot 'config\broker_secrets.yaml')) {
            $env:THALOR_SECRETS_FILE = (Join-Path $RepoRoot 'config\broker_secrets.yaml')
            Write-Output ('THALOR_SECRETS_FILE=' + $env:THALOR_SECRETS_FILE)
        }
    }
    Invoke-NativeStep -Name '61_live_validation_real_preflight' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_live_validation.py','--repo-root','.','--stage','real_preflight','--config',$RealConfig)) -TrackRoots $CommonTrack -AlwaysInclude @($RealConfig) -Optional $false
}

if ($Mode -eq 'RealSubmit') {
    if (-not $AllowLiveSubmit) {
        throw 'Modo RealSubmit exige -AllowLiveSubmit.'
    }
    if ($LiveAck -ne 'I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT') {
        throw 'Modo RealSubmit exige -LiveAck I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT.'
    }
    Invoke-NativeStep -Name '70_live_validation_real_submit' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_live_validation.py','--repo-root','.','--stage','real_submit','--config',$RealConfig,'--allow-live-submit','--ack-live',$LiveAck)) -TrackRoots $CommonTrack -AlwaysInclude @($RealConfig) -Optional $false
}

Write-SummaryFile
Get-ChildItem -Path $ArtifactsRoot -Filter *.zip | Sort-Object Name | Select-Object Name, Length, LastWriteTime
