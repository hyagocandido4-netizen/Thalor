# 06 — Troubleshooting e Mudança Segura do Thalor

## Objetivo

Este documento existe para duas coisas:

1. ajudar o **Hyago do futuro** a diagnosticar problemas do Thalor sem entrar em pânico;
2. permitir mudanças no sistema sem quebrar o que já funciona.

Ele não substitui o runbook operacional. Ele entra **quando algo sai do esperado** ou **quando você quer alterar comportamento/configuração**.

---

## Princípios de troubleshooting do Thalor

### 1. A fonte de verdade precisa ser **fresca**
Nunca decida com base apenas em artifact antigo.

Regra prática:
- prefira `provider_start_gate` e `provider-probe` frescos;
- prefira `evidence_window_scan`, `provider_stability_report`, `provider_session_governor`, `canary_go_no_go` e `portfolio_canary_closure_report` frescos;
- trate artifacts históricos apenas como contexto, não como estado atual.

### 2. Primeiro identifique a **camada** do problema
Quase todo problema do Thalor cai em uma destas camadas:
- **transport/provider**
- **dados locais** (`data/`, datasets, candle DB)
- **market context**
- **intelligence** (`eval`, `pack`, `cp`, artifacts)
- **canary/closure**
- **execution/reconcile**
- **workspace/diagnóstico**

### 3. Não tente consertar tudo ao mesmo tempo
Quando o sistema fica complexo, o erro clássico é misturar:
- preflight,
- repair,
- scan,
- runtime,
- limpeza,
- e alteração de config.

No Thalor, isso costuma piorar a leitura do problema.

### 4. Preserve evidência antes de “arrumar”
Se a sessão foi importante, archive `runs/` antes de qualquer repair pesado.

---

## Matriz rápida de sintomas

### Sintoma: `provider_start_gate` não fecha 2 probes OK
Provável camada:
- transport/provider

Primeiros comandos:
```powershell
$proxy = (Get-Content .\secrets\transport_endpoint -TotalCount 1).Trim()
Test-NetConnection gate.decodo.com -Port 7000
curl.exe --proxy $proxy https://api.ipify.org
.\.venv\Scripts\python.exe -m natbin.runtime_app provider-probe --repo-root . --config config\practice_portfolio_canary.yaml --all-scopes --json
```

Leitura:
- se `Test-NetConnection` falha, o problema está abaixo do bot;
- se `curl --proxy` falha, o problema está no caminho proxy/auth/túnel;
- se os dois passam e o `provider-probe` falha, o problema tende a estar na sessão IQ sobre o proxy;
- se o WARP fizer passar, trate isso como **workaround operacional válido** e evidência de problema de rota/egress fora do bot.

### Sintoma: `provider_start_gate` passa, mas `canary_go_no_go` fica em `NO_GO_REPAIR`
Provável camada:
- intelligence / canary artifacts

Primeiros comandos:
```powershell
.\scripts\tools\portfolio_cp_meta_maintenance.cmd --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\portfolio_canary_closure_report.cmd --config config\practice_portfolio_canary.yaml --json
```

O que observar:
- `missing_artifact_scopes`
- `cp_meta_missing_scopes`
- `gate_fail_closed_scopes`
- `pack_available`
- `eval_available`
- `candidate_source`
- `allocation_source`
- `repaired_scope_tags` vs `unresolved_scope_tags`

### Sintoma: runtime roda, mas não sai trade nenhum
Provável camada:
- estratégia/intelligence/regime, não necessariamente erro

Primeiros comandos pós-run:
```powershell
.\scripts\tools\provider_stability_report.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
```

O que observar:
- `regime_block`
- `below_ev_threshold`
- `not_in_topk_today`
- `cp_reject`
- `watch_scopes` vs `hold_scopes`

Regra importante:
- ausência de trade **não é falha por si só**.

### Sintoma: shakedown/soak roda, mas artifacts parecem incoerentes
Provável camada:
- freshness / repair / artifacts misturados

Ação:
- arquivar `runs/`
- recriar `runs/` limpo
- recomeçar pela ordem correta: `provider_start_gate -> warmup -> maintenance (se necessário) -> scan -> go/no-go -> observe`

### Sintoma: `asset_candidate` ou sinais quebram com erro de SQLite/schema
Provável camada:
- compatibilidade de schema de runtime/state

Ação:
- rodar novamente o fluxo normal no baseline atual;
- se persistir, tratar como problema de migração/compatibilidade e não como problema de estratégia.

---

