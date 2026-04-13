# Atlas de Configuração do Thalor

## Finalidade

Este documento explica **como a configuração do Thalor governa o comportamento real do sistema**.

Ele existe para responder quatro perguntas práticas:

1. **qual arquivo/profile devo usar para o objetivo atual?**
2. **qual bloco de configuração controla qual comportamento?**
3. **o que é seguro mexer e o que é sensível/perigoso?**
4. **o que eu preciso revalidar depois de mudar alguma coisa?**

Este não é um catálogo exaustivo de todos os campos do YAML. É um **atlas operacional**: o objetivo é dar domínio mental e orientar mudança segura.

---

## 1. Modelo mental da configuração do Thalor

No estado atual do projeto, a configuração do Thalor deve ser lida assim:

- **o YAML do profile é a fonte principal de comportamento**;
- **o `.env` é, por padrão, mais apropriado para segredos, postura de deployment e compatibilidade**, não para tuning agressivo de comportamento;
- **segredos do broker** podem vir de arquivo externo (`THALOR_SECRETS_FILE` / `security.secrets_file` / arquivos separados de email e senha);
- `config/base.yaml` é a **fundação comum**;
- profiles como `config/practice_portfolio_canary.yaml` e `config/live_controlled_practice.yaml` são a **verdade operacional** do que realmente será executado.

### Regra prática

Quando você quiser entender “como o bot vai se comportar”, olhe nesta ordem:

1. **profile YAML selecionado**
2. `config/base.yaml` (se o profile herdar dele ou repetir sua filosofia)
3. eventuais `THALOR__*` exportados no processo
4. overlay de segredos externos
5. `.env` local apenas como apoio/compatibilidade

### Regra de ouro

**Não trate `.env` como painel principal de comportamento do bot.**

No desenho atual, comportamento deve viver principalmente no profile YAML. Isso reduz ambiguidade, facilita auditoria e evita “efeitos invisíveis”.

---

## 2. Arquivos e perfis canônicos atuais

### 2.1 `config/base.yaml`

Função: **fundação comum** do projeto.

Use quando:
- quiser entender defaults globais;
- quiser saber qual é o “piso comportamental” do sistema;
- quiser criar ou revisar outro profile.

Não use como:
- profile operacional principal para soak/canary;
- lugar para tuning específico de uma sessão.

### 2.2 `config/practice_portfolio_canary.yaml`

Função: **profile canônico de canary/observação conservadora em PRACTICE**.

É o profile mais importante hoje para:
- preflight;
- warmup;
- shakedown;
- soak controlado;
- coleta operacional rica com risco contido.

Características qualitativas atuais:
- `execution.enabled: true`
- `execution.mode: live`
- `execution.account_mode: PRACTICE`
- `broker.balance_mode: PRACTICE`
- `multi_asset.enabled: true`
- `max_parallel_assets: 1`
- `portfolio_topk_total: 1`
- `portfolio_hard_max_positions: 1`
- `decision.cp_bootstrap_fallback: auto`

Tradução operacional:
- observa vários scopes;
- executa de forma extremamente conservadora;
- mantém top-1 / single-position;
- é o melhor envelope atual para validação séria sem ir para REAL.

### 2.3 `config/live_controlled_practice.yaml`

Função: **profile single-asset/controlado em PRACTICE**.

Características qualitativas atuais:
- `execution.enabled: true`
- `execution.mode: live`
- `execution.account_mode: PRACTICE`
- `broker.balance_mode: PRACTICE`
- `multi_asset.enabled: false`

Use quando:
- quiser validar operação controlada sem a camada multi-asset do canary;
- quiser um caminho mais simples do que o canary portfolio.

### 2.4 `config/live_controlled_real.yaml`

Função: **profile real/live controlado**.

Características qualitativas atuais:
- `execution.enabled: true`
- `execution.mode: live`
- `execution.account_mode: REAL`
- `broker.balance_mode: REAL`
- `multi_asset.enabled: false`

Status operacional recomendado hoje:
- **não usar casualmente**;
- profile de promoção controlada, não de exploração.

