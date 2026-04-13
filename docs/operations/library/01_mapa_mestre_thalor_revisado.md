# 01 — Mapa Mestre do Thalor (revisado)

## O que este documento deve fazer
Este documento não é uma lista de arquivos nem um resumo executivo. Ele deve funcionar como um **mapa real** do Thalor:

- mostrar a **forma** do sistema;
- delimitar as **camadas** e as **fronteiras**;
- explicar como o projeto **se move** durante uma sessão real;
- mostrar onde o Thalor **está hoje**;
- e indicar para onde o projeto **deve evoluir**, sem perder o rumo.

A ideia é simples: o Hyago do futuro deve abrir este documento e conseguir responder rapidamente:

1. **Que sistema é esse?**
2. **Onde eu estou dentro dele?**
3. **O que é estrutural, o que é operacional e o que é evolução?**
4. **O que não pode ser violado?**
5. **Qual é o próximo nível natural do projeto?**

---

## 1. A identidade do Thalor
O Thalor é um **sistema de operação algorítmica conservadora**, Windows-first, com provider IQ Option e proxy Decodo, desenhado para:

- observar múltiplos ativos em paralelo;
- operar em envelope extremamente restrito;
- coletar evidência operacional rica;
- sobreviver a ruído de provider, artifacts incompletos e instabilidades parciais;
- e permitir evolução controlada, em vez de agressão operacional prematura.

O Thalor **não é apenas um bot de execução**. Ele é, na prática, a soma de cinco coisas:

1. **uma pilha de conectividade e sessão**;
2. **uma pilha de dados locais**;
3. **uma pilha de inteligência e decisão**;
4. **uma pilha de portfolio e governança**;
5. **uma pilha de verdade operacional e diagnóstico**.

Quando o projeto fica difícil de entender, normalmente é porque se está olhando para apenas uma dessas cinco pilhas e esquecendo as outras.

---

## 2. O norte do projeto
O norte do Thalor, hoje, deve ser entendido assim:

> **Transformar o Thalor em um sistema operacionalmente soberano, observável e promovível, começando por PRACTICE conservador e só avançando quando estabilidade e evidência justificarem.**

Esse norte impõe uma disciplina importante:

- o foco atual não é “operar muito”;
- o foco atual não é “forçar trades”;
- o foco atual não é “parecer pronto para live”; 
- o foco atual é **ficar coerente, previsível, auditável e repetível**.

Em outras palavras: o Thalor ainda está no estágio em que a **qualidade da operação** vale mais do que o **volume da operação**.

---

## 3. As invariantes do sistema
Estas são as regras que definem o espaço seguro do projeto. Se uma delas for violada, o mapa inteiro começa a perder valor.

### Invariantes técnicas e operacionais
- `./secrets` não deve ser mexido no impulso.
- `secrets/transport_endpoint` é sagrado.
- Decodo é permanente.
- O envelope atual canônico é **PRACTICE conservador**.
- O canary vigente é **multi-asset na observação** e **top-1 / single-position na execução**.
- O sistema deve ser operado por **gates e artifacts frescos**, não por sensação subjetiva.
- `data/` é base operacional. `runs/` é evidência operacional. Eles não são a mesma coisa.
- Suíte verde e baseline congelado têm precedência sobre improviso.

### Invariante cognitiva
A mais importante de todas:

> **O Thalor precisa continuar operável pelo Hyago do futuro sem depender de memória difusa.**

Essa invariante justifica a biblioteca documental inteira.

---

## 4. O mapa em quatro vistas
Para realmente dominar o Thalor, é melhor vê-lo em quatro mapas diferentes ao mesmo tempo:

1. **Mapa estrutural** — como o projeto está organizado fisicamente.
2. **Mapa de fluxo** — como o sistema se move em runtime.
3. **Mapa operacional** — como se opera uma sessão real.
4. **Mapa de evolução** — para onde o projeto está indo.

Abaixo estão esses quatro mapas.

---

## 5. Mapa estrutural — a geografia do repositório

```text
Thalor/
├── src/                    -> código-fonte principal vigente
├── config/                 -> perfis e configuração operacional
├── scripts/tools/          -> tooling de operação, repair e diagnóstico
├── tests/                  -> suíte automatizada, baseline técnico
├── docs/                   -> documentação viva do projeto
│   ├── operations/         -> runbooks e operação atual
│   ├── testing/            -> documentação de teste/validação
│   └── history/            -> legado e histórico preservado
├── data/                   -> base local de candles e datasets
├── runs/                   -> evidência viva de runtime e controle
├── secrets/                -> área sensível, fora do jogo casual
└── archive/                -> material operacional arquivado
```

