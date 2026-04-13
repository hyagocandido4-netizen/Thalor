# 03 — Modos de Operação, Perfis e Configuração Aplicada do Thalor (revisado)

## Objetivo deste documento

Este documento existe para resolver um problema específico:

> **não basta saber que perfis existem; é preciso saber como esses perfis se conectam ao runbook operacional e como configurá-los com segurança para cada uso real do Thalor.**

O documento anterior explicava bem **quais perfis existem** e **quando usar cada um**, mas ainda deixava uma lacuna importante:

- como o **Documento 2 — Runbook Operacional** conversa com esses perfis;
- como escolher um profile a partir do **estado operacional**;
- como alterar um profile sem se perder;
- e quais comandos concretos conectam **modo de operação -> profile -> config -> validação -> execução**.

Este documento é, portanto, a **ponte entre:**
- o **Runbook Operacional**;
- o **Atlas de Configuração**;
- e os **perfis YAML** de `config/`.

---

# 1. Como ler este documento

O jeito certo de usar este documento é seguir esta sequência mental:

1. **qual é o meu objetivo operacional agora?**
2. **em que estado operacional o Thalor está?**
3. **qual profile representa melhor esse objetivo nesse estado?**
4. **quais blocos de config desse profile realmente importam?**
5. **qual procedimento do runbook devo executar com esse profile?**
6. **como valido que a combinação funcionou?**

Em outras palavras:

> **Modo de operação** responde “o que quero fazer”.  
> **Profile** responde “com qual envelope vou fazer”.  
> **Configuração** responde “o que controla esse envelope”.  
> **Runbook** responde “quais comandos eu rodo e em que ordem”.

---

# 2. Regra de ouro

## 2.1. O profile nunca deve ser lido sozinho

No Thalor, nenhum profile deve ser interpretado isoladamente.

Um profile só faz sentido quando você o conecta a três coisas:

- **estado operacional atual** do sistema;
- **objetivo da sessão**;
- **procedimento do runbook** que será executado.

Exemplo:

- `practice_portfolio_canary.yaml` **não é apenas um arquivo YAML**;
- ele é o envelope do estado:
  - provider saudável,
  - canary convergido,
  - observação multi-asset conservadora,
  - coleta rica de dados,
  - execução top-1 em PRACTICE.

Então, sempre pense assim:

> **profile = envelope operacional**
>  
> e não
>  
> **profile = arquivo qualquer**

---

## 2.2. O runbook sem profile é cego; o profile sem runbook é solto

Esse documento existe justamente porque:

- o **runbook sozinho** diz “o que fazer”, mas pode não dizer **em qual envelope**;
- o **documento de perfis sozinho** diz “o que existe”, mas pode não dizer **como usar em sequência operacional real**.

O uso maduro do Thalor precisa da **interligação dos dois**.

---

# 3. Matriz mestre: objetivo -> estado -> profile -> runbook -> configuração

Esta é a matriz mais importante do documento.

## 3.1. Leitura rápida

| Objetivo prático | Estado operacional | Profile principal | Runbook dominante | O que mais manda na config |
|---|---|---|---|---|
| congelar baseline | baseline validado | profile vigente do momento + sem mudanças | seção de freeze/baseline | commit/tag, sem alteração de envelope |
| bootstrap frio | bootstrap / artifacts ausentes | `practice_portfolio_canary.yaml` | gate -> warmup -> maintenance -> scan -> go/no-go | `network.transport`, `execution`, `multi_asset`, `observability` |
| repair do canary | provider ok, canary em repair | `practice_portfolio_canary.yaml` | warmup -> cp maintenance -> scan -> closure | `decision`, `intelligence`, `multi_asset`, artifacts |
| shakedown curto | canary saudável | `practice_portfolio_canary.yaml` | start_gate -> warmup -> observe 12 ciclos | `execution`, `multi_asset`, `failsafe`, `observability` |
| soak médio/longo | canary saudável | `practice_portfolio_canary.yaml` | start_gate -> warmup -> observe 18/48/96 ciclos | `multi_asset`, `observability`, `network.transport`, `failsafe` |
| practice controlado single-asset | provider ok, foco em 1 ativo | `live_controlled_practice.yaml` | gate -> prepare -> candidate -> observe/execução controlada | `execution`, `broker`, `assets`, `multi_asset=false` |
| exploração de envelope multi-asset | engenharia / experimentação | `multi_asset.yaml` | runbook exploratório / nunca como default cego | `multi_asset`, `portfolio`, `risk`, `execution` |
| preparação para REAL/live | promoção futura | `live_controlled_real.yaml` | checklist de promoção / nunca como default | `execution.account_mode`, `broker.balance_mode`, `failsafe`, `risk` |
| onboarding / modelagem | leitura ou criação de profile | `*.example`, `portfolio_example.yaml`, `base.yaml` | atlas de configuração + diff contra profile canônico | defaults, estrutura, chaves esperadas |

