# 02 — Runbook Operacional do Thalor (revisado)

## Objetivo deste documento
Este documento é o **manual operacional central** do Thalor. Ele não cobre só o fluxo padrão atual consolidado; ele cobre os **estados reais, estados problemáticos, estados de recuperação, estados pretendidos e estados futuros** do projeto.

Ele existe para responder, com comandos concretos e com ordem de execução clara:

- em que estado o Thalor está agora;
- o que eu devo fazer neste estado;
- o que eu não devo fazer neste estado;
- como sair de um estado ruim para um estado saudável;
- como usar o Thalor nas diferentes possibilidades operacionais do projeto;
- como conduzir mudança, validação, shakedown, soak, freeze e promoção sem improviso.

Este documento foi pensado para o **Hyago do futuro operando sozinho**. O objetivo não é decorar tudo; é ter um guia suficientemente completo para operar o sistema com autonomia.

---

# 1. Como ler este documento

Este runbook tem **duas lógicas ao mesmo tempo**:

1. uma lógica de **estado operacional**;
2. uma lógica de **modo de uso**.

A lógica por estado responde:

- provider está ruim?
- canary está em repair?
- o sistema está só em observação?
- estamos em shakedown?
- estamos em soak longo?
- estamos congelando baseline?
- estamos preparando promoção?

A lógica por modo responde:

- estou em **bootstrap**?
- em **diagnóstico**?
- em **canary conservador**?
- em **maintenance/repair**?
- em **coleta séria de dados**?
- em **engenharia exploratória**?
- em **PRACTICE controlado**?
- em **REAL/live futuro**?

O documento não tenta ser uma referência completa de cada subcomando existente no código. Ele tenta ser a referência operacional para **usar o Thalor nas suas possibilidades reais de operação**.

---

# 2. Invariantes operacionais do Thalor

## 2.1. Pontos sagrados
Não tocar sem motivo extremo e sem validação forte:

- `./secrets`
- `secrets/transport_endpoint`
- credenciais reais
- envelope de risco sem revalidação

## 2.2. O que normalmente pode ser limpo
Normalmente pode ser arquivado/rotacionado:

- `runs/`
- bundles e exports temporários
- logs transitórios locais

## 2.3. O que não deve ser limpo no impulso
Não limpar no impulso:

- `data/`
- bases SQLite de mercado que ainda servem à convergência local
- dataset paths que evitam bootstrap desnecessário

## 2.4. Fonte de verdade operacional
No Thalor, verdade operacional não é “qualquer artifact verde”.

A ordem correta de confiança é:

1. **probe fresco / gate fresco**
2. artifacts frescos de control repo
3. closure consolidado
4. histórico antigo

Em termos práticos:
- `provider_start_gate` > artifacts antigos
- `provider_probe` fresco > status velho
- `canary_go_no_go` fresco > interpretação manual de arquivos soltos

## 2.5. Filosofia padrão do projeto
O Thalor foi construído para operar de forma:

- conservadora;
- fail-closed;
- diagnosticável;
- reproduzível;
- com o mínimo possível de improviso durante a operação.

Isso significa que o comportamento correto do operador nem sempre é “forçar o sistema a rodar”. Muitas vezes o comportamento correto é:

- abortar cedo,
- reparar artifacts,
- revalidar,
- ou simplesmente aguardar nova janela.

---

# 3. Mapa de estados operacionais do Thalor

Abaixo está a máquina de estados prática do projeto. Ela não é formal no código como um único enum, mas é a melhor forma de operar o sistema sem se perder.

## Estado A — Bootstrap frio
### O que é
Projeto recém-extraído, recém-clonado, ou workspace muito limpo, ainda sem convergência local suficiente.

### Sinais típicos
- `runs/` inexistente ou vazia
- `market_context` inexistente
- DBs locais muito antigos ou vazios
- artifacts de intelligence/portfolio ainda ausentes