### Como pensar cada zona

#### `src/`
É o **motor** do Thalor. Aqui vivem as implementações vigentes. A pergunta desta zona é: **como o sistema realmente se comporta?**

#### `config/`
É o **painel de comportamento** do sistema. Não é detalhe: mudar config muda envelope, risco, pacing, governança, execution mode e comportamento decisório.

#### `scripts/tools/`
É o **painel operacional** do Thalor. Aqui estão os comandos que permitem operar, inspecionar, reparar e fechar o sistema.

#### `tests/`
É o **tribunal técnico** do projeto. A suíte verde define se o estado atual merece ser congelado, promovido ou modificado.

#### `docs/`
É a **memória externa organizada** do projeto. Não é enfeite. É parte da soberania do sistema.

#### `data/`
É a **base persistente local de entrada analítica**. Candles e datasets vivem aqui. Não limpar no impulso.

#### `runs/`
É a **evidência operacional viva**. Tudo que aconteceu na sessão recente tende a aparecer aqui.

#### `secrets/`
É a **zona protegida**. Não é área de experimentação.

---

## 6. Mapa de fluxo — como o Thalor se move

O fluxo canônico do sistema pode ser visto assim:

```text
[Profile / Config]
        |
        v
[Transport / Proxy / Provider Session]
        |
        v
[Collect Recent / Make Dataset / Refresh Market Context]
        |
        v
[Observer / Intelligence / Candidate / Eval / Pack]
        |
        v
[Portfolio / Allocation / Governor / Envelope]
        |
        v
[Execution / Reconcile / Persistence]
        |
        v
[Control Artifacts / Diagnostics / Closure / Bundles]
```

Esse fluxo parece linear, mas na prática o Thalor funciona como **um sistema em laços**.

### Laço 1 — laço de conectividade
Mantém viva a relação entre Thalor, proxy e provider.

### Laço 2 — laço de dados
Atualiza candles, datasets e market context.

### Laço 3 — laço de inteligência
Transforma dados em candidate/eval/pack e bloqueios explicáveis.

### Laço 4 — laço de portfolio
Ranqueia scopes, aplica envelope e decide se algo pode ou não virar execução.

### Laço 5 — laço de verdade operacional
Produz provider probe, governor, stability, closure e bundles.

Se um laço quebra, os outros podem continuar parcialmente. É por isso que o sistema às vezes parece “vivo” mesmo quando não está operacionalmente pronto.

---

## 7. Mapa de fronteiras — o que está dentro e o que está fora do seu controle

Uma grande fonte de confusão em sistemas complexos é não separar claramente o que está sob controle do projeto e o que está fora dele.

### Dentro do controle do Thalor
- código em `src/`
- perfis em `config/`
- tooling em `scripts/tools/`
- schema local, artifacts e docs
- governança de envelope
- repair e diagnostics
- baseline técnico e suíte

### Parcialmente dentro do controle do Thalor
- qualidade e frescor dos artifacts locais
- pacing do canary
- timing e robustez dos repairs
- headroom dos ciclos
- observabilidade e interpretação operacional

### Fora do controle direto do Thalor
- estabilidade real da IQ Option
- ruído de `upstream_digital_metadata`
- flapping do caminho de rede
- rota entre sua máquina e o Decodo
- qualidade de peering/ISP
- janelas de mercado sem entrada elegível

Essa fronteira é importante porque evita dois erros:

1. culpar o código por tudo;
2. culpar o mundo externo por tudo.

---

## 8. Mapa operacional — o ciclo de uma sessão real

O Thalor, hoje, deve ser operado em sessões disciplinadas. O fluxo certo é este:

```text
1. Preparar workspace limpo
2. Validar provider fresco
3. Rodar warmup
4. Rodar maintenance quando necessário
5. Rodar evidence / stability / governor / go-no-go
6. Iniciar observe (shakedown ou soak)
7. Monitorar sem interferir
8. Rodar closure / exportar artifacts / arquivar evidência
```

### O que isso significa na prática
O Thalor não é um sistema para “ligar e ver no que dá”.
Ele é um sistema para:

- validar antes de subir;
- observar enquanto roda;
- e fechar com artifacts claros no final.

Essa disciplina é parte da arquitetura, não só da operação.

---

## 9. As camadas principais, com delimitação funcional

### Camada A — Configuração e perfis
**Papel:** decidir o comportamento do sistema.

**Pergunta que ela responde:**
> Em que modo o Thalor está operando?

