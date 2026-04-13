# Thalor — Plano de ataque em partes e entrega da Parte 1

## Ordem real de prioridade

### Parte 1 — Verdade operacional + blockers internos determinísticos
Objetivo: impedir falso verde operacional e blindar a hidratação do circuit breaker contra drift de schema.

Escopo desta parte:
- tornar `provider_stability`, `provider_session_governor`, `closure_report` e `canary_go_no_go` **freshness-aware**;
- forçar refresh ativo do `provider-probe` quando o artifact estiver stale;
- evitar que relatórios antigos continuem parecendo válidos horas depois;
- blindar `CircuitBreakerSnapshot` para aceitar payloads parciais/estendidos sem quebrar o runtime.

### Parte 2 — Telemetria request-level de verdade
Objetivo: fazer `request_metrics.jsonl` deixar de ser só um “initialized” e virar telemetria útil de connect/calls/timeouts/failures.

### Parte 3 — Resiliência de conectividade e pacing
Objetivo: reduzir flapping, budgets ruins de retry, pacing inadequado e thrash em janelas ruins de provider.

### Parte 4 — Headroom multi-asset e ciclo de 300s
Objetivo: reduzir estouro de budget por scope/candle, especialmente nos scopes mais caros.

### Parte 5 — Dívida de inteligência/cp_meta e limpeza final de sinais
Objetivo: remover suppressões evitáveis por `cp_meta`, melhorar cobertura e deixar a coleta de entradas mais rica.

## O que foi resolvido na Parte 1

### 1) Artifacts stale não podem mais mentir sozinhos
- `provider_stability` agora calcula freshness dos artifacts de controle.
- `provider_probe` stale passa a disparar refresh automático.
- `provider_session_governor` não confia mais cegamente em `provider_stability.json` antigo.
- `canary_go_no_go` passou a chamar o closure report com `--active-provider-probe`.
- `portfolio_canary_closure_report` passa a operar com `active-provider-probe=true` por padrão.

### 2) CLI mais segura por padrão
- `provider_stability_report` agora usa active provider probe por padrão.
- `provider_session_governor` agora usa active provider probe por padrão.
- ainda existe caminho passivo explícito via flag `--passive-provider-probe`.

### 3) Compatibilidade robusta do circuit breaker
- `CircuitBreakerSnapshot.from_mapping(...)` foi adicionado.
- unknown fields passam a ser ignorados de forma segura.
- parsing de datetime/int/bool foi centralizado.
- `RuntimeControlRepository.load_breaker()` agora usa essa hidratação compatível.

## Arquivos alterados
- `src/natbin/ops/diagnostic_utils.py`
- `src/natbin/ops/provider_stability.py`
- `src/natbin/ops/provider_session_governor.py`
- `src/natbin/runtime/failsafe.py`
- `src/natbin/state/control_repo.py`
- `scripts/tools/portfolio_canary_closure_report.py`
- `scripts/tools/canary_go_no_go.py`
- `tests/test_part29_truth_and_breaker_compat.py`

## Validação executada
Suite direcionada da Parte 1:
- `44 passed`

Cobriu:
- provider stability
- provider session governor
- signal proof / governed canary
- canary closure
- breaker hygiene / stale breaker heal
- compatibilidade nova do breaker

## O que ainda NÃO foi atacado nesta parte
- request metrics request-level ainda não foi expandido;
- pacing/retry/connectivity tuning ainda não foi mexido nesta parte;
- headroom multi-asset ainda não foi otimizado;
- cp_meta/intelligence debt ainda não foi tratado aqui.