### Objetivo
Levar o projeto ao ponto em que ele consegue:
- abrir provider;
- gerar artifacts mínimos;
- entrar no fluxo canônico do canary.

### Ferramentas principais
- suíte completa
- `provider_start_gate`
- `portfolio_canary_warmup`
- `portfolio_cp_meta_maintenance`
- `evidence_window_scan`
- `canary_go_no_go`

### O que não fazer
- não começar direto por soak longo;
- não confiar em artifacts velhos copiados de outra sessão;
- não mexer em config de risco antes de o bootstrap estabilizar.

---

## Estado B — Baseline validado
### O que é
Estado em que a suíte está verde e o projeto está tecnicamente íntegro.

### Sinais típicos
- `pytest -q` fecha totalmente verde;
- commit/tag de baseline faz sentido;
- não há regressão funcional conhecida aberta.

### Objetivo
Congelar o estado atual como referência operacional/documental.

### Ferramentas principais
- `pytest -q`
- `git status`
- `git commit`
- `git tag`

### O que não fazer
- não sair refatorando workspace antes de congelar;
- não misturar freeze com mudança funcional grande.

---

## Estado C — Provider indisponível / janela ruim
### O que é
A camada de provider/proxy não consegue fechar sessão fresca.

### Sinais típicos
- `provider_start_gate` falha
- `provider_probe.ok=false`
- `provider_ready_scopes=0`
- `iqoption_connect` com timeout ou erro repetitivo

### Objetivo
Determinar se:
- a janela está realmente ruim;
- o problema é de rota/proxy/provider;
- vale aguardar ou abortar a tentativa.

### Ferramentas principais
- `provider_start_gate`
- `provider_probe`
- `transport-smoke`
- `provider_stability_report`
- testes externos mínimos de rede/proxy

### O que não fazer
- não deixar `provider_start_gate` rodando indefinidamente;
- não subir shakedown/soak em cima de probe ruim;
- não confiar em artifacts verdes antigos.

---

## Estado D — Provider saudável, canary em repair
### O que é
A conectividade está boa, mas o envelope do canary ainda não fechou.

### Sinais típicos
- `provider_ready_scopes > 0`
- `canary_go_no_go = NO_GO_REPAIR`
- `closure_state = repair_needed`
- artifacts faltando ou inconsistentes

### Objetivo
Convergir pack/eval/candidate/allocation/artifacts até o canary ficar operacionalmente fechado.

### Ferramentas principais
- `portfolio_canary_warmup`
- `portfolio_cp_meta_maintenance`
- `evidence_window_scan`
- `portfolio_canary_closure_report`
- `signal_artifact_audit`
- `asset prepare`
- `asset candidate`
- `intelligence-refresh`

### O que não fazer
- não iniciar soak enquanto o canary continuar em `NO_GO_REPAIR`;
- não interpretar “provider ok” como “canary pronto”.

---

## Estado E — Canary saudável, aguardando sinal
### O que é
Este é o estado canônico atual consolidado do Thalor.

### Sinais típicos
- `canary_go_no_go.ok = true`
- `decision = GO_WAITING_SIGNAL`
- `closure_state = healthy_waiting_signal`
- `blocking_cp_meta_missing_scopes = 0`
- `blocking_gate_fail_closed_scopes = 0`

### Objetivo
Operar com segurança para:
- observar;
- ranquear scopes;
- colher dados;
- deixar o runtime rodar sem forçar trade.

### Ferramentas principais
- `provider_start_gate`
- `portfolio_canary_warmup`
- `evidence_window_scan`
- `portfolio observe`
- monitoramento passivo de logs/artifacts

### O que não fazer
- não interpretar ausência de trade como falha;
- não abrir novas frentes de engenharia sem necessidade;
- não ampliar envelope cedo demais.

---

## Estado F — Shakedown
### O que é
Sessão curta, controlada, para provar que o sistema integrado roda direito.

### Sinais típicos
- profile certo carregado
- provider gate passou
- canary fechado
- intenção é validar múltiplos candles, não necessariamente ficar horas

