# Plano de correção priorizado — raiz operacional do Thalor

## Objetivo

## P0 — costura canônica de secrets/proxy/transporte

**Objetivo:** fazer com que a configuração efetiva de conectividade do broker seja a mesma em `status`, `doctor`, `triage`, `collect_recent`, `refresh_market_context` e runtime/daemon, mesmo quando o operador usa apenas `.env` + `THALOR_SECRETS_FILE` + `secrets/transport_endpoint`.

### Ações

1. Ensinar o loader a repassar para a camada de secrets também os valores crus do `.env`, incluindo `THALOR_SECRETS_FILE`, que antes não chegava em `apply_external_secret_overrides()`.
2. Autodescobrir `config/broker_secrets.yaml` / `secrets/broker.yaml` quando nenhum bundle explícito for informado, reduzindo dependência de plumbing frágil no shell.
3. Persistir `connectivity.json` já no `build_context()`, para que cada comando de controle publique a conectividade efetiva da mesma resolução de config que acabou de usar.
4. Preservar o contrato seguro de secrets por arquivo (`secrets/transport_endpoint`) sem exigir credencial inline em `.env`.

### Resultado esperado

- `runtime_app status/doctor/triage` passam a refletir a mesma conectividade que o caminho broker-facing real enxerga;
- `transport_enabled` / `transport_ready` deixam de depender de export manual no shell quando o operador usa `.env` + bundle/secrets padrão;
- `latest_connectivity.json` passa a existir de forma consistente nas baterias, facilitando o diagnóstico fino do unlock do provider.

### Aplicação neste pacote

Este pacote aplica o P0 em quatro frentes objetivas:

1. `apply_external_secret_overrides()` passa a absorver transporte e request metrics a partir do mesmo `THALOR_SECRETS_FILE` já usado para broker credentials.
2. Quando o bundle não define transporte inline, o loader autodescobre `secrets/transport_endpoint` e `secrets/transport_endpoints`, habilitando `network.transport` sem exigir `TRANSPORT_*` no shell.
3. O payload de conectividade agora expõe `source_trace`, permitindo confirmar nos artifacts se o transporte veio do bundle ou do secret file autodetectado.
4. O redaction path do `NetworkTransportManager` passa a mascarar credenciais também em `env_overlay` e `websocket_options`, eliminando vazamento do proxy auth em JSONL/diagnostics.

Eliminar primeiro a causa estrutural das falhas live observadas nas baterias de diagnóstico:

- `collect_recent` falhando no bootstrap broker-facing;
- `refresh_market_context` estourando orçamento operacional;
- contaminação do circuit breaker por falhas de conectividade mal classificadas;
- divergência entre configuração resolvida (`config + .env`) e processos que instanciam o cliente IQ diretamente.

A correção será executada em fases pequenas, verificáveis e reversíveis.

## Fase 1 — conectividade determinística nos entrypoints broker-facing

**Objetivo:** garantir que todos os entrypoints que falam com o broker usem a mesma configuração resolvida do runtime, inclusive proxy/transporte e métricas, sem depender de variáveis já exportadas no shell.

### Ações

1. Introduzir `IQClient.from_runtime_config(...)`.
2. Fazer `collect_recent`, `refresh_market_context`, `collect_candles` e `backfill_candles` criarem o cliente a partir da configuração resolvida.
3. Propagar `broker.timeout_connect_s` para o `IQClient`.
4. Normalizar o `JSONDecodeError` gerado pela `iqoptionapi` em erro operacional explícito e classificável como falha de transporte.
5. Cobrir o comportamento com testes de regressão.

### Resultado esperado

- subprocessos de `asset_prepare` passam a enxergar o proxy configurado em `.env` / `config` sem depender de `os.environ` já exportado;
- a camada `NetworkTransportManager` passa a ser aplicada de forma homogênea nos fluxos reais que falhavam;
- logs e incidentes deixam de registrar apenas `JSONDecodeError` opaco e passam a registrar a causa operacional normalizada.

## Fase 2 — orçamento de falha e degradação controlada

**Objetivo:** impedir que um problema transitório de broker derrube todo o bootstrap do escopo.

### Ações

1. Introduzir orçamento de tentativa por operação (`collect_recent`, `refresh_market_context`).
2. Fazer `refresh_market_context` cair para fallback baseado em DB/cache em falhas operacionais do broker, não apenas em ausência de dependência.
3. Classificar melhor falhas transitórias vs. definitivas para evitar espera inútil até o timeout global do subprocesso.

### Resultado esperado

