# Thalor — bateria profissional e exaustiva de testes

Este guia foi montado em cima do ZIP mais recente do Thalor que você anexou.
Ele usa a estrutura real do projeto:

- `tests/test_*.py`
- `scripts/tools/*_smoke.py`
- `scripts/ci/smoke_*.py`
- `scripts/tools/local_test_suite.py`
- `scripts/tools/run_thalor_diagnostics_v4_1.ps1`
- `src/natbin/runtime_app.py`
- `docker-compose*.yml`
- `Dockerfile`

## Regra operacional importante

**Nunca compacte o repositório inteiro para me enviar.**  
Envie só os ZIPs gerados pelas fases abaixo, porque eles ficam limitados a logs, relatórios, JUnit XML, JSONs e bundles sanitizados.

Não envie manualmente:

- `.env`
- `config/broker_secrets.yaml`
- `secrets/`
- `.venv/`
- `.git/`
- `data/*.sqlite3*`
- ZIPs feitos a partir da raiz inteira do projeto

---

## 0) Preparação

### PowerShell

```powershell
Set-Location C:\CAMINHO\PARA\Thalor

$RUN_ID = Get-Date -Format 'yyyyMMdd_HHmmss'
$ART = Join-Path (Resolve-Path .) "test_battery\$RUN_ID"
New-Item -ItemType Directory -Force $ART | Out-Null

function New-Phase {
    param([string]$Name)
    $Dir = Join-Path $ART $Name
    New-Item -ItemType Directory -Force $Dir | Out-Null
    return $Dir
}

function Close-PhaseZip {
    param([string]$Dir)
    $Zip = "$Dir.zip"
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    Compress-Archive -Path (Join-Path $Dir '*') -DestinationPath $Zip -Force
    Write-Host "ZIP criado: $Zip"
}

function Write-ExitCode {
    param([string]$Path,[int]$Code)
    Set-Content -Path $Path -Value $Code -Encoding UTF8
}

py -3.12 -m venv .venv
$PY = (Resolve-Path .\.venv\Scripts\python.exe).Path
$env:PYTHONPATH = "$((Resolve-Path .\src).Path);$env:PYTHONPATH"
```

Instalação:

```powershell
& $PY -m pip install -U pip
& $PY -m pip install -r requirements-dev.txt
```

---

## 1) Bateria de baseline, ambiente e dependências

```powershell
$P = New-Phase '01_baseline'

& $PY -VV *> (Join-Path $P 'python_version.log')
& $PY -m pip list *> (Join-Path $P 'pip_list.log')
& $PY -m pip check *> (Join-Path $P 'pip_check.log')
Write-ExitCode (Join-Path $P 'pip_check.exit.txt') $LASTEXITCODE

git status --short --branch *> (Join-Path $P 'git_status.log')
git rev-parse HEAD *> (Join-Path $P 'git_head.log')
Get-ChildItem . -Force | Select-Object Name,Mode,Length | Format-Table -AutoSize | Out-File (Join-Path $P 'repo_root_listing.log') -Encoding utf8

Close-PhaseZip $P
```

**Me envie depois:** `test_battery\<RUN_ID>\01_baseline.zip`

---

## 2) Bateria de integridade estática, imports, config e compilação

```powershell
$P = New-Phase '02_static_integrity'

& $PY scripts\tools\selfcheck_repo.py *> (Join-Path $P 'selfcheck_repo.log')
Write-ExitCode (Join-Path $P 'selfcheck_repo.exit.txt') $LASTEXITCODE

& $PY scripts\tools\check_hidden_unicode.py *> (Join-Path $P 'check_hidden_unicode.log')
Write-ExitCode (Join-Path $P 'check_hidden_unicode.exit.txt') $LASTEXITCODE

& $PY scripts\tools\config_smoke.py *> (Join-Path $P 'config_smoke.log')
Write-ExitCode (Join-Path $P 'config_smoke.exit.txt') $LASTEXITCODE

& $PY scripts\tools\config_consumers_smoke.py *> (Join-Path $P 'config_consumers_smoke.log')
Write-ExitCode (Join-Path $P 'config_consumers_smoke.exit.txt') $LASTEXITCODE

& $PY scripts\tools\repo_entrypoints_smoke.py *> (Join-Path $P 'repo_entrypoints_smoke.log')
Write-ExitCode (Join-Path $P 'repo_entrypoints_smoke.exit.txt') $LASTEXITCODE

& $PY scripts\tools\health_smoke.py *> (Join-Path $P 'health_smoke.log')
Write-ExitCode (Join-Path $P 'health_smoke.exit.txt') $LASTEXITCODE

& $PY -m compileall -q src tests scripts *> (Join-Path $P 'compileall.log')
Write-ExitCode (Join-Path $P 'compileall.exit.txt') $LASTEXITCODE

Close-PhaseZip $P
```