### 2.5 `config/multi_asset.yaml`

Função: **profile de engenharia/exploração multi-asset**, não profile canônico do canary.

Características qualitativas atuais:
- `multi_asset.enabled: true`
- `max_parallel_assets: 3`
- `portfolio_topk_total: 3`
- `portfolio_hard_max_positions: 2`
- `execution.enabled: false`
- `execution.mode: disabled`
- `provider: fake`

Use quando:
- quiser experimentar envelope multi-asset mais largo;
- quiser validar paralelismo/partitioning;
- estiver em contexto de engenharia, não de run conservador principal.

### 2.6 `*.example`

Função: **template/documentação**, não profile ativo.

Use para:
- onboarding;
- referência de estrutura;
- criação de novos perfis.

Não use como verdade operacional direta sem revisão.

---

## 3. Atlas por bloco lógico

Abaixo, cada bloco é descrito por:
- **o que controla**
- **campos mais importantes**
- **o que costuma ser seguro mexer**
- **o que é sensível/perigoso**
- **como revalidar**

---

## 3.1 `runtime`

### O que controla

Comportamento do ciclo e da higiene de runtime:
- profile ativo;
- tratamento de artifacts stales;
- lifecycle de startup;
- housekeeping de runtime.

### Campos importantes

- `runtime.profile`
- `runtime.quota_aware_sleep`
- `runtime.stale_artifact_after_sec`
- `runtime.startup_invalidate_stale_artifacts`
- `runtime.startup_lifecycle_artifacts`
- `runtime.lock_refresh_enable`

### Mudanças normalmente seguras

- nome do profile;
- ajustes moderados de retenção/lifecycle;
- `quota_aware_sleep` quando você sabe por que quer mudar.

### Mudanças sensíveis

- `stale_artifact_after_sec`:
  - muito baixo = artefato “morre” cedo demais;
  - muito alto = artefato velho parece fresco.
- desligar invalidação/lifecycle de startup sem motivo forte.

### Revalidação mínima após mudança

- `provider_start_gate`
- `portfolio_canary_warmup`
- `evidence_window_scan`

Se mexer na semântica de stale/freshness, faça também um shakedown curto.

---

## 3.2 `broker`

### O que controla

Parâmetros do adapter do broker/IQ:
- provider;
- balance mode lógico;
- retries/backoff;
- timeout de connect;
- pacing de API.

### Campos importantes

- `broker.provider`
- `broker.balance_mode`
- `broker.connect_retries`
- `broker.connect_sleep_s`
- `broker.connect_sleep_max_s`
- `broker.timeout_connect_s`
- `broker.api_throttle_min_interval_s`
- `broker.api_throttle_jitter_s`

### Mudanças normalmente seguras

- pacing de API (`api_throttle_*`) com ajustes pequenos;
- tuning leve de retry/sleep em contexto de conectividade instável.

### Mudanças sensíveis

- `balance_mode`
- `timeout_connect_s`
- orçamento de retries

Mudanças aqui mexem diretamente em:
- latência do `provider-probe`;
- tempo de recuperação;
- risco de thrash em janela ruim.

### Revalidação mínima após mudança

- `provider_start_gate`
- `provider_stability_report`
- shakedown curto

---

## 3.3 `data`

### O que controla

Base local de candles e dataset:
- DB principal/templated;
- CSV de dataset;
- tamanho do lookback de decisão;
- lote de coleta.

### Campos importantes

- `data.db_path`
- `data.dataset_path`
- `data.lookback_candles`
- `data.max_batch`

### Mudanças normalmente seguras

- `max_batch` em ajustes moderados;
- caminhos, quando a intenção é reorganização explícita.

### Mudanças sensíveis

- `lookback_candles`
- qualquer mudança de path em contexto multi-asset sem checar partitioning

### Regra prática

- no canary/multi-asset, a peça importante é a combinação com `multi_asset.partition_data_paths` e templates;
- **não limpe `data/` por impulso**.

### Revalidação mínima após mudança

- `asset prepare` em um scope
- `portfolio_canary_warmup`
- `evidence_window_scan`