- redução drástica de `TimeoutExpired: timeout=120s module=natbin.refresh_market_context`;
- menor contaminação do circuit breaker por falhas secundárias.

### Aplicação neste pacote

Esta fase foi implementada com quatro mudanças principais:

1. `IQClient` agora usa os defaults de `broker.*` resolvidos em config para:
   - `connect_retries`, `connect_sleep_s`, `connect_sleep_max_s`, `timeout_connect_s`;
   - `get_candles_retries`, `get_candles_sleep_s`, `get_candles_sleep_max_s`, `timeout_get_candles_s`;
   - `timeout_market_context_s`.
2. `get_all_profit`, `get_all_open_time`, `get_all_ACTIVES_OPCODE`, `update_ACTIVES_OPCODE` e `get_candles` passam a respeitar orçamento de timeout por tentativa.
3. `refresh_market_context` agora degrada para fallback local (DB + cache de payout) quando o broker falha operacionalmente, sem esperar o timeout global do subprocesso.
4. `collect_recent` agora reutiliza dados locais recentes de forma controlada quando a falha é operacional e o snapshot local ainda é utilizável.

### Novas chaves de configuração do broker

- `broker.timeout_get_candles_s`
- `broker.timeout_market_context_s`
- `broker.collect_reuse_local_data_on_failure`
- `broker.collect_reuse_local_max_age_sec`
- `broker.market_context_cache_fallback_enable`
- `broker.market_context_cache_max_age_sec`

## Fase 3 — higiene do circuit breaker e da superfície operacional

**Objetivo:** fazer o breaker reagir à causa raiz e não amplificar cascatas.

### Ações

1. Separar falhas de bootstrap broker-facing das falhas de decisão/execução.
2. Melhorar incidentes/control artifacts com causa primária e última causa de transporte.
3. Evitar bloqueios `half_open` prematuros quando a tentativa positiva ainda não ocorreu com transporte válido.


### Aplicação nesta fase

Esta fase foi implementada com cinco correções estruturais:

1. O `precheck` passou a ser apenas observacional no estado `half_open`; ele não consome mais a tentativa de recuperação positiva do circuit breaker.
2. O snapshot persistido do breaker agora registra `primary_cause`, `failure_domain`, `failure_step`, `last_transport_error`, `last_transport_failure_utc` e o estado real da tentativa `half_open` em voo.
3. O runtime/portfolio passou a classificar falhas por domínio (`broker_bootstrap`, `prepare`, `decision`, `execution`, `runtime`) antes de gravar o breaker.
4. Foi criado um artifact dedicado `runs/control/<scope>/breaker.json`, usado como fonte diagnóstica por `status`, `incidents` e `doctor`.
5. `incident_status` e `production_doctor` agora reportam a causa primária do breaker e o último erro de transporte, evitando que sintomas secundários encubram a falha raiz.

### Resultado esperado

- `status`/`precheck` deixam de bloquear a recuperação half-open por simples observação;
- incidentes e doctor passam a diferenciar falha de bootstrap broker-facing de falha de decisão/execução;
- a superfície operacional passa a apontar explicitamente para erro de transporte quando essa for a causa raiz.

## Fase 4 — consistência de Docker/Compose/ambientes

**Objetivo:** alinhar execução local, VPS e produção ao mesmo contrato de configuração.

### Ações

1. Corrigir interpolação/expansão de `THALOR_CONFIG` no Compose e sincronizar `THALOR_CONFIG_PATH` / `THALOR_DASHBOARD_CONFIG_PATH` dentro do container.
2. Tornar `docker-compose.vps.yml` e `docker-compose.prod.yml` válidos como arquivos standalone, mantendo `docker-compose.prod.yml` também compatível como override.
3. Ensinar o loader tipado a absorver aliases legados de transporte e request metrics para que `.env`, runtime e config resolvida concordem.
4. Validar flags efetivas de transporte/métricas dentro do container e não só no YAML renderizado.

### Aplicação nesta fase

Esta fase foi implementada com cinco correções estruturais:

1. `resolve_config_path()` passou a honrar `THALOR_CONFIG_PATH` e `THALOR_CONFIG`, e `resolve_repo_root()` passou a honrar `THALOR_REPO_ROOT` quando o root não é fornecido explicitamente.
2. Os wrappers `scripts/docker/bootstrap_env.sh`, `runtime_loop.sh`, `backup_loop.sh`, `dashboard.sh`, `runtime_status.sh` e `contract_check.sh` passaram a normalizar paths relativos para caminhos absolutos dentro do container antes de chamar o runtime.
3. `docker-compose.yml`, `docker-compose.vps.yml` e `docker-compose.prod.yml` foram alinhados ao mesmo contrato de variáveis, com correção do bug de interpolação do dashboard e comando canônico baseado em scripts wrapper.
4. O loader tipado passou a absorver aliases legados (`TRANSPORT_*`, `REQUEST_METRICS_*`) como fonte compatível, evitando mismatch entre `env_file`, `.env` e runtime resolvido.
5. Foi criado `natbin.ops.docker_contract`, um validador side-effect free do contrato efetivo do container, consumido pelo script `scripts/docker/contract_check.sh`.