### Objetivo
Validar:
- estabilidade integrada;
- artifacts novos;
- request metrics úteis;
- budget do ciclo;
- ausência de crashes.

### Duração típica
- 12 ciclos: ~1h a 1h20
- 18 ciclos: ~1h30 a 2h

---

## Estado G — Soak
### O que é
Sessão longa, deliberadamente orientada à coleta de evidência operacional em mercado real.

### Objetivo
Coletar:
- sinais;
- gates;
- regime/drift;
- request metrics;
- comportamento do provider;
- submit/reject/reconcile, quando houver;
- consistência multi-candle e multi-janela.

### Duração típica
- médio: 36 ciclos (~3–4h)
- longo: 96 ciclos (~8h)

---

## Estado H — Diagnóstico cirúrgico de scope
### O que é
Você não quer rodar o canary inteiro; quer entender um scope específico.

### Objetivo
Operar em nível de:
- um ativo;
- um interval;
- um artifact;
- uma cadeia específica de prepare/candidate/intelligence.

### Ferramentas principais
- `asset prepare`
- `asset candidate`
- `intelligence-refresh`
- `evidence_window_scan`
- `portfolio_canary_signal_proof`

---

## Estado I — PRACTICE controlado
### O que é
Modo em que o bot já não está só em validação de envelope, mas em uso prático controlado em conta PRACTICE.

### Objetivo
Coletar comportamento operacional mais próximo da operação real, mantendo:
- envelope de risco conservador;
- trilha diagnóstica forte;
- possibilidade de abortar cedo.

### Ferramentas principais
- `live_controlled_practice.yaml`
- `practice-preflight`
- `provider_start_gate`
- `portfolio observe`
- `orders`, `reconcile`, `check-order-status`

### Observação
Este estado é **mais forte** que o canary, mas ainda **não é** promoção para REAL/live.

---

## Estado J — REAL/live futuro
### O que é
Estado pretendido de maturidade futura, ainda não tratado como padrão seguro atual.

### Objetivo
Operar só depois de evidência suficiente em PRACTICE.

### Condições mínimas desejáveis
- canary repetidamente saudável;
- múltiplos soaks bons;
- execution path confiável em PRACTICE;
- reconcile e observabilidade bem fechados;
- runbook suficientemente estável;
- disciplina de abort e rollback definida.

### Regra
Este estado deve ser tratado como **promovido**, nunca como “atalho”.

---

## Estado K — Engenharia exploratória
### O que é
Modo em que o objetivo não é operar o canary atual, e sim explorar capacidades do sistema:
- multi-asset mais agressivo;
- tuning;
- dashboards;
- Monte Carlo;
- experimentos;
- smoke tests.

### Risco
Misturar esse estado com operação real do canary confunde tudo. Ele deve ser tratado como trilha separada.

---

# 4. Modos de uso do Thalor

Abaixo não estão “estados”, mas **maneiras de usar o sistema**.

## 4.1 Modo canônico atual — Canary conservador em PRACTICE
### Quando usar
- para operação padrão atual do projeto;
- para shakedown;
- para soak;
- para coleta de dados operacionais reais com segurança.

### Profile
- `config/practice_portfolio_canary.yaml`

### Sequência padrão
```powershell
Set-Location C:\Users\hyago_31k5yin\Documents\Thalor
$env:PYTHONPATH = ".;src"

.\scripts\tools\provider_start_gate.cmd --config config\practice_portfolio_canary.yaml --all-scopes --consecutive-ok 2 --json
.\scripts\tools\portfolio_canary_warmup.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\portfolio_cp_meta_maintenance.cmd --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
```

---

## 4.2 Modo repair do canary
### Quando usar
- `NO_GO_REPAIR`
- artifacts faltando
- eval/candidate/pack não convergindo

### Sequência base
```powershell
.\scripts\tools\portfolio_cp_meta_maintenance.cmd --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
```

