# Documento 4 — Dicionário de Estados e Artifacts do Thalor

## Objetivo
Este documento existe para responder uma pergunta simples e crítica:

**“Quando o Thalor me mostra um estado, um campo ou um artifact, o que isso quer dizer de verdade?”**

O foco aqui não é explicar implementação linha por linha. O foco é dar ao **Hyago do futuro** um vocabulário operacional confiável para:

- interpretar outputs dos tools;
- entender se o sistema está saudável, degradado ou bloqueado;
- saber se um no-trade é saudável ou se ainda existe reparo pendente;
- reconhecer quais artifacts são fontes de verdade e quais são só derivados ou apoio.

Este documento deve ser lido junto com:
- `01_mapa_mestre_thalor.md`
- `02_runbook_operacional_thalor.md`
- `03_modos_e_perfis_thalor.md`

---

## 1. Leitura rápida
Se você estiver com pressa, estas são as interpretações mais importantes do Thalor hoje.

### Estado bom
- `provider_start_gate.ok = true`
- `provider_probe.ok = true`
- `canary_go_no_go.decision = GO_WAITING_SIGNAL` **ou** `GO_ACTIONABLE`
- `closure_state = healthy_waiting_signal` **ou** `actionable_scope_ready`
- `blocking_cp_meta_missing_scopes = 0`
- `blocking_gate_fail_closed_scopes = 0`

### Estado ruim
- `provider_ready_scopes = 0`
- `provider_session.status = error`
- `canary_go_no_go.decision = NO_GO_REPAIR`
- `closure_state = repair_needed`
- `missing_artifact_scopes > 0`
- `stale_artifact_scopes > 0`

### Estado saudável sem trade
- provider funcional
- canary fechado
- `decision = GO_WAITING_SIGNAL`
- `dominant_nontrade_reason = regime_block` ou outro motivo estrutural saudável

**Tradução:** o sistema está funcionando e só não encontrou entrada elegível naquele momento.

---

## 2. Vocabulário-base de payloads
Quase todos os tools do Thalor devolvem payloads JSON com alguns campos recorrentes.

### `kind`
Identifica que tipo de payload é aquele.

Exemplos:
- `provider_probe`
- `provider_stability_report`
- `provider_session_governor`
- `portfolio_canary_warmup`
- `evidence_window_scan`
- `portfolio_canary_signal_proof`
- `signal_artifact_audit`
- `canary_go_no_go`
- `canary_closure_report`

### `ok`
Indica se o tool, **na lógica dele**, considera o resultado aceitável.

Importante:
- `ok=true` **não significa necessariamente** “pode operar tudo”
- um payload pode vir `ok=true` e ainda ter `severity=warn`
- em canary, o contexto importa mais do que o `ok` isolado

### `severity`
Graduação operacional do estado:
- `ok` = saudável
- `warn` = degradado, mas ainda utilizável em certo envelope
- `error` = bloqueante para aquela superfície

### `actions`
Lista de próximos passos sugeridos pelo próprio tool.

No Thalor, `actions` é muito útil porque frequentemente já aponta o próximo comando correto.

---

## 3. Estados globais do canary

## `healthy_waiting_signal`
É o **estado-alvo mais comum** do canary conservador.

Significa:
- não há reparo bloqueante;
- provider não está estruturalmente instável;
- não há scope acionável agora;
- os no-trades observados são aceitáveis.

Tradução operacional:
**o sistema está fechado e saudável; pode continuar rodando aguardando próximo sinal.**

---

## `actionable_scope_ready`
Significa:
- provider funcional;
- canary fechado;
- existe pelo menos um scope com condição operacional candidata.

Tradução operacional:
**há um scope que merece evidência e atenção imediata.**

---

## `repair_needed`
Significa que ainda existe reparo realmente bloqueante antes de considerar o canary fechado.

Ele normalmente aparece quando há um ou mais destes casos:
- `missing_artifact_scopes > 0`
- `stale_artifact_scopes > 0`
- `blocking_cp_meta_missing_scopes > 0`
- `blocking_gate_fail_closed_scopes > 0`

Tradução operacional:
**não subir soak sério ainda; primeiro reparar artifacts/intelligence.**

---

## `provider_unstable`
Significa que o problema dominante é o provider, não o artifact.

Em geral aparece quando:
- `stability_state = unstable`
- existem `hard_blockers`
- ou `provider_ready_scopes <= 0`

Tradução operacional:
**não insistir em canary; estabilizar provider primeiro.**

---

## `observe_only_degraded_provider`
Estado intermediário onde o provider ainda está funcional, mas ruidoso.

Tradução operacional:
- manter envelope conservador;
- top-1;
- single-position;
- pacing serializado.

