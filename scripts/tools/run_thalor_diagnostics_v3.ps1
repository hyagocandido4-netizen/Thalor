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
    [switch]$StrictWarnings,
    [switch]$CleanBeforeEachPhase = $true,
    [switch]$CreateAggregateBundle = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path $RepoRoot).Path
$SessionId = Get-Date -Format 'yyyyMMdd_HHmmss'
$ArtifactsParent = Join-Path $RepoRoot 'diag_zips'
$ArtifactsRoot = Join-Path $ArtifactsParent $SessionId
New-Item -ItemType Directory -Force $ArtifactsRoot | Out-Null

$script:Results = New-Object System.Collections.ArrayList
$script:Failures = New-Object System.Collections.ArrayList
$script:Warnings = New-Object System.Collections.ArrayList
$script:StepIndex = 0

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
    if ($full -match '[\\/]\.env(\..+)?$' -and $full -notmatch '[\\/]\.env\.example$') { return $false }
    if ($full -match '[\\/]broker_secrets\.ya?ml$') { return $false }
    if ($full -match '[\\/]__pycache__[\\/]') { return $false }
    if ($full -match '[\\/]\.git[\\/]') { return $false }
    if ($full -match '[\\/]\.venv[\\/]') { return $false }
    if ($full -match '[\\/]diag_zips[\\/]') { return $false }
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
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

    $zip = [System.IO.Compression.ZipFile]::Open($ZipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        Get-ChildItem -LiteralPath $StageDir -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($StageDir.TrimEnd('\\').Length).TrimStart('\\')
            $entryName = $rel -replace '\\', '/'
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $entryName, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    }
    finally {
        $zip.Dispose()
    }
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

function Get-StepOrdinalName {
    param([string]$Name)
    $script:StepIndex += 1
    return ('{0:D3}_{1}' -f $script:StepIndex, $Name)
}

function Get-StatusRank {
    param([string]$Status)
    switch ($Status) {
        'ok' { return 0 }
        'warn' { return 1 }
        'fail' { return 2 }
        'skipped' { return 0 }
        default { return 0 }
    }
}

function Merge-Status {
    param(
        [string]$Current,
        [string]$Candidate
    )
    if ((Get-StatusRank $Candidate) -gt (Get-StatusRank $Current)) {
        return $Candidate
    }
    return $Current
}

function Get-PropValue {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
    }
    $prop = $Object.PSObject.Properties[$Name]
    if ($null -ne $prop) { return $prop.Value }
    return $null
}

function Get-NormalizedText {
    param($Value)
    if ($null -eq $Value) { return '' }
    return ([string]$Value).Trim().ToLowerInvariant()
}

function Get-CollectionCount {
    param($Value)
    if ($null -eq $Value) { return 0 }
    if ($Value -is [string]) {
        if ([string]::IsNullOrWhiteSpace($Value)) { return 0 }
        return 1
    }
    if ($Value -is [System.Collections.IDictionary]) {
        return [int]$Value.Count
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        return @($Value).Count
    }
    return 1
}

function Try-ParseJsonFromText {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }

    $candidate = $Text.Trim()
    $candidate = $candidate.TrimStart([char]0xFEFF)

    try {
        return @{
            Parsed = ($candidate | ConvertFrom-Json -Depth 100)
            Raw    = $candidate
        }
    }
    catch {}

    $pairs = @(
        @{ Start = '{'; End = '}' },
        @{ Start = '['; End = ']' }
    )
    foreach ($pair in $pairs) {
        $start = $candidate.IndexOf($pair.Start)
        $finish = $candidate.LastIndexOf($pair.End)
        if ($start -ge 0 -and $finish -gt $start) {
            $slice = $candidate.Substring($start, $finish - $start + 1)
            try {
                return @{
                    Parsed = ($slice | ConvertFrom-Json -Depth 100)
                    Raw    = $slice
                }
            }
            catch {}
        }
    }

    return $null
}

function New-SemanticAssessment {
    param(
        [string]$Status = 'ok',
        [string[]]$Reasons = @(),
        [bool]$Detected = $false
    )
    return [pscustomobject]@{
        status   = $Status
        reasons  = @($Reasons)
        detected = $Detected
    }
}

