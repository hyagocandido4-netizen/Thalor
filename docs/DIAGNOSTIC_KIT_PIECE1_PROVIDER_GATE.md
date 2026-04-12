# Diagnostic Kit – Peça 1: `provider-probe` e `production-gate`

Esta é a primeira peça do kit diagnóstico canônico do Thalor.

O objetivo é reduzir o tempo entre:
- “o provider está acessível?”
- “o scope consegue operar?”
- “os 6 assets estão realmente prontos ao mesmo tempo?”

## 1. `provider-probe`

Comando focado no caminho broker-facing.

### Escopo único

```bash
python -m natbin.runtime_app provider-probe \
  --repo-root . \
  --config config/live_controlled_real.yaml \
  --json
```

### Todos os scopes de um profile multi-asset

```bash
python -m natbin.runtime_app provider-probe \
  --repo-root . \
  --config config/multi_asset.yaml \
  --all-scopes \
  --json
```

### Modo passivo

Não abre sessão remota. Útil para auditar config, secrets, transporte, artifacts e coerência de modos.

```bash
python -m natbin.runtime_app provider-probe \
  --repo-root . \
  --config config/live_controlled_real.yaml \
  --passive \
  --json
```

## O que o `provider-probe` verifica

- security posture básica
- credenciais do broker
- dependência do broker
- sessão compartilhada com o provider
- hints de transporte/proxy
- compatibilidade SOCKS/PySocks
- alinhamento entre `execution.account_mode` e `broker.balance_mode`
- market context local por scope
- DB local de candles por scope
- leitura remota de candles por scope
- leitura remota de market context por scope

## Artifacts emitidos

Por scope:

```text
runs/control/<scope_tag>/provider_probe.json
```

Consolidado de profile multi-asset:

```text
runs/control/_repo/provider_probe.json
```

---

## 2. `production-gate`

Comando consolidado para responder:

> “Posso operar agora?”

Ele combina:
- `provider-probe`
- `doctor`
- `release readiness`

### Escopo único

```bash
python -m natbin.runtime_app production-gate \
  --repo-root . \
  --config config/live_controlled_real.yaml \
  --probe-provider \
  --json
```

### Todos os scopes do profile multi-asset

```bash
python -m natbin.runtime_app production-gate \
  --repo-root . \
  --config config/multi_asset.yaml \
  --all-scopes \
  --probe-provider \
  --json
```

## O que o `production-gate` responde

- quais scopes estão prontos para ciclo
- quais scopes estão prontos para live
- quais scopes estão com provider realmente operacional
- blockers categorizados por:
  - `provider`
  - `data`
  - `guardrail`
  - `config`
  - `security`
  - `intelligence`
  - `runtime`
- ações recomendadas em ordem curta e operacional

## Artifacts emitidos

Por scope:

```text
runs/control/<scope_tag>/production_gate.json
```

Consolidado de profile multi-asset:

```text
runs/control/_repo/production_gate.json
```

---

## 3. Fluxo recomendado para o objetivo final de 6 assets

### Passo A — validar provider e transporte

```bash
python -m natbin.runtime_app provider-probe \
  --repo-root . \
  --config config/multi_asset.yaml \
  --all-scopes \
  --json
```

### Passo B — consolidar readiness operacional

```bash
python -m natbin.runtime_app production-gate \
  --repo-root . \
  --config config/multi_asset.yaml \
  --all-scopes \
  --probe-provider \
  --json
```

### Passo C — quando o gate estiver verde

```bash
python -m natbin.runtime_app portfolio observe --repo-root . --config config/multi_asset.yaml --once --json
```

---

## 4. Interpretação rápida

### `provider-probe` com erro

O problema ainda está no caminho broker-facing.

Procure primeiro por:
- `provider_dependency`
- `provider_credentials`
- `provider_session`
- `remote_candles`
- `remote_market_context`
- `mode_alignment`
- `transport_hint`

### `provider-probe` verde mas `production-gate` vermelho

O provider está bom, mas o bloqueio mudou para:
- guard rails
- stale artifacts
- dataset
- circuit breaker
- release readiness
- intelligence

---

## 5. Objetivo operacional

O estado desejado para os 6 assets é:
- `provider_probe.summary.scope_errors == 0`
- `production_gate.ready_for_all_scopes == true`
- `production_gate.summary.ready_for_live_count == 6`
- `production_gate.summary.provider_ready_count == 6`

Quando isso acontecer, o próximo passo natural é validar o ciclo portfolio/observe completo e então avançar para o restante do kit diagnóstico.