---

## 3.4 `decision`

### O que controla

Núcleo da política de decisão e gating:
- tipo de gate;
- threshold;
- uso de CP;
- bounds de regime;
- pacing de decisão;
- comportamento fail-closed.

### Campos importantes

- `decision.gate_mode`
- `decision.cp_bootstrap_fallback`
- `decision.meta_model`
- `decision.thresh_on`
- `decision.threshold`
- `decision.cp_alpha`
- `decision.cpreg.*`
- `decision.tune_dir`
- `decision.bounds.*`
- `decision.fail_closed`

### Mudanças normalmente seguras

- ajustes pequenos de threshold, com revalidação;
- `cp_bootstrap_fallback` apenas no envelope practice/canary, quando já entendido.

### Mudanças sensíveis

- `gate_mode`
- `fail_closed`
- `cp_bootstrap_fallback`
- `bounds`
- `cpreg`

Esses campos mudam qualitativamente a estratégia, o tipo de bloqueio e o significado do canary.

### Revalidação mínima após mudança

- `portfolio_cp_meta_maintenance`
- `evidence_window_scan`
- `canary_go_no_go`
- shakedown curto

### Regra forte

**Nunca altere `decision.*` sem considerar que você está mudando comportamento do bot, não só “tuning”.**

---

## 3.5 `quota`

### O que controla

Cadência diária e caps de pacing comportamental.

### Campos importantes

- `quota.target_trades_per_day`
- `quota.hard_max_trades_per_day`
- `quota.pacing_morning_cap`
- `quota.pacing_afternoon_cap`
- janelas `*_until_hhmm`

### Mudanças normalmente seguras

- pequenos ajustes de meta diária em PRACTICE;
- ajustes de pacing quando o objetivo é distribuição temporal.

### Mudanças sensíveis

- aumentar hard max sem revisar o resto do envelope;
- mudar pacing em conjunto com execution limits sem novo shakedown.

### Revalidação mínima após mudança

- `evidence_window_scan`
- `canary_go_no_go`
- soak curto/médio

---

## 3.6 `autos`

### O que controla

Comportamento da camada automática de summaries/eval e algumas features legadas de automação.

### Campos importantes

- `autos.enabled`
- `autos.summary_fail_closed`
- `autos.legacy_summary_fallback`
- `autos.min_days_used`
- `autos.min_trades_eval`

### Mudanças normalmente seguras

- ajustes leves de mínimos de uso/trades em contexto de análise.

### Mudanças sensíveis

- desligar fail-closed sem entender impacto;
- religar fallback legado sem motivo muito forte.

---

## 3.7 `network.transport`

### O que controla

Camada de transporte/proxy do broker:
- origem do endpoint;
- healthcheck;
- retries/backoff;
- quarentena;
- structured log de transport.

### Campos importantes

- `network.transport.enabled`
- `network.transport.endpoint_file`
- `network.transport.healthcheck_mode`
- `network.transport.healthcheck_url`
- `network.transport.fail_open_when_exhausted`
- `network.transport.structured_log_path`

### Mudanças normalmente seguras

- `healthcheck_url` apenas quando realmente necessário;
- structured log path, se houver reorganização.

### Mudanças sensíveis/perigosas

- `endpoint_file`
- `fail_open_when_exhausted`
- semântica de healthcheck

### Regra crítica

- `secrets/transport_endpoint` é sagrado e não deve ser “ajustado no impulso”;
- o transport atual foi estabilizado com bastante trabalho; não mexa sem razão concreta.

### Revalidação mínima após mudança

- `provider_start_gate`
- `provider_probe`
- `provider_stability_report`

---

## 3.8 `observability`

### O que controla

Telemetria e logs do runtime.

### Campos importantes

- `observability.request_metrics.enabled`
- `observability.request_metrics.structured_log_path`
- `observability.request_metrics.emit_request_events`
- `observability.request_metrics.emit_summary_every_requests`
- `observability.structured_logs_enable`
- `observability.structured_logs_path`
- `observability.loop_log_enable`
- `observability.incidents_enable`
- `observability.decision_snapshots_enable`