---

# 4. Modos de operação do Thalor

Aqui “modo de operação” não significa uma flag única do YAML. É uma combinação de:
- objetivo,
- profile,
- envelope de risco,
- e procedimento do runbook.

---

## 4.1. Modo: baseline validado

### O que é
Modo de congelamento do estado tecnicamente íntegro do projeto.

### Quando usar
- depois de suíte completa verde;
- depois de convergência importante do canary;
- antes de profissionalização do workspace;
- antes de gerar ZIP de base documental.

### Profile
O **profile já vigente** no momento.  
Aqui o foco não é trocar de profile. É **congelar o estado existente**.

### Conexão com o runbook
Use as seções de:
- suíte completa;
- freeze;
- commit/tag;
- coleta de evidência do baseline.

### O que mais manda na configuração
Quase nada deve mudar aqui.  
É um modo de **não mudança funcional**.

### Regra
Se você está em baseline freeze, não deveria estar mexendo em:
- `execution.account_mode`
- `broker.balance_mode`
- `multi_asset.max_parallel_assets`
- thresholds críticos
- `network.transport`

---

## 4.2. Modo: bootstrap frio

### O que é
Modo em que:
- `runs/` está vazia ou recém-limpa;
- artifacts ainda não foram convergidos;
- há pouca ou nenhuma superfície local confiável.

### Profile canônico
**`config/practice_portfolio_canary.yaml`**

### Por quê
Porque ele é o envelope mais conservador que:
- conversa bem com provider-backed execution;
- exercita multi-asset observacional;
- e produz artifacts ricos sem abrir risco demais.

### Conexão com o runbook
Fluxo típico:
1. `provider_start_gate`
2. `portfolio_canary_warmup`
3. `portfolio_cp_meta_maintenance`
4. `evidence_window_scan`
5. `canary_go_no_go`

### Configuração realmente importante aqui
- `network.transport.*`
- `execution.enabled`
- `execution.mode`
- `execution.account_mode`
- `broker.balance_mode`
- `multi_asset.enabled`
- `observability.*`

### Como configurar esse modo
Na prática, você **não inventa um profile novo** para bootstrap.  
Você usa o canary e garante:
- `PRACTICE`
- `max_parallel_assets=1`
- `portfolio_topk_total=1`
- provider gate fresco
- logs/metrics ligados

### O que não fazer
- não usar `multi_asset.yaml` aqui;
- não começar por `live_controlled_real.yaml`;
- não alterar risco antes de convergir artifacts.

---

## 4.3. Modo: repair do canary

### O que é
Estado em que o provider está operacionalmente OK, mas o canary ainda está em:
- `NO_GO_REPAIR`
- `repair_needed`
- ou com artifacts incompletos.

### Profile canônico
**`config/practice_portfolio_canary.yaml`**

### Conexão com o runbook
Fluxo típico:
1. `provider_start_gate`
2. `portfolio_canary_warmup`
3. `portfolio_cp_meta_maintenance`
4. `evidence_window_scan`
5. `canary_go_no_go`
6. `portfolio_canary_closure_report`

### Configuração que mais importa
- `decision.*` (incluindo gate/cp behavior)
- `intelligence.*`
- `multi_asset.*`
- `observability.*`
- qualquer fallback controlado que ajude a convergir artifacts

### Como configurar esse modo
Aqui a regra não é “trocar de profile”.  
É manter o profile canônico e só fazer mudanças **cirúrgicas** nas superfícies que afetam:
- eval
- pack
- cp
- candidate/allocation artifacts