function Evaluate-JsonSemantics {
    param(
        $JsonObject,
        [string]$Profile = 'json_generic'
    )

    $status = 'ok'
    $reasons = New-Object System.Collections.ArrayList

    $severity = Get-NormalizedText (Get-PropValue $JsonObject 'severity')
    $okValue = Get-PropValue $JsonObject 'ok'
    $blockedValue = Get-PropValue $JsonObject 'blocked'
    $state = Get-NormalizedText (Get-PropValue $JsonObject 'state')
    $message = Get-NormalizedText (Get-PropValue $JsonObject 'message')
    $checks = @(Get-PropValue $JsonObject 'checks')
    $errorsCount = Get-CollectionCount (Get-PropValue $JsonObject 'errors')
    $warningsCount = Get-CollectionCount (Get-PropValue $JsonObject 'warnings')

    $failSeverities = @('error','fatal','critical')
    $warnSeverities = @('warn','warning')
    $warnStates = @('blocked','degraded','maintenance','paused','draining','half_open','half-open','half_open_blocked')
    $failStates = @('failed','error','unhealthy')

    if ($Profile -eq 'json_health') {
        $warnStates += @('circuit_open_blocked','circuit_half_open_blocked')
    }

    if ($failSeverities -contains $severity) {
        $status = Merge-Status $status 'fail'
        [void]$reasons.Add(('severity=' + $severity))
    }
    elseif ($warnSeverities -contains $severity) {
        $status = Merge-Status $status 'warn'
        [void]$reasons.Add(('severity=' + $severity))
    }

    if ($okValue -is [bool] -and $okValue -eq $false) {
        if ($Profile -eq 'json_health') {
            $status = Merge-Status $status 'warn'
        }
        else {
            $status = Merge-Status $status 'fail'
        }
        [void]$reasons.Add('ok=false')
    }

    if ($blockedValue -is [bool] -and $blockedValue -eq $true) {
        if ($Profile -eq 'json_security') {
            $status = Merge-Status $status 'fail'
        }
        else {
            $status = Merge-Status $status 'warn'
        }
        [void]$reasons.Add('blocked=true')
    }

    if ($warnStates -contains $state) {
        $status = Merge-Status $status 'warn'
        if ($message) {
            [void]$reasons.Add(('state=' + $state + '; message=' + $message))
        }
        else {
            [void]$reasons.Add(('state=' + $state))
        }
    }
    elseif ($failStates -contains $state) {
        $status = Merge-Status $status 'fail'
        if ($message) {
            [void]$reasons.Add(('state=' + $state + '; message=' + $message))
        }
        else {
            [void]$reasons.Add(('state=' + $state))
        }
    }

    foreach ($check in $checks) {
        $checkStatus = Get-NormalizedText (Get-PropValue $check 'status')
        $checkName = [string](Get-PropValue $check 'name')
        if (@('error','fail','failed','critical') -contains $checkStatus) {
            if ($Profile -eq 'json_health') {
                $status = Merge-Status $status 'warn'
            }
            else {
                $status = Merge-Status $status 'fail'
            }
            if ($checkName) {
                [void]$reasons.Add(('check=' + $checkName + ':' + $checkStatus))
            }
            else {
                [void]$reasons.Add(('check_status=' + $checkStatus))
            }
        }
        elseif (@('warn','warning') -contains $checkStatus) {
            $status = Merge-Status $status 'warn'
            if ($checkName) {
                [void]$reasons.Add(('check=' + $checkName + ':' + $checkStatus))
            }
            else {
                [void]$reasons.Add(('check_status=' + $checkStatus))
            }
        }
    }

    if ($errorsCount -gt 0) {
        if ($Profile -eq 'json_health') {
            $status = Merge-Status $status 'warn'
        }
        else {
            $status = Merge-Status $status 'fail'
        }
        [void]$reasons.Add(('errors=' + $errorsCount))
    }

    if ($warningsCount -gt 0) {
        $status = Merge-Status $status 'warn'
        [void]$reasons.Add(('warnings=' + $warningsCount))
    }

    return (New-SemanticAssessment -Status $status -Reasons @($reasons) -Detected $true)
}

function Evaluate-TextSemantics {
    param(
        [string]$StdoutText,
        [string]$StderrText,
        [string]$Profile = 'none',
        [bool]$TimedOut = $false
    )

    $combined = (($StdoutText | Out-String) + "`n" + ($StderrText | Out-String))
    $combinedNorm = $combined.ToLowerInvariant()

    switch ($Profile) {
        'pytest_collect' {
            if ($combinedNorm -match '(\d+)\s+tests collected') {
                $count = [int]$matches[1]
                if ($count -gt 0) {
                    return (New-SemanticAssessment -Status 'ok' -Reasons @(('tests_collected=' + $count)) -Detected $true)
                }
                return (New-SemanticAssessment -Status 'fail' -Reasons @('tests_collected=0') -Detected $true)
            }
            return (New-SemanticAssessment -Status 'warn' -Reasons @('pytest_collect_output_unrecognized') -Detected $true)
        }
        'dashboard' {
            if ($combinedNorm -match 'streamlit is not installed') {
                return (New-SemanticAssessment -Status 'warn' -Reasons @('streamlit_missing') -Detected $true)
            }
            if ($combinedNorm -match 'local url:' -or $combinedNorm -match 'you can now view your streamlit app') {
                if ($TimedOut) {
                    return (New-SemanticAssessment -Status 'ok' -Reasons @('dashboard_started_and_timed_out_as_expected') -Detected $true)
                }
                return (New-SemanticAssessment -Status 'ok' -Reasons @('dashboard_started') -Detected $true)
            }
            if ($TimedOut) {
                return (New-SemanticAssessment -Status 'warn' -Reasons @('dashboard_timed_out_without_start_confirmation') -Detected $true)
            }
            if (-not [string]::IsNullOrWhiteSpace($StderrText)) {
                return (New-SemanticAssessment -Status 'warn' -Reasons @('dashboard_stderr_nonempty') -Detected $true)
            }
            return (New-SemanticAssessment -Status 'warn' -Reasons @('dashboard_exited_without_launch_signal') -Detected $true)
        }
        default {
            if ($combinedNorm -match 'traceback \(most recent call last\):') {
                return (New-SemanticAssessment -Status 'fail' -Reasons @('python_traceback_detected') -Detected $true)
            }
            if (-not [string]::IsNullOrWhiteSpace($StderrText)) {
                return (New-SemanticAssessment -Status 'warn' -Reasons @('stderr_nonempty') -Detected $true)
            }
            return (New-SemanticAssessment -Status 'ok' -Reasons @() -Detected $false)
        }
    }
}

function Resolve-FinalStatus {
    param(
        [string]$ProcessStatus,
        [string]$SemanticStatus,
        [bool]$Optional,
        [bool]$DowngradeSemanticFailToWarn
    )

    if ($ProcessStatus -eq 'fail') {
        if ($Optional) { return 'warn' }
        return 'fail'
    }

    if ($SemanticStatus -eq 'fail') {
        if ($Optional -or $DowngradeSemanticFailToWarn) { return 'warn' }
        return 'fail'
    }

    if ($SemanticStatus -eq 'warn') {
        return 'warn'
    }

    return 'ok'
}

