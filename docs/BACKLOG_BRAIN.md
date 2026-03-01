\# BACKLOG\_BRAIN — Roadmap do “cérebro” (ML + decisão) do iq-bot



\*\*Última atualização:\*\* 2026-02-22 (BRT)  

\*\*Objetivo central:\*\* poucos sinais, alta convicção, decisões auditáveis, melhoria incremental sem autoengano.



Este documento registra hipóteses, melhorias e experimentos do “cérebro” do bot (features, modelo, calibração, tuning, gating, sinais).  

Tudo aqui deve virar \*\*commit rastreável\*\* quando entrar no core.



---



\## Princípios de evolução



1\) \*\*Evidência > intuição\*\*  

&nbsp;  Mudança só entra se passar nos testes (paper/multiwindow/live) com critérios claros.



2\) \*\*Uma variável por vez\*\*  

&nbsp;  Evitar “pacotes de mudanças” que impedem saber o que causou melhora/piora.



3\) \*\*Auditabilidade total\*\*  

&nbsp;  Todo sinal deve ser justificável (reason, conf, rank, bounds, versão do modelo).



4\) \*\*Sem promessas\*\*  

&nbsp;  Métricas do projeto são hit rate/coverage/estabilidade. Não há promessa de ganho.



---



\## Métricas oficiais



\### Primárias

\- \*\*Hit rate (apenas trades emitidos)\*\*: acerto em CALL/PUT quando `reason=topk\_emit`.

\- \*\*Coverage\*\*: % de candles que viram trade.

\- \*\*Trades/dia\*\*: alvo típico \*\*≤ 2 por dia\*\* (Top-K), podendo testar variações.



\### Secundárias

\- \*\*Estabilidade multiwindow\*\*: hit rate ponderado por trades em janelas pseudo-futuras.

\- \*\*Drift de calibração\*\*: distribuição de `proba\_up` e `conf` no tempo.

\- \*\*Tempo de execução\*\*: robustez do loop (não travar; não exceder 2–3 min).



---



\## Escada de testes (gating para entrar no core)



1\) \*\*Paper Holdout (rápido)\*\*

&nbsp;  - split temporal 80/20

&nbsp;  - gera baseline



2\) \*\*Multiwindow (pseudo-futuro)\*\*

&nbsp;  - 6 janelas sequenciais (expanding train)

&nbsp;  - critério de robustez



3\) \*\*Live Observe\*\*

&nbsp;  - rodar com scheduler

&nbsp;  - avaliar somente `topk\_emit` após atingir N mínimo



---



\## Critérios de aceite (Definition of Done)



Uma mudança só entra como “core” se:



\- \*\*Multiwindow:\*\* melhora \*\*≥ +1,0 pp\*\* no hit rate ponderado  

&nbsp; \*\*OU\*\* mantém hit rate e reduz coverage mantendo trades mínimos;

\- \*\*Trades mínimos:\*\* multiwindow com \*\*≥ 60 trades\*\* (ou definido no experimento);

\- \*\*Live (quando aplicável):\*\* após \*\*≥ 50 emits\*\*, manter ou melhorar hit rate sem explosão de sinais;

\- \*\*Sem regressão operacional:\*\* loop continua estável (sem travar / sem lock / sem dataset 0).



---



\## Estado atual (baseline)



\- Ativo: \*\*EURUSD-OTC\*\*

\- Timeframe: \*\*5m\*\*

\- Modelo base: \*\*HistGradientBoosting + calibração sigmoid\*\*

\- Seleção: \*\*Top-K por dia (K=2)\*\* + gating por `conf >= threshold` + bounds `vol/bb/atr`

\- Persistência: SQLite `signals\_v2` + CSV diário `live\_signals\_v2\_YYYYMMDD.csv`



---



\## Backlog — Triagem (P0/P1/P2/P3)



\### P0 — Correções / Riscos (obrigatório)

> Baixo esforço, alto impacto em estabilidade e integridade.



\- \[ ] \*\*Guardas numéricas (NaN/Inf)\*\* em features

&nbsp; - RSI: lidar com `avg\_loss=0`, evitar `inf/NaN` em massa

&nbsp; - volratio: evitar divisão por zero e “volume ruim”

&nbsp; - ATR: evitar divisão por close ~0 (raríssimo)

&nbsp; - \*\*Teste:\*\* compileall + dataset build com sanity (sem colunas 100% Inf/NaN)



\- \[ ] \*\*Gaps anômalos e labels\*\*

&nbsp; - criar flag “gap\_too\_big” e descartar labels quando o próximo candle não for confiável

&nbsp; - \*\*Teste:\*\* dataset não pode zerar; multiwindow não pode degradar forte



\- \[ ] \*\*Remover auto-geração de arquivos dentro do loop\*\*

&nbsp; - loop não deve reescrever `collect\_recent.py`