### Anti-padrão
Trocar para profile mais agressivo para “tentar destravar” o canary.  
Isso quase sempre piora a leitura do problema.

---

## 4.4. Modo: canary saudável / aguardando sinal

### O que é
Estado saudável e canônico do projeto hoje:
- `GO_WAITING_SIGNAL`
- `healthy_waiting_signal`
- zero blockers estruturais
- provider pelo menos suficientemente estável

### Profile canônico
**`config/practice_portfolio_canary.yaml`**

### Conexão com o runbook
Fluxo típico:
1. `provider_start_gate`
2. `portfolio_canary_warmup`
3. `portfolio observe --max-cycles ...`

### Configuração que mais importa
- `multi_asset.enabled=true`
- `max_parallel_assets=1`
- `portfolio_topk_total=1`
- `portfolio_hard_max_positions=1`
- `execution.account_mode=PRACTICE`
- `broker.balance_mode=PRACTICE`
- `observability.request_metrics.enabled=true`

### Como configurar esse modo
Você praticamente não deve “configurar” no impulso.  
Você deve **preservar o envelope**.

Se for mudar algo, mude só depois de nova validação.

### O que o profile está dizendo de verdade
Esse modo significa:

> observar 6 scopes, escolher top-1, executar no máximo 1 posição, em PRACTICE, sob governor serializado, com telemetria rica.

---

## 4.5. Modo: shakedown curto

### O que é
Sessão curta para provar integridade integrada:
- runtime
- provider
- artifacts
- request metrics
- cycle budget

### Profile canônico
**`config/practice_portfolio_canary.yaml`**

### Conexão com o runbook
Fluxo típico:
1. `provider_start_gate`
2. `portfolio_canary_warmup`
3. `portfolio observe --max-cycles 12`

### Configuração que mais importa
- a mesma do canary saudável
- mais:
  - `quota-aware-sleep`
  - `precheck-market-context`
  - logs ligados
  - request metrics ligadas

### Como configurar esse modo
Na prática, você não muda o profile.  
Você muda só o **jeito de invocar o runtime**, por exemplo:
- `--max-cycles 12`
- ou `--max-cycles 18`

### Regra
Shakedown é um **modo operacional** derivado do canary, não um profile separado.

---

## 4.6. Modo: soak médio/longo

### O que é
Sessão de horas para coletar dados operacionais ricos e validar estabilidade real.

### Profile canônico
**`config/practice_portfolio_canary.yaml`**

### Conexão com o runbook
Fluxo típico:
1. `provider_start_gate`
2. `portfolio_canary_warmup`
3. `portfolio observe --max-cycles 48` (médio)
4. `portfolio observe --max-cycles 96` (longo)

### Configuração que mais importa
- toda a espinha dorsal do canary
- especialmente:
  - `observability.*`
  - `network.transport.*`
  - `failsafe.*`
  - `multi_asset.*`
  - `decision.*`
  - `intelligence.*`

### Como configurar esse modo
Novamente: o profile não muda.  
O que muda é:
- duração da sessão;
- qualidade da janela operacional;
- disciplina de monitoramento e pós-run.

### Regra
Se você acha que precisa mudar o profile para fazer um soak, provavelmente ainda não está no modo certo.

---

## 4.7. Modo: practice controlado single-asset

### O que é
Modo para trabalhar com execução e observação focadas em um único ativo ou numa trilha mais simples do que o canary multi-asset.

### Profile canônico
**`config/live_controlled_practice.yaml`**

### Quando faz sentido
- quando você quer uma superfície mais simples;
- quando quer estudar um ativo específico;
- quando o multi-asset não é necessário para aquele teste;
- quando precisa reduzir a complexidade operacional da sessão.

### Conexão com o runbook
Fluxo típico:
1. gate fresco
2. prepare/candidate do ativo principal
3. observe ou execução controlada

### Configuração que mais importa
- `multi_asset.enabled: false`
- `execution.mode: live`
- `execution.account_mode: PRACTICE`
- `broker.balance_mode: PRACTICE`
- `assets` focados