## Fluxo de troubleshooting por camadas

## A. Problemas de provider/transport

### Perguntas que você deve responder
1. O problema é **fresco**?
2. `provider_start_gate` está falhando agora?
3. `provider-probe` fresco confirma o problema?
4. O proxy simples funciona fora do bot?
5. O WARP muda o resultado?

### Sequência mínima
```powershell
Set-Location C:\Users\hyago_31k5yin\Documents\Thalor
$env:PYTHONPATH = ".;src"
$proxy = (Get-Content .\secrets\transport_endpoint -TotalCount 1).Trim()

Test-NetConnection gate.decodo.com -Port 7000
curl.exe --proxy $proxy https://api.ipify.org
.\scripts\tools\provider_start_gate.cmd --config config\practice_portfolio_canary.yaml --all-scopes --consecutive-ok 2 --json
```

### Interpretação operacional
- **sem WARP falha / com WARP passa**: trate WARP como workaround válido e registre problema de rota/egress externo ao bot;
- `provider_start_gate` falha por muito tempo: **não suba shakedown/soak**;
- `provider_start_gate` passou: siga para warmup e canary.

---

## B. Problemas de canary e artifacts

### Perguntas que você deve responder
1. O provider já está saudável?
2. O canary está em `NO_GO_REPAIR` ou em `GO_WAITING_SIGNAL`?
3. O repair está mudando o before/after de verdade?
4. Os scopes têm `pack_available` e `eval_available`?

### Sequência mínima
```powershell
.\scripts\tools\portfolio_canary_warmup.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\portfolio_cp_meta_maintenance.cmd --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\portfolio_canary_closure_report.cmd --config config\practice_portfolio_canary.yaml --json
```

### Interpretação operacional
- `NO_GO_REPAIR`: ainda há blocker real antes do runtime observacional sério;
- `GO_WAITING_SIGNAL`: canary operacionalmente fechado; pode rodar observação conservadora;
- `repaired_scope_tags=[]` com before/after idêntico: repair foi no-op prático;
- `pack_available=true` + `eval_available=true`: convergência de inteligência ok.

---

## C. Problemas de dados locais e market context

### Sintomas comuns
- `DB local stale ou vazio`
- `market_context local stale`
- `db_missing`
- artifacts locais velhos depois de limpar `runs/`

### Sequência mínima
```powershell
.\scripts\tools\portfolio_canary_warmup.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
```

### Interpretação
- se o provider está saudável e o local está stale, isso é **maintenance**, não colapso do sistema;
- se o local continua stale mesmo com provider saudável, aí sim investigar o fluxo de prepare/refresh.

---

## D. Problemas de runtime/observe

### Sintomas comuns
- runtime roda mas não atualiza artifacts;
- ciclo demora demais;
- ciclo termina com `errors`;
- o processo fica vivo mas não produz dados úteis.

### O que checar sem interferir demais
```powershell
Get-ChildItem .\runs\logs\network_transport.jsonl, .\runs\logs\request_metrics.jsonl, .\runs\logs\runtime_structured.jsonl, .\runs\portfolio_cycle_latest.json, .\runs\portfolio_allocation_latest.json -ErrorAction SilentlyContinue | Select-Object Name,Length,LastWriteTime

Get-Content .\runs\logs\runtime_structured.jsonl -Tail 10
Get-Content .\runs\logs\request_metrics.jsonl -Tail 10
Get-Content .\runs\logs\network_transport.jsonl -Tail 10
```

### Regra importante
Durante o shakedown/soak, evite rodar em paralelo:
- `provider_start_gate`
- `provider_probe`
- `portfolio_cp_meta_maintenance`
- `canary_go_no_go`
- `portfolio_canary_closure_report`

Esses comandos devem entrar **antes** ou **depois** da observação, não durante.

---

## E. Problemas de suíte/testes

### Sequência canônica
```powershell
Set-Location C:\Users\hyago_31k5yin\Documents\Thalor
$env:PYTHONPATH = ".;src"
New-Item -ItemType Directory -Force .\runs\chat_exports | Out-Null
Get-Date | Tee-Object -FilePath .\runs\chat_exports\full_test_suite_timestamp.txt
.\.venv\Scripts\python.exe -m pytest -q 2>&1 | Tee-Object -FilePath .\runs\chat_exports\full_test_suite.txt
Compress-Archive -Path .\runs\chat_exports\full_test_suite* -DestinationPath .\runs\chat_exports_full_suite.zip -Force
```