&nbsp; - \*\*Teste:\*\* `observe\_loop.ps1 -Once` funciona com repo “limpo”



\- \[ ] \*\*Metadados do sinal (auditabilidade)\*\*

&nbsp; - gravar no `signals\_v2`: `model\_version`, `train\_rows`, `train\_end\_ts`, `best\_source`

&nbsp; - \*\*Teste:\*\* linha do sinal deve conter esses campos sempre



---



\### P1 — Melhorias práticas (ROI alto)

> Melhoram performance/robustez com custo moderado.



\- \[ ] \*\*Cache de modelo (treinar menos, inferir mais)\*\*

&nbsp; - retreino 1x/dia (ou a cada N candles)

&nbsp; - inferência a cada 5m com modelo persistido

&nbsp; - \*\*Meta:\*\* reduzir falhas e tempo do loop; estabilizar comportamento



\- \[ ] \*\*Tuning robusto por Multiwindow (seleção do best)\*\*

&nbsp; - substituir seleção por um único holdout por otimização em multiwindow

&nbsp; - \*\*Meta:\*\* reduzir “falso 58%” que cai para 52% quando endurece a validação



\- \[ ] \*\*Ensemble simples (calibrated averaging)\*\*

&nbsp; - combinar HGB + LogReg calibrados (média de probas)

&nbsp; - \*\*Meta:\*\* aumentar robustez sem “milagre”

&nbsp; - \*\*Aceite:\*\* multiwindow ≥ +1pp



\- \[ ] \*\*Controle por alvo de trades/dia\*\*

&nbsp; - em vez de threshold fixo, regular para manter volume (ex.: 2 trades/dia)

&nbsp; - \*\*Meta:\*\* consistência operacional + menos sensibilidade a drift



\- \[ ] \*\*Backtest com PnL simplificado (binário)\*\*

&nbsp; - além do hit rate, simular payout/fee (parâmetros explícitos)

&nbsp; - \*\*Meta:\*\* aproximar decisão do mundo real (sem promessas, só cenário)



---



\### P2 — Inovações promissoras (experimental)

> Testar como experimento separado; só entra se bater critérios.



\- \[ ] \*\*Gating aprendido: “tradeável vs não tradeável” (2 estágios)\*\*

&nbsp; - Estágio A: classificador HOLD vs OPERAR

&nbsp; - Estágio B: direcional CALL/PUT

&nbsp; - \*\*Meta:\*\* aumentar qualidade por trade mantendo poucos sinais



\- \[ ] \*\*Conformal / uncertainty gating\*\*

&nbsp; - transformar calibração em regra de abstinência com garantias locais

&nbsp; - \*\*Meta:\*\* HOLD “explicável” + menos overtrading



\- \[ ] \*\*Top-K dinâmico\*\*

&nbsp; - K pode variar por regime (com guardrails rígidos)

&nbsp; - \*\*Meta:\*\* aproveitar dias bons sem explodir volume



---



\### P3 — Baixa prioridade / alto custo (não agora)

> Só considerar após score live sólido e pipeline maduro.



\- \[ ] \*\*Reinforcement Learning\*\*

\- \[ ] \*\*Sentiment/news features para OTC\*\*

&nbsp; - (OTC pode não refletir diretamente fundamentos do mercado “real”)



---



\## Sugestões externas (Grok) — Triadas



> Mantemos aqui como “entrada”, mas só sobe após teste.



\### Aproveitar (candidatas P0/P1)

\- Guardas NaN/Inf em RSI/ATR/volratio

\- Melhorar regime filter (rolling bounds / controle de coverage)

\- Ensemble (LogReg + HGB calibrados)

\- Logging/auditabilidade mais rica

\- Retreino com cadência (ex.: diário)



\### Avaliar com cautela (P2/P3)

\- Features “exóticas” (Ichimoku etc.): só se multiwindow sustentar

\- Sentiment/news: provavelmente baixa correlação em OTC

\- RL: custo alto / risco de overfitting



---



\## Template para novas ideias (copiar/colar)



\*\*Título:\*\*  

\*\*Tipo:\*\* P0 / P1 / P2 / P3  

\*\*Hipótese:\*\*  

\*\*Mudança (técnica):\*\*  

\*\*Métricas-alvo:\*\*  

\*\*Teste mínimo:\*\* paper / multiwindow / live  

\*\*Critério de aceite:\*\*  

\*\*Risco principal:\*\*  

\*\*Rollback:\*\* (como desfazer em 1 commit)



---



\## Log de decisões (curto)



\- 2026-02-22: Top-K por dia (K=2) promoveu melhora vs baseline em multiwindow (↑ hit rate, ↓ coverage).  

\- 2026-02-22: Sincronização: coletar somente candles fechados e alinhar scheduler em :00:15, :05:15…