### Como configurar esse modo
Aqui sim o **profile troca**.

Você sai do canary multi-asset e entra no:
- `live_controlled_practice.yaml`

### Regra
Use este profile quando a simplificação do ambiente for **parte do objetivo**, e não só por nervosismo com o canary.

---

## 4.8. Modo: exploração multi-asset / engenharia

### O que é
Modo de experimentação de envelope mais agressivo, mais aberto ou mais exploratório.

### Profile associado
**`config/multi_asset.yaml`**

### Quando faz sentido
- investigação técnica;
- engenharia de budget/prepare/fan-out;
- experimentação de envelope;
- comparação entre comportamentos.

### Quando não faz sentido
- shakedown canônico
- soak canônico de coleta conservadora
- baseline operacional principal

### Conexão com o runbook
Este modo não deveria seguir o fluxo padrão sem reflexão.  
Ele exige:
- intenção explícita;
- hipótese clara;
- e validação posterior.

### Configuração que mais importa
- `multi_asset.*`
- `portfolio.*`
- limits e quotas
- governor behavior
- execution envelope

### Regra
Este profile é **de engenharia/exploração**, não de padrão operacional atual.

---

## 4.9. Modo: promoção futura para REAL/live

### O que é
Modo pretendido/futuro, ainda sujeito a critérios muito mais duros de promoção.

### Profile associado
**`config/live_controlled_real.yaml`**

### Leitura correta
Esse profile existe para:
- representar o envelope de REAL/live;
- servir de preparação para futura promoção;
- mas **não** é o modo padrão atual do projeto.

### O que mais importa aqui
- `execution.account_mode: REAL`
- `broker.balance_mode: REAL`
- envelope de risco
- validação muito mais dura
- critérios de promoção explícitos
- runbook de abort muito claro

### Conexão com o runbook
Este modo só deve entrar em cena depois de:
- repetibilidade em PRACTICE;
- observabilidade consolidada;
- confiança operacional forte.

### Regra
`live_controlled_real.yaml` é profile de **promoção futura**, não de curiosidade operacional.

---

## 4.10. Modo: leitura, modelagem e onboarding de config

### O que é
Modo em que você não está necessariamente operando o bot, mas entendendo como a configuração é montada.

### Perfis relevantes
- `config/base.yaml`
- `config/*.example`
- `config/portfolio_example.yaml`

### Função
- entender estrutura;
- comparar defaults;
- prototipar sem tocar o profile canônico.

### Regra
Esses arquivos **não substituem** os perfis canônicos de operação.

---

# 5. Perfis do Thalor e sua ligação com o runbook

Aqui a correlação fica explícita.

---

## 5.1. `config/base.yaml`

### Papel
Fundação conceitual e default do sistema.

### Ligação com o runbook
Não é profile de sessão operacional típico.  
Serve mais para:
- leitura estrutural;
- comparação;
- entendimento dos defaults.

### Como configurá-lo
Normalmente você **não configura o sistema rodando direto com ele**.  
Você usa o `base.yaml` como base mental e deixa os perfis específicos sobrescreverem.

### Pergunta útil
> “o comportamento que estou vendo veio do profile canônico ou já era default do base?”

---

## 5.2. `config/practice_portfolio_canary.yaml`

### Papel
Envelope operacional principal do Thalor hoje.

### Ligação com o runbook
É o profile usado por:
- bootstrap
- repair do canary
- canary saudável
- shakedown
- soak médio
- soak longo

### Como configurá-lo
Você deve pensar nele em três níveis:

#### Nível A — não mexer quase nunca
- `execution.account_mode`
- `broker.balance_mode`
- `network.transport.endpoint_file`
- `max_parallel_assets`
- `portfolio_topk_total`
- hard max positions / pending / trades per day

#### Nível B — mexer com muito cuidado
- `decision.*`
- `intelligence.*`
- `failsafe.*`
- `network.transport.*`
- timing/budget/gate behavior

#### Nível C — ajustes operacionais mais aceitáveis
- observability/log paths
- número de ciclos via CLI
- pequenos ajustes de artifacts e monitoramento

### Regra prática
Se sua pergunta for “qual profile eu uso?”, quase sempre a resposta atual ainda é:
**`practice_portfolio_canary.yaml`**