### Resultado esperado

- `docker-compose.prod.yml` e `docker-compose.vps.yml` passam a ser utilizáveis de forma previsível como stacks autônomos;
- o dashboard deixa de sofrer com expansão prematura de `THALOR_CONFIG` no host;
- transporte e request metrics passam a refletir o estado efetivo do runtime, e não apenas o YAML renderizado.

## Fase 5 — tooling, workspace e ruído operacional

**Objetivo:** remover ruído que atrapalha diagnósticos, sem misturar isso com a raiz do problema live.

### Ações

1. Fazer scanners ignorarem `test_battery/` e outros artefatos gerados.
2. Limpar artefatos stale do workspace e endurecer políticas de exclusão.
3. Melhorar bundles de diagnóstico para separar causa primária de sintomas subsequentes.

### Aplicação nesta fase

Esta fase foi implementada com cinco correções operacionais:

1. Foi criado `natbin.ops.workspace_hygiene`, com preview/aplicação de limpeza segura para ruído gerado por testes/diagnósticos/caches, sem tocar em `runs/`, `data/`, `secrets/` ou `.env`.
2. `runtime_app` ganhou os comandos `workspace-hygiene` e `triage`, com artifacts em `runs/control/_repo/workspace_hygiene.json` e `runs/control/<scope>/triage.json`.
3. `repo_sync`, `release_hygiene`, `.gitignore`, `selfcheck_repo.py`, `check_hidden_unicode.py` e `strip_hidden_unicode.py` passaram a tratar `test_battery/`, `diag_zips/`, `coverage.xml`, `diag_bundle_*.zip` e caches como ruído gerado.
4. Os runners `run_thalor_diagnostics_v4.ps1` e `run_thalor_diagnostics_v4_1.ps1` passaram a materializar `workspace-hygiene` e `triage`, melhorando os bundles para separar causa primária de sintoma.
5. O payload de `repo_sync` agora diferencia `meaningful_dirty` de `noise_only_dirty`, reduzindo falso alarme por artefato local gerado.

### Resultado esperado

- scanners e selfchecks deixam de falhar por artefatos produzidos pela própria bateria de testes;
- bundles passam a incluir um resumo curto e consistente da causa primária (`triage`);
- o workspace pode ser higienizado de forma previsível antes de novas rodadas de diagnóstico ou package/release.

## Ordem de execução

1. **Fase 1** — já iniciada neste pacote.
2. **Fase 2**
3. **Fase 3**
4. **Fase 4**
5. **Fase 5**

## Critério de avanço

Cada fase só avança quando cumprir os três critérios abaixo:

1. teste unitário/regressão cobrindo o bug;
2. smoke ou cenário operacional reproduzindo a melhoria;
3. artefato ZIP de patch e pacote completo gerado para revisão.

## P1 — unlock do provider concluído e próximos guard rails

Depois do P0, o caminho broker-facing passou a resolver o proxy canonicamente. O bloqueio residual real foi reduzido a dois pontos:

1. dependência ausente de `PySocks` para endpoints `socks5h://...`;
2. incompatibilidade semântica entre `real_preflight` e `drain mode`, além de risco de divergência entre `execution.account_mode` e `broker.balance_mode`.

### Aplicação neste pacote

1. `PySocks` foi adicionado explicitamente em `pyproject.toml`, `requirements.txt`, `requirements-dev.txt` e `requirements-ci.txt`, além de validação de instalação no `Dockerfile`.
2. O runtime passou a fazer preflight explícito de transporte SOCKS antes de tentar conectar.
3. `runtime_app precheck` ganhou `--allow-drain-mode`, permitindo validar um `real_preflight` com `drain mode` ligado sem se auto-bloquear.
4. `controlled_live_validation` passou a usar esse modo explicitamente no estágio `real_preflight`.
5. `broker.balance_mode` deixou de ser controlado por bundles de segredo; o valor canônico agora pertence ao profile YAML.
6. `production_doctor` e `release` passaram a bloquear explicitamente divergências entre `execution.account_mode` e `broker.balance_mode`.

