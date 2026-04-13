# Thalor — Parte 2

## Objetivo
Transformar `request_metrics.jsonl` em telemetria **realmente útil** para soak longo.

## Problema dominante atacado
O arquivo `runs/logs/request_metrics.jsonl` estava sendo criado, mas continha praticamente só `request_metrics_initialized`. Isso acontecia por dois motivos combinados:

1. `RequestMetrics` só escrevia no JSONL eventos de **init** e **summary**.
2. O `IQClient` só contabilizava métricas de `connect`, deixando de fora o grosso do tráfego (`get_candles`, `get_all_profit`, `get_optioninfo_v2`, submits e reads auxiliares).

## Correções desta parte
- `RequestMetrics` agora emite **eventos por requisição** em `request_metrics.jsonl`.
- Esses eventos carregam contexto operacional útil:
  - `operation`
  - `target`
  - `success`
  - `latency_ms`
  - `attempt`
  - `retries`
  - `reason` (em falha)
  - metadados do transport (`transport_scheme`, `transport_target`, `transport_host`, `transport_port`, `transport_source`, `transport_type`)
- `RequestMetrics` agora pode emitir **sumários periódicos** durante a execução (`emit_summary_every_requests`).
- `RequestMetrics` registra **summary on close** via `atexit`, reduzindo o risco de terminar uma sessão sem fechamento de métricas.
- `IQClient` agora registra métricas detalhadas para:
  - `connect`
  - `_call_with_retries(...)` (caminho usado por `get_all_profit`, `get_all_open_time`, `buy_option`, `get_async_order`, `get_betinfo`, `get_option_open_by_other_pc`)
  - `get_candles`
  - `get_recent_closed_options`
- Profiles principais passaram a explicitar:
  - `emit_request_events: true`
  - `emit_summary_every_requests: 25`
- A Parte 1 foi mantida neste overlay cumulativo.

## Impacto esperado na próxima run
Depois deste patch, o arquivo `runs/logs/request_metrics.jsonl` deve conter, além dos inits:
- `request_metrics_request`
- `request_metrics_summary`

## Validação rápida
Depois de extrair o ZIP na raiz:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest -q tests\test_request_metrics.py tests\test_iq_client_network_transport.py
```

Durante uma run real:

```powershell
Get-Content runs\logs\request_metrics.jsonl -Tail 20
```

Você deve ver eventos `request_metrics_request` com latência e contexto de operação.