---

## 5.3. `config/live_controlled_practice.yaml`

### Papel
Practice controlado, mais simples e mais focado.

### Ligação com o runbook
Use quando o runbook te levar a um modo de:
- execução practice single-asset;
- estudo focado de ativo;
- sessão mais enxuta que o canary multi-asset.

### Como configurá-lo
As variáveis centrais são:
- ativo(s) foco
- `PRACTICE`
- multi-asset desligado
- limites conservadores preservados

### Regra prática
Troque para este profile quando você **quiser simplificar o espaço operacional**, não quando quiser “fugir” do canary sem motivo.

---

## 5.4. `config/live_controlled_real.yaml`

### Papel
Profile de envelope real.

### Ligação com o runbook
Conecta com as seções de:
- promoção futura
- checklist reforçado
- abort duro
- validação muito mais rígida

### Como configurá-lo
Mudanças aqui são sensíveis por natureza.  
Não é profile de teste casual.

### Regra prática
Se você não tiver um motivo formal de promoção, você não deveria estar aqui.

---

## 5.5. `config/multi_asset.yaml`

### Papel
Profile de exploração/engenharia.

### Ligação com o runbook
Conecta com:
- experimentação controlada;
- comparação de comportamento;
- engenharia de envelope.

### Como configurá-lo
Sempre com hipótese explícita.
Nunca como “novo default” por impulso.

### Regra prática
Se você estiver em dúvida, **não** use este profile como padrão de operação.

---

## 5.6. `config/*.example` e `portfolio_example.yaml`

### Papel
Templates e moldes.

### Ligação com o runbook
Pouca ligação operacional direta.  
Eles se conectam mais ao:
- Atlas de Configuração
- onboarding
- modelagem

### Regra prática
Não confundir:
- “arquivo útil para entender estrutura”
com
- “profile operacional do projeto”

---

# 6. Como configurar o Thalor a partir do runbook e dos perfis

Esta é a seção mais importante do documento.

---

## 6.1. Situação: “quero rodar um shakedown curto”

### Estado operacional esperado
- provider saudável
- canary já convergido ou convergível rapidamente

### Profile
**`config/practice_portfolio_canary.yaml`**

### O que no profile precisa estar correto
- `execution.account_mode = PRACTICE`
- `broker.balance_mode = PRACTICE`
- `max_parallel_assets = 1`
- `portfolio_topk_total = 1`

### Comandos
```powershell
.\scripts\tools\provider_start_gate.cmd --config config\practice_portfolio_canary.yaml --all-scopes --consecutive-ok 2 --json
.\scripts\tools\portfolio_canary_warmup.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe --repo-root . --config config\practice_portfolio_canary.yaml --topk 1 --lookback-candles 2000 --quota-aware-sleep --precheck-market-context --max-cycles 12
```

### Conclusão
Aqui o modo de uso, o profile e o runbook estão totalmente alinhados.

---

## 6.2. Situação: “quero reparar o canary”

### Estado operacional esperado
- provider ok
- `canary_go_no_go = NO_GO_REPAIR`

### Profile
**`config/practice_portfolio_canary.yaml`**

### Configuração relevante
- `decision.*`
- `intelligence.*`
- artifacts e surface de repair

### Comandos
```powershell
.\scripts\tools\provider_start_gate.cmd --config config\practice_portfolio_canary.yaml --all-scopes --consecutive-ok 2 --json
.\scripts\tools\portfolio_cp_meta_maintenance.cmd --config config\practice_portfolio_canary.yaml --json
.\scripts\tools\evidence_window_scan.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\scripts\tools\canary_go_no_go.cmd --config config\practice_portfolio_canary.yaml --json
```

### Conclusão
Não troca de profile.  
Muda o **estado** e muda o **fluxo do runbook**.

---

## 6.3. Situação: “quero uma sessão de practice mais simples”

### Estado operacional esperado
- você quer reduzir complexidade operacional

### Profile
**`config/live_controlled_practice.yaml`**

### Configuração relevante
- ativos foco
- `PRACTICE`
- `multi_asset=false`