### Mudanças normalmente seguras

- caminhos de log;
- habilitar/desabilitar métricas adicionais em ambiente local.

### Mudanças sensíveis

- desligar request metrics ou structured logs em fase de validação;
- alterar demais o ritmo dos summaries e perder visibilidade.

### Regra prática

Durante shakedown/soak, **observability não é luxo; é parte do produto.**

---

## 3.9 `failsafe`

### O que controla

Postura fail-closed e mecanismos de proteção operacional locais.

### Campos importantes

- `failsafe.global_fail_closed`
- `failsafe.market_context_fail_closed`
- `failsafe.summary_fail_closed`
- `failsafe.kill_switch_file`
- `failsafe.drain_mode_file`
- `failsafe.circuit_breaker_enable`
- `failsafe.breaker_failures_to_open`
- `failsafe.breaker_cooldown_minutes`

### Mudanças normalmente seguras

- quase nenhuma, sem uma necessidade clara.

### Mudanças sensíveis/perigosas

- qualquer uma que relaxe fail-closed;
- breaker thresholds/cooldown sem motivo forte.

### Regra prática

Se você precisa perguntar “será que posso desligar isso?”, a resposta padrão deve ser **não**.

---

## 3.10 `multi_asset`

### O que controla

Envelope de observação/seleção multi-asset:
- paralelismo;
- fan-out;
- top-k;
- caps de posição/pending;
- partitioning de dados;
- headroom do prepare;
- rotação de candidate budget.

### Campos importantes

- `multi_asset.enabled`
- `multi_asset.max_parallel_assets`
- `multi_asset.stagger_sec`
- `multi_asset.execution_stagger_sec`
- `multi_asset.portfolio_topk_total`
- `multi_asset.portfolio_hard_max_positions`
- `multi_asset.portfolio_hard_max_trades_per_day`
- `multi_asset.portfolio_hard_max_pending_unknown_total`
- `multi_asset.partition_data_paths`
- `multi_asset.data_db_template`
- `multi_asset.dataset_path_template`
- `multi_asset.adaptive_prepare_enable`
- `multi_asset.prepare_incremental_lookback_candles`
- `multi_asset.candidate_budget_rotation_enable`

### Mudanças normalmente seguras

- ajustes pequenos de stagger;
- tuning leve de prepare incremental;
- candidate budget rotation, quando você entende o governor.

### Mudanças sensíveis/perigosas

- `max_parallel_assets`
- `portfolio_topk_total`
- caps de posição/pending
- partitioning templates

### Regra prática

No canary atual, a configuração saudável é a **conservadora**:
- multi-asset ligado;
- execução top-1;
- `max_parallel_assets=1`;
- `portfolio_hard_max_positions=1`.

Não aumente isso sem nova validação em camadas.

---

## 3.11 `intelligence`

### O que controla

A camada mais rica e mais fácil de subestimar:
- slot-aware tuning;
- learned gating/stacking;
- drift/regime;
- retrain planning;
- coverage regulator;
- anti-overfit;
- policies por scope.

### Campos importantes

Na prática, pense em subfamílias:

#### a) learned / stacking
- `learned_gating_enable`
- `learned_gating_weight`
- `learned_stacking_enable`
- `learned_promote_above`
- `learned_suppress_below`
- `learned_abstain_band`
- `learned_min_reliability`

#### b) allocator penalties/bonuses
- `allocator_block_regime`
- `allocator_warn_penalty`
- `allocator_block_penalty`
- `allocator_under_target_bonus`
- `allocator_over_target_penalty`
- `allocator_retrain_penalty`
- `allocator_reliability_penalty`

#### c) drift/regime/retrain
- `drift_monitor_enable`
- `drift_warn_psi`
- `drift_block_psi`
- `drift_fail_closed`
- `regime_warn_shift`
- `regime_block_shift`
- `retrain_*`

#### d) coverage/anti-overfit
- `coverage_*`
- `anti_overfit_*`
- `anti_overfit_tuning_*`

### Mudanças normalmente seguras