function Get-RedactedEnvSummary {
    $vars = New-Object System.Collections.ArrayList
    Get-ChildItem Env: | Where-Object { $_.Name -like 'THALOR*' } | Sort-Object Name | ForEach-Object {
        $name = $_.Name
        $rawValue = [string]$_.Value
        $displayValue = if ($name -eq 'THALOR_SECRETS_FILE') {
            if ([string]::IsNullOrWhiteSpace($rawValue)) { '' } else { Split-Path $rawValue -Leaf }
        }
        elseif ($name -match 'SECRET|TOKEN|PASSWORD|EMAIL|LOGIN|KEY') {
            '<redacted>'
        }
        elseif ($rawValue.Length -gt 120) {
            $rawValue.Substring(0, 120) + '...'
        }
        else {
            $rawValue
        }

        [void]$vars.Add([pscustomobject]@{
            name  = $name
            value = $displayValue
        })
    }

    return [pscustomobject]@{
        count = $vars.Count
        vars  = @($vars)
    }
}

function Get-RelevantEnvSnapshot {
    $snapshot = @{}
    Get-ChildItem Env: | Where-Object {
        $_.Name -like 'THALOR*' -or $_.Name -eq 'PYTHONPATH' -or $_.Name -eq 'PYTEST_ADDOPTS'
    } | ForEach-Object {
        $snapshot[$_.Name] = [string]$_.Value
    }
    return $snapshot
}

function Restore-RelevantEnvSnapshot {
    param($Snapshot)

    Get-ChildItem Env: | Where-Object {
        $_.Name -like 'THALOR*' -or $_.Name -eq 'PYTHONPATH' -or $_.Name -eq 'PYTEST_ADDOPTS'
    } | ForEach-Object {
        if (-not $Snapshot.ContainsKey($_.Name)) {
            Remove-Item ("Env:" + $_.Name) -ErrorAction SilentlyContinue
        }
    }

    foreach ($key in $Snapshot.Keys) {
        Set-Item ("Env:" + $key) $Snapshot[$key]
    }
}

function Clear-ThalorAmbientEnv {
    Get-ChildItem Env: | Where-Object { $_.Name -like 'THALOR*' } | ForEach-Object {
        Remove-Item ("Env:" + $_.Name) -ErrorAction SilentlyContinue
    }
    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
}

function Remove-PathWithRetry {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    for ($i = 0; $i -lt 3; $i++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            if ($i -eq 2) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Reset-WorkspaceState {
    param([string]$PhaseName)

    $removed = New-Object System.Collections.ArrayList
    $targets = @(
        (Join-Path $RepoRoot 'runs'),
        (Join-Path $RepoRoot 'logs'),
        (Join-Path $RepoRoot 'reports'),
        (Join-Path $RepoRoot '.pytest_cache'),
        (Join-Path $RepoRoot 'htmlcov'),
        (Join-Path $RepoRoot 'coverage.xml'),
        (Join-Path $RepoRoot '.coverage')
    )

    foreach ($target in $targets) {
        if (Test-Path $target) {
            Remove-PathWithRetry -Path $target
            [void]$removed.Add((Get-RepoRelativePath $target))
        }
    }

    Get-ChildItem -Path $RepoRoot -Filter '.coverage.*' -File -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-PathWithRetry -Path $_.FullName
        [void]$removed.Add((Get-RepoRelativePath $_.FullName))
    }

    New-Item -ItemType Directory -Force (Join-Path $RepoRoot 'runs\tests') | Out-Null

    return [pscustomobject]@{
        phase    = $PhaseName
        removed  = @($removed)
        recreated = @('runs', 'runs\tests')
    }
}

function Apply-PhaseEnvironment {
    param(
        [string]$PhaseName,
        [bool]$UseBrokerSecrets
    )

    Clear-ThalorAmbientEnv
    $env:PYTHONPATH = $script:DesiredPythonPath

    if ($UseBrokerSecrets) {
        $secretPath = Join-Path $RepoRoot 'config\broker_secrets.yaml'
        if (Test-Path $secretPath) {
            $env:THALOR_SECRETS_FILE = $secretPath
        }
    }

    return [pscustomobject]@{
        phase = $PhaseName
        use_broker_secrets = $UseBrokerSecrets
        env = Get-RedactedEnvSummary
    }
}

function New-StepContext {
    param(
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][string]$CommandText,
        [string]$Phase = ''
    )

    $ordinalName = Get-StepOrdinalName -Name $DisplayName
    $safeName = ($ordinalName -replace '[^A-Za-z0-9._-]', '_')
    $stepRoot = Join-Path $ArtifactsRoot $safeName
    $stageDir = Join-Path $stepRoot 'stage'
    $ctxDir = Join-Path $stageDir 'context'
    $payloadDir = Join-Path $stageDir 'payload'
    New-Item -ItemType Directory -Force $ctxDir | Out-Null
    New-Item -ItemType Directory -Force $payloadDir | Out-Null

    $commandFile = Join-Path $ctxDir 'command.txt'
    Set-Content $commandFile $CommandText -Encoding UTF8
    (Get-RedactedEnvSummary | ConvertTo-Json -Depth 8) | Set-Content (Join-Path $ctxDir 'env_scope.json') -Encoding UTF8

    return [pscustomobject]@{
        name        = $DisplayName
        ordinalName = $ordinalName
        stepRoot    = $stepRoot
        stageDir    = $stageDir
        ctxDir      = $ctxDir
        payloadDir  = $payloadDir
        commandText = $CommandText
        commandFile = $commandFile
        metaFile    = (Join-Path $ctxDir 'meta.json')
        stdoutFile  = (Join-Path $ctxDir 'stdout.log')
        stderrFile  = (Join-Path $ctxDir 'stderr.log')
        combinedLog = (Join-Path $ctxDir 'combined.log')
        phase       = $Phase
    }
}