### Regra
- sem suíte verde, não congele baseline;
- sem suíte verde, não trate o estado atual como base documental confiável.

---

## Protocolo de mudança segura

## Mudança tipo 1 — documentação/workspace apenas
Exemplos:
- mover arquivos de docs;
- criar índices;
- arquivar histórico;
- reorganizar raiz sem tocar em runtime.

### Validação mínima exigida
- `git status --short`
- `pytest -q`

### Risco
Baixo, desde que não toque em código/config/scripts.

---

## Mudança tipo 2 — config operacional conservadora
Exemplos:
- thresholds do canary;
- budgets do governor;
- pacing;
- parâmetros não destrutivos de observação.

### Validação mínima exigida
1. `pytest -q`
2. `provider_start_gate`
3. `portfolio_canary_warmup`
4. `evidence_window_scan`
5. `canary_go_no_go`
6. shakedown curto

### Regra
Não promover direto para soak longo após alterar config sensível.

---

## Mudança tipo 3 — transport/provider/runtime path
Exemplos:
- `network.transport`
- timeouts de connect
- budget de retry
- healthcheck transport
- bridge do IQ client

### Validação mínima exigida
1. `pytest -q`
2. `provider_start_gate`
3. `provider_stability_report`
4. `provider_session_governor`
5. shakedown curto
6. soak médio

### Regra
Não trate mudança de transport como tweak simples. Isso é caminho crítico.

---

## Mudança tipo 4 — intelligence/canary/closure
Exemplos:
- `pack`, `eval`, `cp`, repair, closure, audit, canary gates

### Validação mínima exigida
1. `pytest -q`
2. `provider_start_gate`
3. `portfolio_cp_meta_maintenance`
4. `evidence_window_scan`
5. `canary_go_no_go`
6. `portfolio_canary_closure_report`
7. shakedown curto

### Regra
Não considere resolvido só porque o subprocesso retornou 0. Verifique **o before/after do estado**.

---

## Mudança tipo 5 — execution/reconcile
Exemplos:
- ordem, submit, reconcile, broker_orders, runtime_execution

### Validação mínima exigida
1. `pytest -q`
2. provider/canary íntegros
3. shakedown curto
4. soak médio em PRACTICE
5. análise de `runtime_execution.sqlite3`, orders e reconcile

### Regra
Não misture mudança de execution com mudança de canary/transport no mesmo pacote se puder evitar.

---

## O que nunca fazer no impulso

### 1. Não tocar em `./secrets`
Especialmente:
- `secrets/transport_endpoint`

### 2. Não limpar `data/` por reflexo
`data/` é base local útil. O alvo usual de limpeza é `runs/`, não `data/`.

### 3. Não confiar em artifact velho no lugar de probe fresco
Se o provider está oscilando, `provider_start_gate`/`provider-probe` fresco valem mais do que relatórios antigos verdes.

### 4. Não subir soak sem gate
A sequência mínima é sempre:
- `provider_start_gate`
- `portfolio_canary_warmup`
- repair/scan se necessário
- `canary_go_no_go`
- só então `portfolio observe`

### 5. Não mudar várias camadas críticas de uma vez
Exemplo ruim:
- mudar transport
- mudar config de canary
- mudar intelligence
- e reorganizar docs

Tudo no mesmo pacote torna o diagnóstico péssimo.

---

## Quando abortar uma sessão

Aborte se:
- `provider_start_gate` não fecha probes frescos em janela razoável;
- provider colapsa e para de atualizar artifacts úteis;
- runtime entra em erro fatal repetido;
- logs param de crescer;
- o problema exige mexer em secrets/transport de forma improvisada;
- o shakedown/soak entrou numa janela operacional claramente inválida.

---

## Quando continuar uma sessão

Continue se:
- o provider está saudável ou suficientemente estável para o envelope atual;
- `canary_go_no_go` está em `GO_WAITING_SIGNAL`;
- logs e artifacts estão sendo atualizados;
- os blockers restantes são de regime/no-trade, não de colapso técnico.

---

## Regra de ouro do Hyago do futuro

Quando estiver em dúvida, faça estas quatro perguntas:

1. **O provider está realmente saudável agora?**
2. **O canary está fechado ou ainda está em repair?**
3. **O problema é de estratégia/regime ou de infraestrutura/artifact?**
4. **Eu estou prestes a mudar algo sem uma validação proporcional ao risco?**

Se responder com calma a essas quatro perguntas, você provavelmente evita 80% dos erros operacionais do Thalor.