**Me envie depois:** `test_battery\<RUN_ID>\02_static_integrity.zip`

---

## 3) Bateria de pytest completo do projeto inteiro

```powershell
$P = New-Phase '03_pytest_full'

& $PY -m pytest -ra -vv --durations=50 --junitxml (Join-Path $P 'junit_full.xml') tests *> (Join-Path $P 'pytest_full.log')
Write-ExitCode (Join-Path $P 'pytest_full.exit.txt') $LASTEXITCODE

& $PY -m pytest --collect-only -q tests *> (Join-Path $P 'pytest_collect_only.log')
Write-ExitCode (Join-Path $P 'pytest_collect_only.exit.txt') $LASTEXITCODE

Close-PhaseZip $P
```

**Me envie depois:** `test_battery\<RUN_ID>\03_pytest_full.zip`

---

## 4) Bateria de pytest isolado por arquivo

Essa fase serve para achar:

- falhas específicas por módulo
- ordem de execução problemática
- dependência implícita entre testes
- flakiness básica

```powershell
$P = New-Phase '04_pytest_isolated'
$results = @()

Get-ChildItem .\tests\test_*.py | Sort-Object Name | ForEach-Object {
    $name = $_.BaseName
    $log = Join-Path $P "$name.log"
    $xml = Join-Path $P "$name.xml"

    & $PY -m pytest -ra -vv $_.FullName --junitxml $xml *> $log
    $code = $LASTEXITCODE

    $results += [pscustomobject]@{
        test_file = $_.Name
        exit_code = $code
        log = [System.IO.Path]::GetFileName($log)
        junit = [System.IO.Path]::GetFileName($xml)
    }
}

$results | ConvertTo-Json -Depth 5 | Out-File (Join-Path $P 'isolated_summary.json') -Encoding utf8
Close-PhaseZip $P
```

**Me envie depois:** `test_battery\<RUN_ID>\04_pytest_isolated.zip`

---

## 5) Bateria de smoke scripts — todos os smokes do repositório

```powershell
$P = New-Phase '05_smokes'
$results = @()

$smokes = @()
$smokes += Get-ChildItem .\scripts\tools\*_smoke.py
$smokes += Get-ChildItem .\scripts\ci\smoke_*.py
$smokes = $smokes | Sort-Object FullName -Unique

foreach ($s in $smokes) {
    $name = $s.BaseName
    $log = Join-Path $P "$name.log"

    & $PY $s.FullName *> $log
    $code = $LASTEXITCODE

    $results += [pscustomobject]@{
        smoke_script = $s.FullName
        exit_code = $code
        log = [System.IO.Path]::GetFileName($log)
    }
}

$results | ConvertTo-Json -Depth 5 | Out-File (Join-Path $P 'smoke_summary.json') -Encoding utf8
Close-PhaseZip $P
```

**Me envie depois:** `test_battery\<RUN_ID>\05_smokes.zip`

---

## 6) Bateria do control-plane / CLI / JSON snapshots

Esta fase é muito boa para encontrar problemas de config, parsing, estado, artifacts e compatibilidade entre módulos.

```powershell
$P = New-Phase '06_control_plane'
$cmds = @(
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

$results = @()
$idx = 0
foreach ($args in $cmds) {
    $idx += 1
    $name = ('{0:D2}_{1}' -f $idx, ($args -join '_').Replace('--','').Replace('/','_').Replace('\','_').Replace(':','_'))
    $log = Join-Path $P "$name.log"

    & $PY -m natbin.runtime_app @args *> $log
    $code = $LASTEXITCODE

    $results += [pscustomobject]@{
        name = $name
        args = $args
        exit_code = $code
        log = [System.IO.Path]::GetFileName($log)
    }
}

$results | ConvertTo-Json -Depth 6 | Out-File (Join-Path $P 'control_plane_summary.json') -Encoding utf8
Close-PhaseZip $P
```