### Sequência scope-cirúrgica
```powershell
.\.venv\Scripts\python.exe -m natbin.control.app --repo-root . --config config\practice_portfolio_canary.yaml asset prepare --asset AUDUSD-OTC --interval-sec 300 --json
.\.venv\Scripts\python.exe -m natbin.control.app --repo-root . --config config\practice_portfolio_canary.yaml asset candidate --asset AUDUSD-OTC --interval-sec 300 --json
.\.venv\Scripts\python.exe -m natbin.control.app --repo-root . --config config\practice_portfolio_canary.yaml intelligence-refresh --asset AUDUSD-OTC --interval-sec 300 --json
```

---

## 4.3 Modo shakedown
### Quando usar
- depois de mudança importante
- depois de baseline novo
- antes de soak médio/longo

### Comando padrão
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe `
  --repo-root . `
  --config config\practice_portfolio_canary.yaml `
  --topk 1 `
  --lookback-candles 2000 `
  --quota-aware-sleep `
  --precheck-market-context `
  --max-cycles 12 2>&1 | Tee-Object -FilePath .\runs\logs\portfolio_observe_shakedown.log
```

---

## 4.4 Modo soak médio/longo
### Quando usar
- depois de shakedown limpo
- para coleta séria de evidência operacional

### Soak médio
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe `
  --repo-root . `
  --config config\practice_portfolio_canary.yaml `
  --topk 1 `
  --lookback-candles 2000 `
  --quota-aware-sleep `
  --precheck-market-context `
  --max-cycles 36 2>&1 | Tee-Object -FilePath .\runs\logs\portfolio_observe_soak_medium.log
```

### Soak longo
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe `
  --repo-root . `
  --config config\practice_portfolio_canary.yaml `
  --topk 1 `
  --lookback-candles 2000 `
  --quota-aware-sleep `
  --precheck-market-context `
  --max-cycles 96 2>&1 | Tee-Object -FilePath .\runs\logs\portfolio_observe_long_soak.log
```

---

## 4.5 Modo de diagnóstico do provider
### Quando usar
- provider oscilando
- gate falhando
- dúvida se o problema é de rede/proxy/provider

### Ferramentas principais
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app provider-probe --repo-root . --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\provider_start_gate.cmd --config config\practice_portfolio_canary.yaml --all-scopes --consecutive-ok 2 --json
.\scripts\tools\provider_stability_report.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\provider_session_governor.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
```

### Diagnóstico externo mínimo
```powershell
$proxy = (Get-Content .\secrets\transport_endpoint -TotalCount 1).Trim()
Test-NetConnection gate.decodo.com -Port 7000
curl.exe --proxy $proxy https://api.ipify.org
```

---

## 4.6 Modo observação/diagnóstico do portfolio
### Quando usar
- para entender o board e a alocação sem iniciar longa sessão

### Comandos principais
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio status --repo-root . --config config\practice_portfolio_canary.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio plan --repo-root . --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\portfolio_canary_signal_proof.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
```

---

## 4.7 Modo PRACTICE controlado
### Quando usar
- quando o objetivo é executar em PRACTICE com mais seriedade do que o canary puro
- quando o canary já está repetidamente saudável

### Profile
- `config/live_controlled_practice.yaml`

### Sequência sugerida
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app practice-preflight --repo-root . --config config\live_controlled_practice.yaml --json
.\scripts\tools\provider_start_gate.cmd --config config\live_controlled_practice.yaml --all-scopes --consecutive-ok 2 --json
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe --repo-root . --config config\live_controlled_practice.yaml --topk 1 --lookback-candles 2000 --quota-aware-sleep --precheck-market-context --max-cycles 12
```

### Observação
É PRACTICE, mas com ambição operacional maior. Não deve ser confundido com canary nem com live real.

---

## 4.8 Modo de execução/reconcile
### Quando usar
- quando já existe intenção/ordem e você quer inspecionar ou reconciliar execução

### Comandos principais
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app orders --repo-root . --config config\live_controlled_practice.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app check-order-status --repo-root . --config config\live_controlled_practice.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app reconcile --repo-root . --config config\live_controlled_practice.yaml --json
```