- quase nenhuma “no escuro”.

### Mudanças sensíveis/perigosas

- praticamente todas.

### Regra prática

**`intelligence.*` é uma camada de comportamento e qualidade, não um bloco cosmético.**

Mudar isso sem nova validação pode te dar um bot “diferente” mantendo a mesma aparência externa.

### Revalidação mínima após mudança

- `portfolio_cp_meta_maintenance`
- `evidence_window_scan`
- `canary_go_no_go`
- shakedown
- idealmente soak curto/médio

---

## 3.12 `security`

### O que controla

Postura de deployment, segredos e guardas de envio real.

### Campos importantes

- `security.deployment_profile`
- `security.allow_embedded_credentials`
- `security.live_require_credentials`
- `security.live_require_external_credentials`
- `security.secrets_file`
- `security.guard.*`
- `security.protection.*`

### Mudanças normalmente seguras

- quase só ajustes conscientes de profile de deployment em contexto controlado.

### Mudanças sensíveis/perigosas

- relaxar exigência de segredos externos;
- mudar `guard`/`protection` sem motivo forte;
- qualquer alteração que facilite submit real sem fricção.

### Regra prática

Esta camada existe para te proteger **de você mesmo** em momentos ruins. Respeite-a.

---

## 3.13 `notifications`

### O que controla

Alertas, hoje especialmente Telegram.

### Campos importantes

- `notifications.enabled`
- `notifications.telegram.enabled`
- `notifications.telegram.send_enabled`
- caminhos de outbox/state
- flags de emissão (`emit_release_summary`, `emit_security_alerts`, etc.)

### Mudanças normalmente seguras

- ativar/desativar Telegram em ambiente local;
- ajustar caminhos.

### Mudanças sensíveis

- quase nenhuma em termos de comportamento do bot; é bloco de observabilidade externa.

---

## 3.14 `execution`

### O que controla

Submissão e reconciliação de ordens.

### Campos importantes

- `execution.enabled`
- `execution.mode`
- `execution.provider`
- `execution.account_mode`
- `execution.stake.amount`
- `execution.submit.*`
- `execution.reconcile.*`
- `execution.limits.*`
- `execution.real_guard.*`

### Ponto mais importante do bloco

`execution.mode: live` **não significa automaticamente conta REAL**.

O que define o envelope é a combinação de:
- `execution.enabled`
- `execution.mode`
- `execution.account_mode`
- `broker.balance_mode`

Hoje, o canary saudável usa:
- `execution.enabled: true`
- `execution.mode: live`
- `execution.account_mode: PRACTICE`
- `broker.balance_mode: PRACTICE`

Ou seja: envio real para a conta **PRACTICE** do broker.

### Mudanças normalmente seguras

- stake pequena em PRACTICE, com validação;
- parâmetros de reconcile em ajustes conscientes;
- limites, desde que continue top-1 / single-position no canary.

### Mudanças sensíveis/perigosas

- `account_mode`
- `real_guard`
- relaxar limites de pending/open positions
- `mode`
- prefixo/submit behavior

### Revalidação mínima após mudança

- provider gate
- preflight completo
- shakedown
- soak curto

Se tocar em algo que aumente risco de envio, eu não pularia direto para soak longo.

---

## 3.15 `assets`

### O que controla

Lista de scopes e limites por scope.

### Campos importantes

- `asset`
- `interval_sec`
- `timezone`
- `weight`
- `cluster_key`
- `hard_max_trades_per_day`
- `max_open_positions`
- `max_pending_unknown`

### Mudanças normalmente seguras

- pequenos ajustes de peso/cluster quando você entende a correlação.

### Mudanças sensíveis

- adicionar/remover assets ativos;
- mexer em `cluster_key` sem considerar correlação;
- mudar intervalo e alterar todo o perfil temporal do scope.

---

## 3.16 `runtime_overrides`

### O que controla

Overrides temporários/per-cycle.

### Regra prática

Trate este bloco como **ferramenta avançada**, não como superfície primária de operação.