**Me envie depois:** `test_battery\<RUN_ID>\06_control_plane.zip`

---

## 7) Bateria canônica do projeto + soak limitado

```powershell
$P = New-Phase '07_local_suite_and_soak'

& $PY scripts\tools\local_test_suite.py --repo-root . --preset full *> (Join-Path $P 'local_test_suite_full.log')
Write-ExitCode (Join-Path $P 'local_test_suite_full.exit.txt') $LASTEXITCODE

& $PY scripts\tools\runtime_soak.py --repo-root . --config config\multi_asset.yaml --max-cycles 12 --topk 3 --lookback-candles 2000 *> (Join-Path $P 'runtime_soak.log')
Write-ExitCode (Join-Path $P 'runtime_soak.exit.txt') $LASTEXITCODE

Get-ChildItem .\runs\tests\local_test_suite_*.json -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1 |
    Copy-Item -Destination (Join-Path $P 'local_test_suite_report.json') -Force

Get-ChildItem .\runs\control -Recurse -Filter runtime_soak_summary.json -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1 |
    Copy-Item -Destination (Join-Path $P 'runtime_soak_summary.json') -Force

Close-PhaseZip $P
```

**Me envie depois:** `test_battery\<RUN_ID>\07_local_suite_and_soak.zip`

---

## 8) Bateria de diagnóstico offline com bundles sanitizados

Esta é uma das mais importantes porque já empacota o contexto em ZIPs próprios.

```powershell
$P = New-Phase '08_offline_diag'

powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v4_1.ps1 `
    -Mode Offline `
    -StrictWarnings `
    -IncludeIsolatedPytest `
    -IncludeDocker *> (Join-Path $P 'offline_diag.log')

Write-ExitCode (Join-Path $P 'offline_diag.exit.txt') $LASTEXITCODE

Get-ChildItem .\diag_zips\diag_bundle_*.zip -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1 |
    Copy-Item -Destination (Join-Path $P 'diag_bundle_latest.zip') -Force

Get-ChildItem .\diag_zips\LATEST_SESSION.txt -ErrorAction SilentlyContinue |
    Copy-Item -Destination (Join-Path $P 'LATEST_SESSION.txt') -Force

Close-PhaseZip $P
```

**Me envie depois os dois:**
- `test_battery\<RUN_ID>\08_offline_diag.zip`
- `diag_zips\diag_bundle_<SESSION>.zip`

---

## 9) Bateria Docker / Compose / container image

Se Docker estiver disponível, rode esta fase também.

```powershell
$P = New-Phase '09_docker'

docker version *> (Join-Path $P 'docker_version.log')
Write-ExitCode (Join-Path $P 'docker_version.exit.txt') $LASTEXITCODE

docker compose version *> (Join-Path $P 'docker_compose_version.log')
Write-ExitCode (Join-Path $P 'docker_compose_version.exit.txt') $LASTEXITCODE

docker compose -f docker-compose.yml config *> (Join-Path $P 'docker_compose_base.rendered.yml')
Write-ExitCode (Join-Path $P 'docker_compose_base.exit.txt') $LASTEXITCODE

docker compose -f docker-compose.vps.yml config *> (Join-Path $P 'docker_compose_vps.rendered.yml')
Write-ExitCode (Join-Path $P 'docker_compose_vps.exit.txt') $LASTEXITCODE

docker compose -f docker-compose.prod.yml config *> (Join-Path $P 'docker_compose_prod.rendered.yml')
Write-ExitCode (Join-Path $P 'docker_compose_prod.exit.txt') $LASTEXITCODE

docker build --progress=plain -t thalor:test . *> (Join-Path $P 'docker_build.log')
Write-ExitCode (Join-Path $P 'docker_build.exit.txt') $LASTEXITCODE

docker run --rm thalor:test python -m pytest -q *> (Join-Path $P 'docker_pytest.log')
Write-ExitCode (Join-Path $P 'docker_pytest.exit.txt') $LASTEXITCODE

docker run --rm thalor:test python -m natbin.runtime_app status --repo-root /app --config config/multi_asset.yaml --json *> (Join-Path $P 'docker_runtime_status.log')
Write-ExitCode (Join-Path $P 'docker_runtime_status.exit.txt') $LASTEXITCODE

Close-PhaseZip $P
```