### Regra
Esse modo só faz sentido quando o caminho de execução já está sendo exercitado. Não é o ponto de partida do projeto.

---

## 4.9 Modo REAL/live futuro
### Quando usar
Apenas quando houver decisão explícita de promoção.

### Profile
- `config/live_controlled_real.yaml`

### Pré-condições mínimas recomendadas
- múltiplos soaks bons em PRACTICE;
- execution/reconcile confiável;
- runbooks maduros;
- disciplina de abort e rollback definida;
- evidência suficiente de que o gargalo atual já não é de engenharia estrutural.

### Sequência conceitual
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app practice-preflight --repo-root . --config config\live_controlled_real.yaml --json
.\scripts\tools\provider_start_gate.cmd --config config\live_controlled_real.yaml --all-scopes --consecutive-ok 2 --json
.\.venv\Scripts\python.exe -m natbin.runtime_app production-gate --repo-root . --config config\live_controlled_real.yaml --probe-provider --json
```

### Regra absoluta
Não promover para REAL/live para “testar se vai”. Real/live é consequência de maturidade, não ferramenta de descoberta.

---

## 4.10 Modo multi-asset exploratório / engenharia
### Quando usar
- engenharia exploratória
- testes de fan-out
- observação além do envelope canônico

### Profile
- `config/multi_asset.yaml`

### Exemplo
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio status --repo-root . --config config\multi_asset.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe --repo-root . --config config\multi_asset.yaml --once --topk 3 --lookback-candles 2000 --json
```

### Regra
Tratar como trilha de engenharia, não como substituto do canary conservador.

---

## 4.11 Modo suíte / baseline / freeze
### Quando usar
- antes de congelar baseline
- antes de nova documentação
- depois de grandes mudanças estruturais

### Comando base
```powershell
Set-Location C:\Users\hyago_31k5yin\Documents\Thalor
$env:PYTHONPATH = ".;src"
.\.venv\Scripts\python.exe -m pytest -q
```

### Depois
- commit
- tag
- registro de baseline
- só depois limpeza/profissionalização do workspace

---

## 4.12 Modo de observabilidade e bundles
### Quando usar
- pós-run
- pós-shakedown
- pós-soak
- análise de falha

### Comandos principais
```powershell
.\scripts\tools\provider_stability_report.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\provider_session_governor.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\portfolio_canary_closure_report.cmd --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\capture_portfolio_canary_bundle.cmd --config config\practice_portfolio_canary.yaml
.\scripts\tools\capture_canary_closure_bundle.cmd --config config\practice_portfolio_canary.yaml
```

---

## 4.13 Modo de incidents / safety switches
### Quando usar
- troubleshooting avançado
- inspeção de kill switch, drain mode, breaker

### Comandos principais
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app ops killswitch status --repo-root . --config config\practice_portfolio_canary.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app ops drain status --repo-root . --config config\practice_portfolio_canary.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app ops breaker status --repo-root . --config config\practice_portfolio_canary.yaml --json
```

### Operações destrutivas / administrativas
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app ops breaker reset --repo-root . --config config\practice_portfolio_canary.yaml --json
```

### Regra
Só usar reset/on/off conscientemente. Não usar para “maquiar” problema estrutural.

---

## 4.14 Modo de suporte e auditoria
### Quando usar
- exportar diagnóstico
- verificar superfícies de segurança/estado/config