function Finalize-Step {
    param(
        $Context,
        [string]$FinalStatus,
        [bool]$Optional,
        [int]$ExitCode,
        [string]$ProcessStatus,
        [string]$SemanticStatus,
        [string[]]$SemanticReasons,
        [int]$TimeoutSec,
        [bool]$TimedOut,
        [string[]]$TrackRoots,
        [string[]]$AlwaysInclude,
        [string]$SemanticProfile
    )

    $meta = [pscustomobject]@{
        name               = $Context.name
        ordinal_name       = $Context.ordinalName
        phase              = $Context.phase
        status             = $FinalStatus
        optional           = $Optional
        command            = $Context.commandText
        process_status     = $ProcessStatus
        semantic_status    = $SemanticStatus
        semantic_profile   = $SemanticProfile
        semantic_reasons   = @($SemanticReasons)
        exit_code          = $ExitCode
        timeout            = $TimeoutSec
        timed_out          = $TimedOut
        track_roots        = $TrackRoots
        always_include     = $AlwaysInclude
        started_at         = $script:CurrentStepStart.ToString('o')
        ended_at           = (Get-Date).ToString('o')
    }
    $meta | ConvertTo-Json -Depth 8 | Set-Content $Context.metaFile -Encoding UTF8

    $zipPath = Join-Path $ArtifactsRoot ($Context.ordinalName + '.zip')
    New-ZipFromStage -StageDir $Context.stageDir -ZipPath $zipPath

    $row = [pscustomobject]@{
        name            = $Context.name
        ordinal_name    = $Context.ordinalName
        phase           = $Context.phase
        status          = $FinalStatus
        optional        = $Optional
        process_status  = $ProcessStatus
        semantic_status = $SemanticStatus
        exit_code       = $ExitCode
        zip             = $zipPath
        reasons         = @($SemanticReasons)
    }
    [void]$script:Results.Add($row)

    if ($FinalStatus -eq 'fail') {
        [void]$script:Failures.Add($row)
        Write-Warning ("FAIL -> {0} | {1}" -f $Context.name, $zipPath)
        if ($StopOnRequiredFailure) {
            throw ("Step obrigatório falhou: {0}" -f $Context.name)
        }
    }
    elseif ($FinalStatus -eq 'warn') {
        [void]$script:Warnings.Add($row)
        Write-Warning ("WARN -> {0} | {1}" -f $Context.name, $zipPath)
    }
    elseif ($FinalStatus -eq 'skipped') {
        Write-Host ("SKIP -> {0} | {1}" -f $Context.name, $zipPath) -ForegroundColor Yellow
    }
    else {
        Write-Host ("OK -> {0} | {1}" -f $Context.name, $zipPath) -ForegroundColor Green
    }
}

function Write-SkippedStep {
    param(
        [string]$Name,
        [string]$Reason,
        [string]$CommandText,
        [bool]$Optional,
        [string[]]$AlwaysInclude = @(),
        [string]$Phase = ''
    )

    $script:CurrentStepStart = Get-Date
    $ctx = New-StepContext -DisplayName $Name -CommandText $CommandText -Phase $Phase
    Set-Content $ctx.combinedLog $Reason -Encoding UTF8
    Copy-AlwaysIncludePaths -AlwaysInclude $AlwaysInclude -StagePayloadRoot $ctx.payloadDir
    Finalize-Step -Context $ctx -FinalStatus 'skipped' -Optional $Optional -ExitCode 0 -ProcessStatus 'skipped' -SemanticStatus 'ok' -SemanticReasons @($Reason) -TimeoutSec 0 -TimedOut $false -TrackRoots @() -AlwaysInclude $AlwaysInclude -SemanticProfile 'none'
}

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string[]]$TrackRoots = @(),
        [string[]]$AlwaysInclude = @(),
        [bool]$Optional = $false,
        [int]$TimeoutSec = 0,
        [string]$SemanticProfile = 'none',
        [bool]$DowngradeSemanticFailToWarn = $false,
        [bool]$TreatTimeoutAsSuccess = $false,
        [string]$Phase = ''
    )

    $commandText = Join-CommandText -FilePath $FilePath -Arguments $Arguments
    if (-not (Test-ExternalAvailable $FilePath)) {
        Write-SkippedStep -Name $Name -Reason ("Executável não encontrado: {0}" -f $FilePath) -CommandText $commandText -Optional $Optional -AlwaysInclude $AlwaysInclude -Phase $Phase
        return
    }

    $script:CurrentStepStart = Get-Date
    $ctx = New-StepContext -DisplayName $Name -CommandText $commandText -Phase $Phase
    $before = Get-FileSnapshot -TrackRoots $TrackRoots

    [int]$exitCode = 1
    $timedOut = $false

    try {
        if ($TimeoutSec -le 0) {
            $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $RepoRoot -RedirectStandardOutput $ctx.stdoutFile -RedirectStandardError $ctx.stderrFile -PassThru -Wait
            $exitCode = [int]$proc.ExitCode
        }
        else {
            $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $RepoRoot -RedirectStandardOutput $ctx.stdoutFile -RedirectStandardError $ctx.stderrFile -PassThru
            try {
                Wait-Process -Id $proc.Id -Timeout $TimeoutSec -ErrorAction Stop
                $proc.WaitForExit()
                $proc.Refresh()
                if ($proc.HasExited) {
                    $exitCode = [int]$proc.ExitCode
                }
                else {
                    $timedOut = $true
                    $exitCode = 124
                }
            }
            catch {
                $timedOut = $true
                $exitCode = 124
                try {
                    & taskkill /PID $proc.Id /T /F | Out-Null
                }
                catch {
                    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
                }
            }
        }
    }
    catch {
        $_ | Out-String | Set-Content $ctx.stderrFile -Encoding UTF8
        $exitCode = 1
    }

    $stdoutText = if (Test-Path $ctx.stdoutFile) { Get-Content $ctx.stdoutFile -Raw } else { '' }
    $stderrText = if (Test-Path $ctx.stderrFile) { Get-Content $ctx.stderrFile -Raw } else { '' }
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
    Set-Content $ctx.combinedLog $combined -Encoding UTF8

    $semantic = New-SemanticAssessment -Status 'ok' -Reasons @() -Detected $false
    if ($SemanticProfile -like 'json*') {
        $parsed = Try-ParseJsonFromText -Text $stdoutText
        if ($null -ne $parsed) {
            $semantic = Evaluate-JsonSemantics -JsonObject $parsed.Parsed -Profile $SemanticProfile
            ($parsed.Raw) | Set-Content (Join-Path $ctx.ctxDir 'stdout.json') -Encoding UTF8
        }
        else {
            $semantic = New-SemanticAssessment -Status 'warn' -Reasons @('json_output_not_parseable') -Detected $true
        }
    }
    elseif ($SemanticProfile -ne 'none') {
        $semantic = Evaluate-TextSemantics -StdoutText $stdoutText -StderrText $stderrText -Profile $SemanticProfile -TimedOut $timedOut
    }

    if ($timedOut -and $TreatTimeoutAsSuccess) {
        $processStatus = 'ok'
    }
    elseif ($exitCode -eq 0) {
        $processStatus = 'ok'
    }
    else {
        $processStatus = 'fail'
    }

    $finalStatus = Resolve-FinalStatus -ProcessStatus $processStatus -SemanticStatus $semantic.status -Optional $Optional -DowngradeSemanticFailToWarn $DowngradeSemanticFailToWarn

    $after = Get-FileSnapshot -TrackRoots $TrackRoots
    $changed = Get-ChangedRelativePaths -Before $before -After $after
    foreach ($rel in $changed) {
        Copy-FileToStage -SourceFullName (Join-Path $RepoRoot $rel) -StagePayloadRoot $ctx.payloadDir
    }
    Copy-AlwaysIncludePaths -AlwaysInclude $AlwaysInclude -StagePayloadRoot $ctx.payloadDir

    Finalize-Step -Context $ctx -FinalStatus $finalStatus -Optional $Optional -ExitCode $exitCode -ProcessStatus $processStatus -SemanticStatus $semantic.status -SemanticReasons $semantic.reasons -TimeoutSec $TimeoutSec -TimedOut $timedOut -TrackRoots $TrackRoots -AlwaysInclude $AlwaysInclude -SemanticProfile $SemanticProfile
}