**Me envie depois:** `test_battery\<RUN_ID>\09_docker.zip`

---

## 10) Bateria de integração externa em PRACTICE

Só rode esta fase se você quiser validar broker/proxy/rede/transport real em ambiente controlado.

```powershell
$P = New-Phase '10_practice_integration'

powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v4_1.ps1 `
    -Mode Practice `
    -StrictWarnings `
    -PracticeConfig config\live_controlled_practice.yaml *> (Join-Path $P 'practice_diag.log')

Write-ExitCode (Join-Path $P 'practice_diag.exit.txt') $LASTEXITCODE

Get-ChildItem .\diag_zips\diag_bundle_*.zip -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1 |
    Copy-Item -Destination (Join-Path $P 'practice_diag_bundle_latest.zip') -Force

Close-PhaseZip $P
```

**Me envie depois os dois:**
- `test_battery\<RUN_ID>\10_practice_integration.zip`
- `diag_zips\diag_bundle_<SESSION>.zip`

---

## 11) Bateria de real preflight

Esta fase **não** é para enviar ordem live.
Ela serve só para validar readiness, guardrails, config, runtime e broker path.

```powershell
$P = New-Phase '11_real_preflight'

powershell -ExecutionPolicy Bypass -File .\scripts\tools\run_thalor_diagnostics_v4_1.ps1 `
    -Mode RealPreflight `
    -StrictWarnings `
    -RealConfig config\live_controlled_real.yaml *> (Join-Path $P 'real_preflight_diag.log')

Write-ExitCode (Join-Path $P 'real_preflight_diag.exit.txt') $LASTEXITCODE

Get-ChildItem .\diag_zips\diag_bundle_*.zip -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1 |
    Copy-Item -Destination (Join-Path $P 'real_preflight_diag_bundle_latest.zip') -Force

Close-PhaseZip $P
```

**Me envie depois os dois:**
- `test_battery\<RUN_ID>\11_real_preflight.zip`
- `diag_zips\diag_bundle_<SESSION>.zip`

---

## 12) Ordem recomendada de execução

1. `01_baseline`
2. `02_static_integrity`
3. `03_pytest_full`
4. `04_pytest_isolated`
5. `05_smokes`
6. `06_control_plane`
7. `07_local_suite_and_soak`
8. `08_offline_diag`
9. `09_docker`
10. `10_practice_integration` *(opcional, mas muito útil)*
11. `11_real_preflight` *(opcional e sem submit live)*

---

## 13) O que me enviar ao final

No mínimo:

- `01_baseline.zip`
- `02_static_integrity.zip`
- `03_pytest_full.zip`
- `04_pytest_isolated.zip`
- `05_smokes.zip`
- `06_control_plane.zip`
- `07_local_suite_and_soak.zip`
- `08_offline_diag.zip`
- `diag_bundle_<SESSION>.zip`

Se você também rodar Docker e integração externa:

- `09_docker.zip`
- `10_practice_integration.zip`
- `11_real_preflight.zip`

---

## 14) Comando único mais prático

Se você quiser automatizar quase tudo, use o script PowerShell que acompanha este kit:

```powershell
powershell -ExecutionPolicy Bypass -File .\thalor_professional_test_battery.ps1 -RepoRoot . -IncludeDocker
```

Para incluir prática:

```powershell
powershell -ExecutionPolicy Bypass -File .\thalor_professional_test_battery.ps1 -RepoRoot . -IncludeDocker -IncludePractice
```

Para incluir preflight real:

```powershell
powershell -ExecutionPolicy Bypass -File .\thalor_professional_test_battery.ps1 -RepoRoot . -IncludeDocker -IncludePractice -IncludeRealPreflight
```
