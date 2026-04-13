# Biblioteca Operacional do Thalor

## Objetivo
Esta biblioteca existe para que o **Hyago do futuro** consiga:

- entender o estado atual do Thalor sem depender de memória difusa;
- operar o sistema com segurança;
- localizar rapidamente modos, configs, artifacts e ferramentas de diagnóstico;
- alterar o sistema de forma controlada, sem quebrar o baseline.

## Escopo
A biblioteca documenta o **estado atual consolidado** do Thalor.

Ela **não** tenta narrar toda a história do projeto. Quando necessário, inclui notas curtas para explicar **por que** certas peças existem hoje.

## Princípios
- foco em **operação real**;
- exemplos em **PowerShell**;
- prioridade para **Windows local + PRACTICE + canary**;
- o proxy Decodo e `secrets/transport_endpoint` são tratados como invariantes operacionais;
- documentação curta, complementar e navegável.

## Estrutura
1. `01_mapa_mestre_thalor.md`
   - visão global das camadas do sistema;
   - fluxo canônico do Thalor;
   - fontes de verdade operacionais.

2. `02_runbook_operacional_thalor.md`
   - start, preflight, warmup, shakedown, soak, pós-run e abort.

3. `03_modos_e_perfis_thalor.md`
   - perfis YAML, modos de operação e quando usar cada um.

4. `04_dicionario_de_estados_e_artifacts.md`
   - significado operacional de states, reports e artifacts.

5. `05_atlas_de_configuracao_thalor.md`
   - blocos de configuração, impacto operacional e zonas de risco.

6. `06_troubleshooting_e_mudanca_segura.md`
   - diagnóstico rápido e protocolo de mudança segura.

## Estado-base desta biblioteca
Esta biblioteca assume como base:

- baseline funcional validado;
- suíte completa verde;
- canary coerente e fechado no envelope conservador;
- workspace já profissionalizado o suficiente para diferenciar vigente de legado.

## Leitura recomendada
- comece por `01_mapa_mestre_thalor.md`;
- depois leia `02_runbook_operacional_thalor.md`;
- use os demais documentos como referência de operação, interpretação e mudança.

## Regra de ouro
Nada nesta biblioteca, por si só, autoriza promoção para REAL/live.

Ela serve para **entender, operar, validar e decidir**.