### Comandos principais
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app support-bundle --repo-root . --config config\practice_portfolio_canary.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app config-provenance-audit --repo-root . --config config\practice_portfolio_canary.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app runtime-artifact-audit --repo-root . --config config\practice_portfolio_canary.yaml --json
.\.venv\Scripts\python.exe -m natbin.runtime_app state-db-audit --repo-root . --config config\practice_portfolio_canary.yaml --json
```

---

# 5. Fluxo canônico atual consolidado

Este continua sendo o fluxo mais importante do projeto hoje.

## 5.1 Preparar sessão limpa
```powershell
Set-Location C:\Users\hyago_31k5yin\Documents\Thalor
$env:PYTHONPATH = ".;src"

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force ".\archive\$ts" | Out-Null
if (Test-Path .\runs) { Move-Item .\runs ".\archive\$ts\runs_pre_session" }
New-Item -ItemType Directory -Force .\runs\logs | Out-Null
New-Item -ItemType Directory -Force .\runs\control\_repo | Out-Null
New-Item -ItemType Directory -Force .\runs\chat_exports | Out-Null
```

## 5.2 Fechar o gate do provider
```powershell
.\scripts\tools\provider_start_gate.cmd --config config\practice_portfolio_canary.yaml --all-scopes --consecutive-ok 2 --json
```

## 5.3 Warmup
```powershell
.\scripts\tools\portfolio_canary_warmup.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
```

## 5.4 Repair se necessário
```powershell
.\scripts\tools\portfolio_cp_meta_maintenance.cmd --config config\practice_portfolio_canary.yaml --json
```

## 5.5 Evidence + go/no-go
```powershell
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
```

## 5.6 Shakedown curto
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe `
  --repo-root . `
  --config config\practice_portfolio_canary.yaml `
  --topk 1 `
  --lookback-candles 2000 `
  --quota-aware-sleep `
  --precheck-market-context `
  --max-cycles 12 2>&1 | Tee-Object -FilePath .\runs\logs\portfolio_observe_shakedown.log
```

## 5.7 Pós-run
```powershell
.\scripts\tools\provider_stability_report.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\provider_session_governor.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\portfolio_canary_closure_report.cmd --config config\practice_portfolio_canary.yaml --json
```

---

# 6. Matriz de decisão rápida

## Caso 1 — `provider_start_gate` falhou
**Ação:** não partir. Diagnosticar provider.

## Caso 2 — `provider_start_gate` passou, mas `canary_go_no_go = NO_GO_REPAIR`
**Ação:** repair, evidence, rescan. Não iniciar soak.

## Caso 3 — `GO_WAITING_SIGNAL`
**Ação:** pode iniciar shakedown/soak conservador.

## Caso 4 — `provider_state = degraded`, mas `GO_WAITING_SIGNAL`
**Ação:** pode operar, mas mantendo:
- top-1
- single-position
- fan-out serializado
- sem ampliar envelope.

## Caso 5 — shakedown rodou, mas sem trade
**Ação:** não tratar como fracasso automático. Ver se os blockers foram saudáveis (regime/gate/threshold).

## Caso 6 — soak longo terminou limpo
**Ação:** congelar baseline, fazer postmortem, repetir antes de pensar em promoção.

## Caso 7 — vontade de mexer em código no meio da sessão
**Ação:** não mexer. Terminar/abortar a sessão, coletar evidência, só então decidir engenharia.

---

# 7. O que nunca fazer no impulso

- não tocar em `secrets/transport_endpoint`
- não limpar `data/` por reflexo
- não subir soak com `NO_GO_REPAIR`
- não confiar em artifact velho no lugar de gate fresco
- não ampliar envelope só porque uma sessão foi boa
- não usar `REAL/live` para descobrir se o sistema está pronto
- não rodar múltiplos comandos pesados em paralelo com o runtime principal
- não tratar `HOLD` saudável como bug estrutural

---

# 8. O que este documento não substitui

Este runbook é o centro de operação, mas ele não substitui:

- **Mapa Mestre** -> visão do todo
- **Modos e Perfis** -> escolha de profile
- **Dicionário de Estados** -> interpretação fina dos nomes internos
- **Atlas de Configuração** -> impacto dos blocos de config
- **Troubleshooting e Mudança Segura** -> análise de falha e protocolo de alteração

Em caso de dúvida, use este documento para decidir **o que fazer agora**. Use os demais para entender **por que** e **com qual risco**.