function Invoke-PowerShellStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock,
        [string]$CommandText,
        [string[]]$TrackRoots = @(),
        [string[]]$AlwaysInclude = @(),
        [bool]$Optional = $false,
        [string]$Phase = ''
    )

    $script:CurrentStepStart = Get-Date
    $ctx = New-StepContext -DisplayName $Name -CommandText $CommandText -Phase $Phase
    $before = Get-FileSnapshot -TrackRoots $TrackRoots

    [int]$exitCode = 0
    Push-Location $RepoRoot
    try {
        $global:LASTEXITCODE = 0
        & $ScriptBlock *>&1 | Out-File -FilePath $ctx.combinedLog -Encoding UTF8
        if ($null -ne $global:LASTEXITCODE -and [int]$global:LASTEXITCODE -ne 0) {
            $exitCode = [int]$global:LASTEXITCODE
        }
    }
    catch {
        $_ | Out-String | Out-File -FilePath $ctx.combinedLog -Encoding UTF8 -Append
        $exitCode = 1
    }
    finally {
        Pop-Location
    }

    $after = Get-FileSnapshot -TrackRoots $TrackRoots
    $changed = Get-ChangedRelativePaths -Before $before -After $after
    foreach ($rel in $changed) {
        Copy-FileToStage -SourceFullName (Join-Path $RepoRoot $rel) -StagePayloadRoot $ctx.payloadDir
    }
    Copy-AlwaysIncludePaths -AlwaysInclude $AlwaysInclude -StagePayloadRoot $ctx.payloadDir

    $processStatus = if ($exitCode -eq 0) { 'ok' } else { 'fail' }
    $finalStatus = Resolve-FinalStatus -ProcessStatus $processStatus -SemanticStatus 'ok' -Optional $Optional -DowngradeSemanticFailToWarn $false

    Finalize-Step -Context $ctx -FinalStatus $finalStatus -Optional $Optional -ExitCode $exitCode -ProcessStatus $processStatus -SemanticStatus 'ok' -SemanticReasons @() -TimeoutSec 0 -TimedOut $false -TrackRoots $TrackRoots -AlwaysInclude $AlwaysInclude -SemanticProfile 'none'
}

function Invoke-PhasePreparationStep {
    param(
        [string]$Name,
        [string]$PhaseName,
        [bool]$UseBrokerSecrets
    )

    $cleanFlag = $CleanBeforeEachPhase
    Invoke-PowerShellStep -Name $Name -CommandText ("Prepare isolated phase: {0}" -f $PhaseName) -TrackRoots @('runs', 'logs', 'reports', '.pytest_cache', 'htmlcov', 'coverage.xml') -AlwaysInclude @() -Phase $PhaseName -ScriptBlock {
        if ($cleanFlag) {
            $reset = Reset-WorkspaceState -PhaseName $PhaseName
            '=== RESET_WORKSPACE ==='
            $reset | ConvertTo-Json -Depth 8
        }
        else {
            '=== RESET_WORKSPACE ==='
            '{"skipped": true}'
            New-Item -ItemType Directory -Force (Join-Path $RepoRoot 'runs\tests') | Out-Null
        }

        '=== APPLY_ENVIRONMENT ==='
        $envReport = Apply-PhaseEnvironment -PhaseName $PhaseName -UseBrokerSecrets:$UseBrokerSecrets
        $envReport | ConvertTo-Json -Depth 8
    }
}