---

## 4. Decisões finais do `canary_go_no_go`

## `GO_WAITING_SIGNAL`
O melhor estado de operação contínua do canary.

Significa:
- canary fechado;
- sem blockers duros;
- nenhum sinal acionável agora;
- pode rodar observando normalmente.

**É estado verde.**

---

## `GO_ACTIONABLE`
Canary fechado com pelo menos um scope acionável.

**É estado verde com oportunidade operacional.**

---

## `NO_GO_REPAIR`
Há reparo bloqueante antes de considerar o canary fechado.

Em geral aponta para:
- artifact faltando;
- artifact stale;
- CP/gate fail-closed ainda dominante;
- convergência incompleta da inteligência.

**É estado vermelho do canary.**

---

## `NO_GO_PROVIDER_UNSTABLE`
A sessão remota do provider não está boa o suficiente para o envelope atual.

**É estado vermelho do provider.**

---

## `NO_GO_UNKNOWN`
Estado indefinido, inconsistente ou insuficientemente explicado.

Tratamento:
- não avançar
- gerar artifacts frescos
- capturar bundle e investigar

---

## `NO_GO_TOOLING_ERROR`
Falha do próprio tooling de fechamento.

Tratamento:
- validar se o problema é do tool ou do estado do sistema
- não considerar o canary “verde” por ausência de diagnóstico

---

## 5. Estados do provider

## `provider_probe`
É a **fonte de verdade primária** para sessão remota fresca.

Campos mais importantes:

### `transport_hint`
Mostra como o transport foi resolvido.

Campos úteis:
- `configured`
- `source`
- `scheme`
- `uses_socks`
- `pysocks_available`

Tradução:
isso responde **qual caminho de transport o runtime acha que está usando**.

### `shared_provider_session`
Mostra se a sessão compartilhada com o provider foi aberta com sucesso.

Campos úteis:
- `attempted`
- `ok`
- `reason`
- `latency_ms`
- `checked_at_utc`

### `remote_candles`
Mostra se o provider retornou amostra remota de candles.

### `remote_market_context`
Mostra se o provider retornou market context remoto.

### `provider_ready_scopes`
Resumo do número de scopes realmente prontos do ponto de vista do provider.

**Regra prática:** se `provider_ready_scopes = 0`, não subir shakedown/soak.

---

## `stability_state`
Vem do `provider_stability_report`.

### `stable`
Provider limpo o suficiente para o envelope atual.

### `degraded`
Provider ainda funcional, mas com ruído relevante.

Exemplos comuns:
- `upstream_digital_metadata`
- `websocket_lifecycle`

Tradução operacional:
**ainda pode operar no envelope conservador.**

### `unstable`
Provider ruim demais para o envelope atual.

Tradução operacional:
**não operar; estabilizar primeiro.**

---

## `governor_mode`
Vem do `provider_session_governor`.

### `normal`
Modo mais solto, usado quando o provider está estável.

### `serial_guarded`
Modo mais importante do canary atual.

Significa:
- pacing serializado;
- sem paralelismo real de execução;
- tempo entre scopes;
- orçamentos mais conservadores.

### `bootstrap_guarded`
Modo de bootstrap/reconvergência, quando a sessão ainda não está boa o suficiente mas não há blocker duro total.

### `hold_only`
Modo de retenção forte.

Tradução operacional:
- não expandir;
- não confiar para operar normalmente.

---

## 6. Estados de prontidão de scope
Estes aparecem muito em `evidence_window_scan` e `production_doctor`.

## `ready_for_cycle`
O scope tem condição mínima para entrar num ciclo normal de observação.

Não significa necessariamente que está pronto para prática/execução.

## `ready_for_practice`
O scope está operacionalmente íntegro o suficiente para o envelope practice atual.

Se `ready_for_cycle=true` e `ready_for_practice=false`, normalmente ainda existe algum problema de intelligence/control/artifact.

## `market_context_fresh`
O `market_context` local ainda está dentro do freshness budget.

## `market_open`
O scope está com mercado aberto no snapshot atual.

## `window_state`
Resumo curto do estado do scope naquele scan.

### `ready`
Scope pronto para atenção imediata.

### `watch`
Scope saudável, mas ainda sem ação operacional imediata.

### `hold`
Scope bloqueado por algum fator estrutural naquele momento.

---

## 7. Estados e campos da inteligência

## `pack_available`
Existe `pack.json` utilizável para o scope.

Tradução:
- `true` = inteligência consolidada visível para aquele scope
- `false` = a cadeia pode até ter produzido `eval`, mas ainda não publicou/convergiu o pack completo

