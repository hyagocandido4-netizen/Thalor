# Thalor — sequência correta para resolver o bloqueio de conexão e iniciar um long soak conservador

## Verdade operacional

- O bloqueio atual é **fresh provider session indisponível** no momento do `provider-probe`, com `WinError 10060` em `gate.decodo.com:7000` após 8 tentativas.
- O transporte não aparece quebrado: o próprio probe mostra `scheme = socks5h`, `source = file:transport_endpoint` e `pysocks_available = true`.
- Os relatórios `canary_go_no_go`, `provider_session_governor`, `provider_stability_report` e `portfolio_canary_signal_proof` que vieram verdes/warn estavam apoiados em artifacts antigos da sessão de `01:12–01:14Z`, enquanto o probe que falhou foi às `05:30–05:32Z`.
- O caminho correto é: **fresh provider-probe -> warmup/regeneração local -> fresh active stability/governor -> signal proof -> go/no-go -> long observe**.
- Não restaure `runs.zip` antes disso, porque ele reintroduz artifacts velhos. Se quiser acelerar o rebuild, restaure só `data.zip`; depois deixe o warmup atualizar o resto.

## Comandos manuais (PowerShell)

### 1) Entrar na raiz e preparar ambiente
```powershell
Set-Location C:\Users\hyago_31k5yin\Documents\Thalor
$env:PYTHONPATH = "src"
New-Item -ItemType Directory -Force runs, runs\logs, data, data\datasets | Out-Null
```

### 2) Opcional: restaurar **apenas** `data.zip`
```powershell
Expand-Archive -LiteralPath .\data.zip -DestinationPath . -Force
```

### 3) Não restaurar `runs.zip`
Esse ZIP contém control artifacts antigos; para este fluxo, ele atrapalha mais do que ajuda.

### 4) Esperar o provider voltar de verdade
```powershell
$probeOk = $false
while (-not $probeOk) {
  $raw = .\.venv\Scripts\python.exe -m natbin.runtime_app provider-probe --repo-root . --config config\practice_portfolio_canary.yaml --all-scopes --json 2>&1
  $raw | Tee-Object -FilePath .\runs\logs\provider_probe_latest.txt
  $probeOk = ($raw -join "`n") -match '"ok"\s*:\s*true' -and ($raw -join "`n") -match '"shared_provider_session"[\s\S]*?"ok"\s*:\s*true'
  if (-not $probeOk) { Start-Sleep -Seconds 30 }
}
```

### 5) Warmup/regeneração dos 6 scopes
```powershell
.\.venv\Scripts\python.exe .\scripts\tools\portfolio_canary_warmup.py --repo-root . --config config\practice_portfolio_canary.yaml --all-scopes --refresh-stability --json
```

### 6) Fresh active provider stability
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app provider-stability-report --repo-root . --config config\practice_portfolio_canary.yaml --all-scopes --active-provider-probe --refresh-probe --json
```

### 7) Fresh active provider governor
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app provider-session-governor --repo-root . --config config\practice_portfolio_canary.yaml --all-scopes --active-provider-probe --refresh-stability --json
```

### 8) Fresh signal proof
```powershell
.\.venv\Scripts\python.exe .\scripts\tools\portfolio_canary_signal_proof.py --repo-root . --config config\practice_portfolio_canary.yaml --all-scopes --json
```

### 9) Go/No-Go final
```powershell
.\.venv\Scripts\python.exe .\scripts\tools\canary_go_no_go.py --repo-root . --config config\practice_portfolio_canary.yaml --json
```

### 10) Iniciar o long soak conservador de ~8 horas
96 ciclos de 300s ≈ 8 horas.
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe --repo-root . --config config\practice_portfolio_canary.yaml --topk 1 --lookback-candles 2000 --quota-aware-sleep --precheck-market-context --max-cycles 96 2>&1 | Tee-Object -FilePath .\runs\logs\portfolio_observe_long_soak.log
```

### 11) Monitoramento passivo em outro terminal
```powershell
Get-Item runs\logs\network_transport.jsonl, runs\logs\request_metrics.jsonl, runs\logs\runtime_structured.jsonl, runs\portfolio_cycle_latest.json, runs\portfolio_allocation_latest.json | Select-Object Name,Length,LastWriteTime
```

```powershell
Get-Content runs\logs\runtime_structured.jsonl -Tail 10
```

```powershell
Get-Content runs\logs\network_transport.jsonl -Tail 10
```

### 12) Encerramento e bundles finais
```powershell
.\.venv\Scripts\python.exe .\scripts\tools\capture_portfolio_canary_bundle.py --repo-root . --config config\practice_portfolio_canary.yaml
.\.venv\Scripts\python.exe .\scripts\tools\capture_canary_closure_bundle.py --repo-root . --config config\practice_portfolio_canary.yaml
```

## Atalho pronto
Se preferir um único launcher externo, use:
```powershell
python .\thalor_start_practice_canary_long_soak.py --repo-root . --config config\practice_portfolio_canary.yaml --observe-max-cycles 96
```

Esse launcher fica esperando o provider-probe passar de verdade, roda warmup, stability, governor, signal proof, go/no-go e só então inicia o long soak.