### Comandos típicos
Use o mesmo espírito do runbook, mas com este profile:
```powershell
.\.venv\Scripts\python.exe -m natbin.runtime_app provider-probe --repo-root . --config config\live_controlled_practice.yaml --json
```

e depois os comandos de prepare/candidate/observe adequados ao ativo.

### Conclusão
Aqui sim o modo operacional te leva a **troca de profile**.

---

## 6.4. Situação: “quero soak longo de coleta séria de dados”

### Estado operacional esperado
- `GO_WAITING_SIGNAL`
- provider saudável o suficiente
- canary fechado

### Profile
**`config/practice_portfolio_canary.yaml`**

### Configuração relevante
- exatamente a espinha dorsal do canary

### Comandos
```powershell
.\scripts\tools\provider_start_gate.cmd --config config\practice_portfolio_canary.yaml --all-scopes --consecutive-ok 2 --json
.\scripts\tools\portfolio_canary_warmup.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
.\.venv\Scripts\python.exe -m natbin.runtime_app portfolio observe --repo-root . --config config\practice_portfolio_canary.yaml --topk 1 --lookback-candles 2000 --quota-aware-sleep --precheck-market-context --max-cycles 96
```

### Conclusão
Soak longo é **runbook mais longo**, não outro profile.

---

## 6.5. Situação: “quero preparar o futuro REAL/live”

### Estado operacional esperado
- repetibilidade em PRACTICE já demonstrada

### Profile
**`config/live_controlled_real.yaml`**

### Configuração relevante
- tudo que envolve REAL é sensível

### Regra
Não usar esse profile porque “quero ver como fica”.  
Só usar se estiver em etapa formal de promoção.

---

# 7. Mudanças seguras por tipo de objetivo

---

## 7.1. Quero mudar só a duração da sessão
### Melhor caminho
Não mexer no YAML.  
Mudar só a CLI:
- `--max-cycles 12`
- `--max-cycles 18`
- `--max-cycles 48`
- `--max-cycles 96`

### Risco
Baixo.

---

## 7.2. Quero mudar observabilidade/logging
### Melhor caminho
Mexer em:
- `observability.*`
- paths de logs
- export de artifacts

### Risco
Baixo a médio.

---

## 7.3. Quero mudar envelope do canary
### Melhor caminho
Mexer muito pouco e validar com shakedown novo.

### Campos sensíveis
- `max_parallel_assets`
- `portfolio_topk_total`
- hard max positions
- hard max pending unknown
- `decision.*`
- `failsafe.*`

### Risco
Alto.

---

## 7.4. Quero mudar provider/transport
### Melhor caminho
Só com validação explícita.

### Campos sensíveis
- `network.transport.*`
- `endpoint_file`
- `healthcheck_mode`
- timeouts
- fail-open behavior

### Risco
Muito alto.

---

## 7.5. Quero mudar de PRACTICE para REAL
### Melhor caminho
Não tratar como “pequena mudança de config”.  
Tratar como **mudança de modo operacional**.

### Risco
Máximo.

---

# 8. Anti-padrões mais comuns

- usar `multi_asset.yaml` como default por ansiedade
- trocar profile quando o problema é de estado operacional, não de envelope
- mexer no YAML quando bastava mudar a CLI do observe
- usar `live_controlled_real.yaml` sem etapa formal de promoção
- confundir `execution.mode=live` com conta real
- ignorar a combinação `execution.account_mode` + `broker.balance_mode`
- usar `*.example` como se fossem profiles oficiais de produção prática

---

# 9. O que este documento deve te permitir fazer

Depois de ler este documento, você deve ser capaz de responder com clareza:

- que profile representa o envelope principal do projeto hoje;
- quando o problema exige trocar de profile;
- quando o problema exige apenas trocar de fluxo do runbook;
- quais blocos de config mandam de verdade em cada modo;
- como sair de um estado ruim para um estado saudável sem improviso;
- como conectar **modo de uso -> profile -> config -> comando**.

Se essa ponte estiver clara, o Thalor deixa de parecer um conjunto de YAMLs e comandos soltos, e passa a parecer o que ele realmente é:

> um sistema com envelopes operacionais distintos, cada um ligado a um procedimento claro.