**Subdomínios principais:**
- `base.yaml`
- `practice_portfolio_canary.yaml`
- `live_controlled_practice.yaml`
- `live_controlled_real.yaml`
- `multi_asset.yaml`
- `*.example`

**Risco operacional:** alto. Mudanças aqui alteram o envelope inteiro.

---

### Camada B — Transporte e provider
**Papel:** levar o sistema até uma sessão viva com o provider.

**Pergunta que ela responde:**
> O Thalor consegue falar com o mundo externo agora?

**Peças principais:**
- `network_transport`
- `connectivity`
- `iq_client`
- `iqoption` broker adapter
- `provider_probe`
- `provider_start_gate`

**Sinal de maturidade:** o provider não precisa ser perfeito; precisa ser **suficiente e compreensível**.

---

### Camada C — Dados locais
**Papel:** manter uma base utilizável de candles, dataset e market context.

**Pergunta que ela responde:**
> O sistema tem solo local firme para pensar?

**Peças principais:**
- `collect_recent`
- `make_dataset`
- `refresh_market_context`
- `data/*.sqlite3`
- `data/datasets/*`
- `runs/market_context_*`

**Sinal de maturidade:** o sistema deve conseguir distinguir entre falta de provider e falta de base local.

---

### Camada D — Inteligência e observer
**Papel:** transformar o estado do mercado em candidate/eval/pack, com bloqueios explicáveis.

**Pergunta que ela responde:**
> Há um sinal operacionalmente confiável aqui?

**Peças principais:**
- `observer`
- `intelligence`
- `latest_eval`
- `pack`
- `candidate_source`
- `allocation_source`
- CP / drift / regime / retrain surface

**Sinal de maturidade:** o sistema não deve só “dar HOLD”; deve conseguir explicar por que está segurando.

---

### Camada E — Portfolio e governor
**Papel:** transformar múltiplos scopes em um envelope de observação e, se permitido, em uma única decisão conservadora.

**Pergunta que ela responde:**
> Mesmo que existam vários scopes, qual é o comportamento seguro do sistema agora?

**Peças principais:**
- `portfolio.runner`
- `runtime_budget`
- `provider_stability_report`
- `provider_session_governor`
- `evidence_window_scan`
- `portfolio_cycle_latest.json`
- `portfolio_allocation_latest.json`

**Sinal de maturidade:** o sistema deve privilegiar coerência de envelope sobre entusiasmo de execução.

---

### Camada F — Execução e reconcile
**Papel:** registrar intenção, ordem, evento e reconciliação.

**Pergunta que ela responde:**
> O que o Thalor realmente tentou fazer, e o que de fato aconteceu?

**Peças principais:**
- runtime execution process
- execution repo
- reconcile
- `runtime_execution.sqlite3`
- `runtime_control.sqlite3`

**Sinal de maturidade:** execução deve ser rastreável, mesmo quando não houver trade.

---

### Camada G — Control plane e closure
**Papel:** dizer a verdade sobre o estado atual do sistema.

**Pergunta que ela responde:**
> Posso operar, devo reparar, ou devo esperar?

**Peças principais:**
- `provider_probe`
- `provider_start_gate`
- `portfolio_canary_warmup`
- `portfolio_cp_meta_maintenance`
- `evidence_window_scan`
- `canary_go_no_go`
- `portfolio_canary_closure_report`
- bundles e `runs/control/_repo/`

**Sinal de maturidade:** o projeto deve ficar cada vez menos dependente de interpretação subjetiva.

---

## 10. As fontes de verdade, por prioridade
Nem todo output tem o mesmo peso.

### Prioridade 1 — verdade fresca de sessão
- `provider_start_gate`
- `provider_probe`

### Prioridade 2 — verdade fresca de envelope
- `provider_stability_report`
- `provider_session_governor`
- `evidence_window_scan`
- `canary_go_no_go`
- `portfolio_canary_closure_report`

### Prioridade 3 — verdade de ciclo e decisão
- `portfolio_cycle_latest.json`
- `portfolio_allocation_latest.json`
- signal artifacts por scope

### Prioridade 4 — verdade histórica/forense
- `network_transport.jsonl`
- `request_metrics.jsonl`
- `runtime_structured.jsonl`
- `runtime_execution.sqlite3`
- `runtime_control.sqlite3`

### Regra que deve ser lembrada sempre
> **Artifact fresco vence artifact velho.**

O Thalor já mostrou na prática que um artefato “verde” antigo pode coexistir com um provider-probe fresco ruim. Essa regra precisa ficar viva na cabeça do operador.

---

## 11. O mapa do presente — onde o projeto está hoje
Hoje o Thalor está neste estágio:

### Estado técnico
- baseline validado com suíte completa verde;
- canary coerente e fechando em `healthy_waiting_signal`;
- pipeline de repair/convergência funcional;
- workspace suficientemente profissionalizado para servir de base documental.

### Estado operacional
- envelope principal em `PRACTICE`;
- foco em observação multi-asset com execução top-1/single-position;
- provider pode aparecer como `degraded` sem invalidar o envelope;
- o sistema ainda é conservador e aceita muito bem “não operar”.

### Estado cognitivo desejado
- o projeto não deve mais depender de memória implícita;
- o operador deve conseguir navegar por documentos, commands e artifacts;
- a biblioteca operacional passa a ser parte do sistema.

---

## 12. O mapa do futuro — para onde o Thalor deve ir
Este é o ponto que estava faltando no documento anterior: um mapa mestre não deve mostrar só o presente; ele deve mostrar o **horizonte natural** do sistema.

Eu vejo o futuro do Thalor em **cinco níveis de maturidade**.

### Nível 1 — Sistema coerente
Objetivo: parar de quebrar e parar de se contradizer.

**Status:** essencialmente atingido.

### Nível 2 — Sistema operável
Objetivo: o Hyago do futuro consegue operar o Thalor sem se perder.

**Status:** em construção, com a biblioteca operacional viva.

### Nível 3 — Sistema repetível
Objetivo: várias sessões de shakedown/soak dão resultados coerentes, não apenas uma janela boa isolada.

**Status:** próximo foco natural.

### Nível 4 — Sistema promovível
Objetivo: o Thalor não é só estável em PRACTICE; ele produz evidência suficiente para avaliação séria de promoção de envelope.

**Status:** ainda não. Este nível exige repetição e leitura fria dos soaks.

### Nível 5 — Sistema governável
Objetivo: o projeto cresce sem perder soberania, com documentação, critérios de mudança, runbooks, troubleshooting e rastreabilidade fortes.

**Status:** começando agora com esta biblioteca.

### Forma curta de pensar o futuro do projeto
```text
Funcionar
   ->
Ficar coerente
   ->
Ficar operável
   ->
Ficar repetível
   ->
Ficar promovível
   ->
Ficar governável
```

Esse é o mapa do futuro do Thalor. Não é uma lista de features; é uma escada de maturidade.

---

## 13. O que ainda não deve acontecer
Mesmo com o projeto muito mais maduro, este mapa mestre também precisa delimitar o que **não** deve acontecer agora.

Ainda não é hora de:
- aumentar agressivamente paralelismo;
- sair do envelope top-1 / single-position sem evidência repetida;
- tratar `GO_WAITING_SIGNAL` como fracasso;
- confundir provider `degraded` com sistema inviável;
- mexer em secrets/transport por impulso;
- voltar para grandes ciclos de engenharia sem antes observar o comportamento real.

O futuro do projeto não é “mais complexidade”. É **mais domínio sobre a complexidade já criada**.

---

## 14. O mapa de mudança segura
Se o Hyago do futuro quiser mudar algo, este documento deve ajudá-lo a perguntar primeiro:

1. Estou mudando **config**, **pipeline operacional**, **inteligência** ou **execução**?
2. Isso altera o envelope, ou só melhora ergonomia/observabilidade?
3. Isso exige apenas suíte, ou também shakedown?
4. Isso mexe no caminho crítico?
5. Isso preserva a operabilidade futura do sistema?

Se a resposta a essas perguntas estiver nebulosa, a mudança ainda não está madura.

---

## 15. Cinco memórias que o Hyago do futuro deve guardar
Se todo o resto falhar, lembre destas cinco frases:

1. **O Thalor é um sistema em camadas, não um script grande.**
2. **Provider fresco vem antes de qualquer interpretação otimista.**
3. **`data/` é base; `runs/` é evidência.**
4. **O canary pode estar saudável mesmo sem trade.**
5. **O futuro do projeto é repetibilidade e governabilidade, não pressa.**

---

## 16. Como usar este documento
Use este mapa em três situações:

### Quando estiver perdido
Leia as seções 4, 5, 8 e 10.

### Quando quiser mudar algo
Leia as seções 9, 12, 13 e 14.

### Quando quiser explicar o Thalor para si mesmo
Leia as seções 1, 2, 3 e 12.

---

## Próxima leitura recomendada
Depois deste mapa, siga para:

**`02_runbook_operacional_thalor.md`**

Porque o mapa diz **o que o Thalor é**.
O runbook diz **como operá-lo sem se perder**.
