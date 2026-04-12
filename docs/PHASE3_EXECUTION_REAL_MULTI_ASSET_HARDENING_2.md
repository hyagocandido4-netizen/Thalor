# Phase 3 – Execution hardening real multi-asset (Package 3.2)

## Objetivo
Antes de abrir operação REAL com múltiplos assets, a camada de execução precisa falhar de forma segura, previsível e auditável.

## O que mudou

### 1. `execution.real_guard`
Novo bloco de configuração para governar a parte mais sensível da operação REAL:
- `require_env_allow_real`
- `allow_multi_asset_live`
- `serialize_submits`
- `submit_lock_path`
- `min_submit_spacing_sec`
- `max_pending_unknown_total`
- `max_open_positions_total`
- `recent_failure_window_sec`
- `max_recent_transport_failures`
- `post_submit_verify_enable`
- `post_submit_verify_timeout_sec`
- `post_submit_verify_poll_sec`

### 2. Bloqueio explícito de multi-asset REAL
Mesmo com `execution.mode=live`, se `multi_asset.enabled=true` e `execution.real_guard.allow_multi_asset_live=false`, a execução é bloqueada com o motivo:
- `real_multi_asset_not_enabled`

Isso evita ligar operação real multi-asset acidentalmente.

### 3. Armamento via variável de ambiente
O pacote antecipa o mesmo requisito já usado pelo adapter IQOption:
- `THALOR_EXECUTION_ALLOW_REAL=1`

Sem isso, o fluxo é bloqueado com:
- `real_env_not_armed`

### 4. Serialização de submits reais
Submits em modo REAL podem ser serializados por lock de arquivo cross-process:
- padrão: `runs/runtime_execution.submit.lock`

Isso endurece o comportamento quando o portfolio runner dispara múltiplos subprocessos.

### 5. Guard rails globais de execução
Antes do submit, o pacote verifica:
- total global de `accepted_open`
- total global de `submitted_unknown`
- espaçamento global entre submits
- falhas recentes de transporte (`reject`, `timeout`, `exception`)

### 6. Verificação pós-submit
Quando o submit é ACK e o modo é REAL, o pacote faz polling curto de `fetch_order()` e persiste o snapshot encontrado em `broker_orders`.

Isso **não substitui** a reconciliação; é uma verificação curta adicional para endurecer o ciclo.

## Artefato novo
Por scope:
- `runs/control/<scope>/execution_hardening.json`

Campos principais:
- `allowed`
- `reason`
- `live_real_mode`
- `multi_asset_enabled`
- `open_positions_total`
- `pending_unknown_total`
- `recent_transport_failures`
- `last_submit_at_utc`

## Comando novo
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app execution-hardening --repo-root . --config config/live_controlled_real.yaml --json
```

## Exemplo seguro
No `config/multi_asset_live_real.yaml.example`, o profile continua seguro por padrão:
- `execution.mode: live`
- `account_mode: REAL`
- `allow_multi_asset_live: false`

Assim, o profile existe, mas permanece bloqueado até decisão explícita do operador.