function Ensure-ControlConfigStep {
    param(
        [string]$Name,
        [string]$TargetRelPath,
        [string]$ExampleRelPath,
        [string]$PhaseName
    )

    Invoke-PowerShellStep -Name $Name -CommandText ("Ensure control config exists: {0}" -f $TargetRelPath) -TrackRoots @('config') -AlwaysInclude @($ExampleRelPath) -Phase $PhaseName -ScriptBlock {
        $target = Join-Path $RepoRoot $TargetRelPath
        $example = Join-Path $RepoRoot $ExampleRelPath
        if (-not (Test-Path $target)) {
            if (-not (Test-Path $example)) {
                throw ("Config ausente e example não encontrado: {0}" -f $ExampleRelPath)
            }
            Copy-Item $example $target -Force
            Write-Output ('Created ' + $target)
        }
        else {
            Write-Output ('Using existing ' + $target)
        }
    }
}

function Write-SummaryFile {
    $counts = @{
        ok      = (@($script:Results | Where-Object { $_.status -eq 'ok' })).Count
        warn    = (@($script:Results | Where-Object { $_.status -eq 'warn' })).Count
        fail    = (@($script:Results | Where-Object { $_.status -eq 'fail' })).Count
        skipped = (@($script:Results | Where-Object { $_.status -eq 'skipped' })).Count
    }

    $summaryPath = Join-Path $ArtifactsRoot 'SUMMARY.json'
    $payload = [pscustomobject]@{
        session_id    = $SessionId
        generated_at  = (Get-Date).ToString('o')
        repo_root     = $RepoRoot
        artifact_root = $ArtifactsRoot
        counts        = $counts
        results       = $script:Results
        failures      = $script:Failures
        warnings      = $script:Warnings
    }
    $payload | ConvertTo-Json -Depth 10 | Set-Content $summaryPath -Encoding UTF8
    Set-Content (Join-Path $ArtifactsParent 'LATEST_SESSION.txt') $ArtifactsRoot -Encoding UTF8
    Write-Host ("SUMMARY -> {0}" -f $summaryPath) -ForegroundColor Cyan
}

function Write-AggregateBundle {
    if (-not $CreateAggregateBundle) { return }

    $bundlePath = Join-Path $ArtifactsParent ("diag_bundle_{0}.zip" -f $SessionId)
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $bundlePath) { Remove-Item $bundlePath -Force }

    $zip = [System.IO.Compression.ZipFile]::Open($bundlePath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        Get-ChildItem -LiteralPath $ArtifactsRoot -File | ForEach-Object {
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $_.Name, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    }
    finally {
        $zip.Dispose()
    }

    Write-Host ("BUNDLE -> {0}" -f $bundlePath) -ForegroundColor Cyan
}

$python = Resolve-PythonLauncher
$shellExe = Resolve-PreferredShell
$dockerExe = (Get-Command docker -ErrorAction SilentlyContinue)

$srcPath = Join-Path $RepoRoot 'src'
if ($env:PYTHONPATH) {
    if (-not ($env:PYTHONPATH -split [System.IO.Path]::PathSeparator | Where-Object { $_ -eq $srcPath })) {
        $script:DesiredPythonPath = $srcPath + [System.IO.Path]::PathSeparator + $env:PYTHONPATH
    }
    else {
        $script:DesiredPythonPath = $env:PYTHONPATH
    }
}
else {
    $script:DesiredPythonPath = $srcPath
}

$script:OriginalEnvSnapshot = Get-RelevantEnvSnapshot
$env:PYTHONPATH = $script:DesiredPythonPath

$CommonTrack = @('runs', 'logs', 'reports', 'coverage.xml', 'htmlcov', '.pytest_cache')
$BaseConfig = @('config\base.yaml')
$MultiConfig = @('config\multi_asset.yaml')