## `eval_available`
Existe `latest_eval.json` utilizável para o scope.

Tradução:
- `true` = a avaliação existe
- `false` = nem a avaliação básica foi materializada

## `candidate_source`
Origem do artifact de candidate.

Valores importantes:
- `missing`
- `scoped`

### `missing`
O candidate que o audit esperava não foi encontrado.

### `scoped`
O candidate veio do artifact scoped atual do scope.

## `allocation_source`
Mesmo raciocínio do `candidate_source`, mas para allocation.

## `allow_trade`
Se a superfície de inteligência está permitindo trade.

No canary conservador, `allow_trade` pode continuar nulo/false mesmo com estado saudável, porque nem todo ciclo precisa virar trade.

## `feedback_blocked`
A inteligência/feedback vetou o trade.

## `feedback_reason`
Motivo do veto.

Exemplo importante:
- `portfolio_feedback_block:regime_block`

## `cp_available`
Indica se o componente CP conformal está efetivamente disponível.

## `cp_bootstrap_fallback`
Indica o modo de fallback de bootstrap de CP, quando configurado.

## `cp_bootstrap_fallback_active`
Indica que o fallback de bootstrap foi usado naquele caminho.

Nota curta:
esses campos existem porque o projeto precisou resolver uma fase em que `latest_eval` já existia, mas o gate CP ainda segurava tudo em fail-closed.

---

## 8. Razões de não-trade e blockers
Estas são algumas das palavras mais importantes do vocabulário do Thalor.

## `regime_block`
O regime atual não favorece operação.

Tradução operacional:
**não é bug; é no-trade saudável.**

## `below_ev_threshold`
O valor esperado ficou abaixo do mínimo configurado.

Tradução:
**há sinal/processamento, mas a qualidade/EV não justificou operação.**

## `not_in_topk_today`
O scope não entrou no top-k efetivo do dia/ciclo.

## `cp_reject`
O componente CP rejeitou a operação.

## `gate_fail_closed`
O gate foi para fail-closed.

Tradução:
**algo importante faltou ou ficou inconsistente, e o sistema escolheu bloquear em vez de arriscar.**

## `cp_meta_missing`
Falta material/meta necessários para a camada CP.

Historicamente, isso foi um dos grandes blockers do canary.

## `missing_artifact`
O artifact esperado simplesmente não existe.

## `stale_artifact`
O artifact existe, mas está velho demais para a decisão atual.

## `candidate_error`
O candidate não foi só “hold”; ele deu erro no caminho de geração/leitura.

---

## 9. Categorias de ruído operacional
Estas aparecem com frequência no `provider_stability_report`.

## `upstream_digital_metadata`
Ruído vindo do upstream da IQ em metadata digital.

Exemplo comum:
- `missing_underlying_list`

Leitura correta:
- ruim, mas normalmente **não é falha do proxy Decodo**;
- pode manter o provider como `degraded` sem colapsar a sessão.

## `websocket_lifecycle`
Ruído de ciclo de vida do WebSocket.

Leitura correta:
- tratar com pacing conservador;
- evitar bursts e paralelismo desnecessário.

## `intelligence_cp_meta`
Ruído/dívida da camada de inteligência CP.

## `strategy_no_trade`
Categoria importante porque significa que o sistema está vivo, mas sem entrada elegível.

Exemplos:
- `regime_block`
- `cp_reject`
- `below_ev_threshold`

Leitura correta:
**isso é comportamento normal do sistema conservador.**

---

## 10. Artifacts mais importantes do projeto

## 10.1 Artifacts de controle do repositório
Vivem tipicamente em:

```text
runs/control/_repo/
```

Os mais importantes hoje são:

- `provider_probe.json`
- `provider_stability.json`
- `provider_session_governor.json`
- `portfolio_canary_warmup.json`
- `evidence_window_scan.json`
- `portfolio_canary_signal_proof.json`
- `portfolio_canary_signal_scan.json`
- `signal_artifact_audit.json`

Esses arquivos são a espinha dorsal do diagnóstico atual do canary.

---

## 10.2 Artifacts scoped de controle
Vivem tipicamente em:

```text
runs/control/<scope_tag>/
```

Exemplos importantes:
- `doctor.json`
- `provider_probe.json`
- `intelligence.json`
- `breaker.json`
- `practice.json`
- `execution.json`

Esses ajudam a responder **o que aconteceu especificamente com um scope**.

---

## 10.3 Dados locais

### Candles locais
```text
data/market_<scope>.sqlite3
```

### Dataset por scope
```text
data/datasets/<scope_tag>/dataset.csv
```

### Market context local
```text
runs/market_context_<scope_tag>.json
```

Esses três artifacts respondem se a base local está convergida ou stale.

