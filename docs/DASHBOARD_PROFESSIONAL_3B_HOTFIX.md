# Dashboard Professional 3B — Streamlit width + Arrow normalization hotfix

## Problema
Ao abrir o dashboard via Streamlit, o app continuava funcional, mas gerava:

- warnings deprecatórios de `use_container_width`
- aviso do Arrow ao serializar colunas com payloads heterogêneos, como `payload`

## Solução
A camada de exibição do dashboard passou a:

- usar `width="stretch"` nos componentes Streamlit aplicáveis
- converter células tabulares com estruturas aninhadas para strings JSON estáveis antes de montar `DataFrame`
- preservar números, booleanos e strings simples sem alterar o significado operacional

## Impacto
- zero mudança na lógica de analytics
- zero mudança no snapshot bruto
- zero mudança no export report
- somente a camada visual/tabular foi endurecida