Se você estiver precisando muito dele no dia a dia, isso costuma ser sinal de que o profile base ainda não está representando bem o envelope desejado.

---

## 3.17 `production`, `dashboard`, `monte_carlo`

### O que controlam

Blocos auxiliares/adjacentes:
- backup e healthcheck de produção;
- dashboard/reporting;
- simulação Monte Carlo.

### Regra prática

São importantes, mas **não são o centro da operação do canary**.

Não deixe esses blocos roubarem sua atenção quando o objetivo for operar ou validar o bot em PRACTICE.

---

## 4. Atlas por arquivo/profile

---

## 4.1 `config/base.yaml`

Use para entender:
- defaults globais;
- filosofia geral do projeto;
- o que um profile especializado está sobrescrevendo.

Não trate como seu “painel diário”.

---

## 4.2 `config/practice_portfolio_canary.yaml`

Use para:
- canary;
- shakedown;
- soak curto/médio/longo em PRACTICE;
- coleta operacional rica com envelope conservador.

É o profile mais importante para a fase atual do Thalor.

---

## 4.3 `config/live_controlled_practice.yaml`

Use para:
- operação prática mais simples e menos carregada de multi-asset;
- cenários em que você quer PRACTICE, mas sem a camada portfolio canary.

---

## 4.4 `config/live_controlled_real.yaml`

Use somente quando houver justificativa real e procedimento explícito de promoção.

Não é profile de uso casual.

---

## 4.5 `config/multi_asset.yaml`

Use para exploração/engenharia de envelope multi-asset e paralelismo.

Não confundir com o canary vigente.

---

## 4.6 `config/broker_secrets.yaml` e overlays de segredo

Função:
- credenciais e overlays de segredo.

Regra prática:
- trate como material sensível;
- prefira segredos externos e caminho documentado;
- não use isso como lugar para ajustar comportamento do bot.

---

## 5. Protocolo de mudança segura

Quando quiser mudar configuração, siga esta ordem.

### Passo 1 — declarar a intenção

Pergunte:
- estou mudando **comportamento** ou só **organização**?
- a mudança é de **operabilidade**, **risco**, **conectividade** ou **estratégia**?

### Passo 2 — olhar o bloco certo

- execution? risco operacional
- decision? comportamento estratégico
- intelligence? qualidade/seleção/drift
- multi_asset? envelope e headroom
- broker/network? conectividade e pacing

### Passo 3 — mudar pouco por vez

Evite alterar vários blocos sensíveis no mesmo commit.

### Passo 4 — revalidar proporcionalmente

#### Mudança pequena de doc/path/log
- suíte curta ou smoke

#### Mudança de provider/broker/network
- `provider_start_gate`
- `provider_stability_report`
- shakedown

#### Mudança de decision/intelligence/multi_asset/execution
- `portfolio_cp_meta_maintenance`
- `evidence_window_scan`
- `canary_go_no_go`
- shakedown
- idealmente soak curto

### Passo 5 — congelar o novo estado

Se a mudança ficou boa:
- commit claro
- tag se necessário
- update no documento correspondente

---

## 6. O que nunca fazer no impulso

- não mexer em `secrets/transport_endpoint` no escuro;
- não tratar `.env` como painel principal de estratégia;
- não relaxar `fail_closed` sem razão fortíssima;
- não aumentar `max_parallel_assets` só para “ver no que dá”;
- não mudar `execution.account_mode` / `broker.balance_mode` sem um ritual de validação;
- não alterar múltiplos blocos sensíveis e depois tentar adivinhar o que quebrou.

---

## 7. Leitura curta para o Hyago do futuro

Se você estiver perdido e precisar se reorientar rápido:

1. descubra **qual profile está ativo**;
2. confirme `execution.account_mode` + `broker.balance_mode`;
3. confirme se `multi_asset.enabled` está ligado ou não;
4. confirme qual é o `gate_mode` e se há `cp_bootstrap_fallback`;
5. confirme os caps de `multi_asset` e `execution.limits`;
6. só então mexa em qualquer coisa.

Se você fizer só isso, já evita grande parte dos erros perigosos.