---

## 10.4 Artifacts de inteligência
Vivem tipicamente em:

```text
runs/intelligence/<scope_tag>/
```

Os mais importantes:
- `latest_eval.json`
- `pack.json`
- `drift_state.json`
- `retrain_status.json`
- `retrain_plan.json`

Leitura prática:
- `latest_eval.json` = avaliação do scope
- `pack.json` = consolidação operacional da inteligência
- `drift_*` / `retrain_*` = contexto de drift e manutenção do modelo

---

## 10.5 Artifacts globais de portfolio

### Último ciclo
```text
runs/portfolio_cycle_latest.json
```

### Última alocação
```text
runs/portfolio_allocation_latest.json
```

Esses dois são a forma mais rápida de ver:
- o que o runtime acabou de fazer;
- o que foi suprimido;
- o que foi selecionado.

---

## 10.6 Logs estruturados

### Runtime geral
```text
runs/logs/runtime_structured.jsonl
```

### Transport
```text
runs/logs/network_transport.jsonl
```

### Request-level metrics
```text
runs/logs/request_metrics.jsonl
```

Leitura prática:
- `runtime_structured` = linha do tempo geral do processo
- `network_transport` = saúde real do caminho de rede/proxy
- `request_metrics` = latência, operação e sucesso/falha por request

---

## 10.7 Bancos SQLite de runtime

### Controle
```text
runs/runtime_control.sqlite3
```

### Execução
```text
runs/runtime_execution.sqlite3
```

Leitura prática:
- `runtime_control` = breaker/control plane
- `runtime_execution` = intents, submit, broker orders, reconcile

---

## 11. Como interpretar combinações importantes

## Caso A — sistema saudável, sem trade
Se você vir algo como:
- `GO_WAITING_SIGNAL`
- `healthy_waiting_signal`
- `provider_ready_scopes > 0`
- `dominant_nontrade_reason = regime_block`

Tradução:
**o sistema está funcionando e só não houve entrada elegível.**

---

## Caso B — provider saudável, canary ainda vermelho
Se você vir algo como:
- `provider_probe.ok = true`
- `provider_ready_scopes = 6`
- `NO_GO_REPAIR`
- `missing_artifact_scopes > 0`

Tradução:
**o provider está bom, mas o gargalo é artifact/intelligence.**

---

## Caso C — `eval_available=true` e `pack_available=false`
Tradução:
**a cadeia materializou avaliação, mas ainda não convergiu o pack final.**

Historicamente, isso foi uma pista importante para localizar gargalos do canary.

---

## Caso D — `watch_scopes > 0` e `actionable_scopes = 0`
Tradução:
**há scopes vivos e interessantes, mas ainda sem motivo para execução.**

---

## Caso E — `provider_state = degraded`, mas `provider_ready_scopes > 0`
Tradução:
**o provider está ruidoso, mas ainda operável no envelope conservador.**

Não confundir isso com “provider morto”.

---

## 12. Comandos mínimos para consultar estados

### Provider fresco
```powershell
.\scripts\tools\provider_start_gate.cmd --config config\practice_portfolio_canary.yaml --all-scopes --consecutive-ok 2 --json
```

### Warmup do canary
```powershell
.\scripts\tools\portfolio_canary_warmup.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
```

### Scan de janela
```powershell
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
```

### GO/NO-GO final
```powershell
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
```

### Closure consolidado
```powershell
.\scripts\tools\portfolio_canary_closure_report.cmd --config config\practice_portfolio_canary.yaml --json
```

---

## 13. Regras práticas para não se perder

1. **Não confie em um campo isolado.** Sempre interprete estado + contexto.
2. **`provider_probe` fresco vale mais do que artifact velho verde.**
3. **`GO_WAITING_SIGNAL` não é problema; é estado saudável.**
4. **`NO_GO_REPAIR` quase sempre é artifact/intelligence, não provider.**
5. **`degraded` não é necessariamente abort.** Pode ser envelope conservador válido.
6. **`regime_block` é motivo operacional legítimo de não-trade.**
7. **Se `pack_available=false`, ainda falta convergência da inteligência.**
8. **Se `missing_artifact_scopes > 0`, o canary ainda não fechou.**

---

## 14. Intenção final deste documento
Se este documento estiver funcionando como deveria, você deve conseguir olhar para um output do Thalor e pensar algo como:

- “isso é provider ou artifact?”
- “isso é no-trade saudável ou bloqueio?”
- “isso é degradado operável ou instável?”
- “isso é problema de market context, de CP, de pack, de candidate ou de provider?”

Quando isso acontecer sem esforço excessivo, o dicionário cumpriu sua função.