try {
    if ($Mode -in @('Offline','All')) {
        Invoke-PhasePreparationStep -Name '00_phase_prepare_offline' -PhaseName 'offline' -UseBrokerSecrets:$false

        Invoke-PowerShellStep -Name '01_env_bootstrap' -CommandText 'Record environment/bootstrap status without mutating secrets or .env' -TrackRoots $CommonTrack -AlwaysInclude @('pyproject.toml', 'requirements.txt', 'requirements-dev.txt', 'requirements-dashboard.txt', 'pytest.ini', 'README.md', '.env.example', 'config\broker_secrets.yaml.example') -Phase 'offline' -ScriptBlock {
            Write-Output ("RepoRoot=" + $RepoRoot)
            Write-Output ("PYTHONPATH=" + $env:PYTHONPATH)
            Write-Output ("PythonDisplay=" + $python.Display)
            Write-Output ("DotEnvPresent=" + (Test-Path (Join-Path $RepoRoot '.env')))
            Write-Output ("DotEnvExamplePresent=" + (Test-Path (Join-Path $RepoRoot '.env.example')))
            Write-Output ("BrokerSecretsPresent=" + (Test-Path (Join-Path $RepoRoot 'config\broker_secrets.yaml')))
            Write-Output ("BrokerSecretsExamplePresent=" + (Test-Path (Join-Path $RepoRoot 'config\broker_secrets.yaml.example')))
            New-Item -ItemType Directory -Force (Join-Path $RepoRoot 'runs\tests') | Out-Null
        }

        Invoke-NativeStep -Name '02_python_version' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('--version')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '03_pip_check' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pip','check')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '04_compileall' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','compileall','-q','src','tests','scripts')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '05_pytest_collect' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','--collect-only','-q')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -SemanticProfile 'pytest_collect' -Phase 'offline'
        Invoke-NativeStep -Name '06_selfcheck_repo' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/selfcheck_repo.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '07_hidden_unicode' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/check_hidden_unicode.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '08_leak_check' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.leak_check')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '09_release_hygiene_smoke' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/release_hygiene_smoke.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '10_release_hygiene_dryrun' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.release_hygiene','--repo-root','.','--dry-run','--json')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'

        $pytestStrictArgs = @('-m','pytest','-q','-ra','--durations=25')
        if ($StrictWarnings) { $pytestStrictArgs += @('-W','error') }
        Invoke-NativeStep -Name '11_pytest_full' -FilePath $python.FilePath -Arguments ($python.BaseArgs + $pytestStrictArgs) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '12_pytest_final_fix_config' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q','tests\test_final_fix_config.py') + $(if ($StrictWarnings) { @('-W','error') } else { @() })) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '13_pytest_final_fix_sklearn' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q','tests\test_final_fix_sklearn_warnings.py') + $(if ($StrictWarnings) { @('-W','error') } else { @() })) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'

        $intelligenceTestFiles = @(Get-ChildItem -Path (Join-Path $RepoRoot 'tests\test_intelligence_*.py') -File -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { $_.FullName })
        if ($intelligenceTestFiles.Count -gt 0) {
            Invoke-NativeStep -Name '14_pytest_intelligence' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q') + $intelligenceTestFiles + $(if ($StrictWarnings) { @('-W','error') } else { @() })) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        }
        else {
            Write-SkippedStep -Name '14_pytest_intelligence' -Reason 'Nenhum arquivo tests\test_intelligence_*.py encontrado.' -CommandText 'pytest intelligence' -Optional $false -AlwaysInclude @() -Phase 'offline'
        }

        Invoke-NativeStep -Name '15_pytest_multi_exec_dashboard' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q','tests\test_multi_asset_package_2.py','tests\test_execution_layer_21.py','tests\test_dashboard_package_3.py') + $(if ($StrictWarnings) { @('-W','error') } else { @() })) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'

        if ($IncludeIsolatedPytest) {
            Get-ChildItem -Path (Join-Path $RepoRoot 'tests\test_*.py') -File | Sort-Object Name | ForEach-Object {
                $testPath = $_.FullName
                $testName = '16_single_' + $_.BaseName
                Invoke-NativeStep -Name $testName -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','pytest','-q',$testPath) + $(if ($StrictWarnings) { @('-W','error') } else { @() })) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
            }
        }

        Invoke-NativeStep -Name '17_local_suite_full' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/local_test_suite.py','--repo-root','.','--preset','full','--include-soak')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '18_smoke_runtime_app' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/ci/smoke_runtime_app.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        Invoke-NativeStep -Name '19_smoke_execution_layer' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/ci/smoke_execution_layer.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'

        Get-ChildItem -Path (Join-Path $RepoRoot 'scripts\tools\*_smoke.py') -File | Sort-Object Name | ForEach-Object {
            $smokePath = $_.FullName
            $smokeName = '20_smoke_' + $_.BaseName
            Invoke-NativeStep -Name $smokeName -FilePath $python.FilePath -Arguments ($python.BaseArgs + @($smokePath)) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'
        }

        Invoke-NativeStep -Name '21_rt_status' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','status','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '22_rt_plan' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','plan','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '23_rt_quota' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','quota','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '24_rt_precheck' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','precheck','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '25_rt_healthcheck' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','healthcheck','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_health' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '26_rt_health' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','health','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_health' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '27_rt_security' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','security','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_security' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '28_rt_protection' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','protection','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '29_rt_doctor' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','doctor','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '30_rt_release' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','release','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '31_rt_sync' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','sync','--repo-root','.','--json')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '32_rt_incidents_status' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','incidents','status','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '33_rt_incidents_drill' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','incidents','drill','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '34_rt_alerts_status' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','alerts','status','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '35_rt_orders' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','orders','--repo-root','.','--config','config/base.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $BaseConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '36_runtime_health_report' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/runtime_health_report.py')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'offline'

        Invoke-NativeStep -Name '37_pf_status' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','portfolio','status','--repo-root','.','--config','config/multi_asset.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $MultiConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '38_pf_plan' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','portfolio','plan','--repo-root','.','--config','config/multi_asset.yaml','--json')) -TrackRoots $CommonTrack -AlwaysInclude $MultiConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'
        Invoke-NativeStep -Name '39_pf_observe' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','portfolio','observe','--repo-root','.','--config','config/multi_asset.yaml','--once','--topk','3','--lookback-candles','2000','--json')) -TrackRoots $CommonTrack -AlwaysInclude $MultiConfig -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'offline'

        if ($shellExe) {
            Invoke-NativeStep -Name '40_dashboard' -FilePath $shellExe -Arguments @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $RepoRoot 'scripts\tools\run_dashboard.ps1'),'-RepoRoot','.','-Config','config/multi_asset.yaml','-Port','8501') -TrackRoots $CommonTrack -AlwaysInclude $MultiConfig -Optional $true -TimeoutSec $DashboardSeconds -TreatTimeoutAsSuccess $true -SemanticProfile 'dashboard' -Phase 'offline'
        }
        else {
            Write-SkippedStep -Name '40_dashboard' -Reason 'Nenhum shell PowerShell encontrado (pwsh/powershell).' -CommandText 'run_dashboard.ps1' -Optional $true -AlwaysInclude $MultiConfig -Phase 'offline'
        }

        if ($IncludeDocker) {
            if ($dockerExe) {
                Invoke-NativeStep -Name '41_docker_build' -FilePath $dockerExe.Source -Arguments @('build','-t','thalor:ci','.') -TrackRoots $CommonTrack -AlwaysInclude @('Dockerfile','docker-compose.yml','docker-compose.prod.yml','docker-compose.vps.yml') -Optional $true -Phase 'offline'
                Invoke-NativeStep -Name '42_compose_base' -FilePath $dockerExe.Source -Arguments @('compose','-f','docker-compose.yml','config') -TrackRoots $CommonTrack -AlwaysInclude @('docker-compose.yml') -Optional $true -Phase 'offline'
                Invoke-NativeStep -Name '43_compose_prod' -FilePath $dockerExe.Source -Arguments @('compose','-f','docker-compose.yml','-f','docker-compose.prod.yml','config') -TrackRoots $CommonTrack -AlwaysInclude @('docker-compose.yml','docker-compose.prod.yml') -Optional $true -Phase 'offline'
                Invoke-NativeStep -Name '44_compose_vps' -FilePath $dockerExe.Source -Arguments @('compose','-f','docker-compose.yml','-f','docker-compose.vps.yml','config') -TrackRoots $CommonTrack -AlwaysInclude @('docker-compose.yml','docker-compose.vps.yml') -Optional $true -Phase 'offline'
            }
            else {
                Write-SkippedStep -Name '41_docker_bundle' -Reason 'Docker não encontrado.' -CommandText 'docker build/compose' -Optional $true -AlwaysInclude @('Dockerfile','docker-compose.yml','docker-compose.prod.yml','docker-compose.vps.yml') -Phase 'offline'
            }
        }
    }

    if ($Mode -in @('Practice','All')) {
        Invoke-PhasePreparationStep -Name '50_phase_prepare_practice_baseline' -PhaseName 'practice_baseline' -UseBrokerSecrets:$false
        Invoke-NativeStep -Name '51_live_validation_baseline' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_live_validation.py','--repo-root','.','--stage','baseline')) -TrackRoots $CommonTrack -AlwaysInclude @() -Optional $false -Phase 'practice_baseline'

        Invoke-PhasePreparationStep -Name '52_phase_prepare_practice_live' -PhaseName 'practice_live' -UseBrokerSecrets:$true
        Ensure-ControlConfigStep -Name '53_prepare_practice_config' -TargetRelPath $PracticeConfig -ExampleRelPath 'config\live_controlled_practice.yaml.example' -PhaseName 'practice_live'
        Invoke-NativeStep -Name '54_live_validation_practice' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_live_validation.py','--repo-root','.','--stage','practice','--config',$PracticeConfig)) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false -Phase 'practice_live'
        Invoke-NativeStep -Name '55_runtime_soak_practice' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/runtime_soak.py','--repo-root','.','--config',$PracticeConfig,'--max-cycles','3')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false -Phase 'practice_live'
        Invoke-NativeStep -Name '56_rt_practice' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','practice','--repo-root','.','--config',$PracticeConfig,'--json')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'practice_live'
        Invoke-NativeStep -Name '57_rt_practice_bootstrap' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','practice-bootstrap','--repo-root','.','--config',$PracticeConfig,'--json')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'practice_live'
        Invoke-NativeStep -Name '58_rt_practice_round' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('-m','natbin.runtime_app','practice-round','--repo-root','.','--config',$PracticeConfig,'--json')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'practice_live'
        Invoke-NativeStep -Name '59_controlled_practice_round' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_practice_round.py','--repo-root','.','--config',$PracticeConfig,'--json')) -TrackRoots $CommonTrack -AlwaysInclude @($PracticeConfig) -Optional $false -SemanticProfile 'json_generic' -DowngradeSemanticFailToWarn $true -Phase 'practice_live'
    }

    if ($Mode -in @('RealPreflight','All')) {
        Invoke-PhasePreparationStep -Name '60_phase_prepare_real_preflight' -PhaseName 'real_preflight' -UseBrokerSecrets:$true
        Ensure-ControlConfigStep -Name '61_prepare_real_config' -TargetRelPath $RealConfig -ExampleRelPath 'config\live_controlled_real.yaml.example' -PhaseName 'real_preflight'
        Invoke-NativeStep -Name '62_live_validation_real_preflight' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_live_validation.py','--repo-root','.','--stage','real_preflight','--config',$RealConfig)) -TrackRoots $CommonTrack -AlwaysInclude @($RealConfig) -Optional $false -Phase 'real_preflight'
    }

    if ($Mode -eq 'RealSubmit') {
        if (-not $AllowLiveSubmit) {
            throw 'Modo RealSubmit exige -AllowLiveSubmit.'
        }
        if ($LiveAck -ne 'I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT') {
            throw 'Modo RealSubmit exige -LiveAck I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT.'
        }
        Invoke-PhasePreparationStep -Name '70_phase_prepare_real_submit' -PhaseName 'real_submit' -UseBrokerSecrets:$true
        Ensure-ControlConfigStep -Name '71_prepare_real_submit_config' -TargetRelPath $RealConfig -ExampleRelPath 'config\live_controlled_real.yaml.example' -PhaseName 'real_submit'
        Invoke-NativeStep -Name '72_live_validation_real_submit' -FilePath $python.FilePath -Arguments ($python.BaseArgs + @('scripts/tools/controlled_live_validation.py','--repo-root','.','--stage','real_submit','--config',$RealConfig,'--allow-live-submit','--ack-live',$LiveAck)) -TrackRoots $CommonTrack -AlwaysInclude @($RealConfig) -Optional $false -Phase 'real_submit'
    }

    Write-SummaryFile
    Write-AggregateBundle
    Get-ChildItem -Path $ArtifactsRoot -Filter *.zip | Sort-Object Name | Select-Object Name, Length, LastWriteTime
}
finally {
    Restore-RelevantEnvSnapshot -Snapshot $script:OriginalEnvSnapshot
}
