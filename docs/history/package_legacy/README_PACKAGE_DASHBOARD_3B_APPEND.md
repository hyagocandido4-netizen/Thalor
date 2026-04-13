# Package DASHBOARD-3B — Streamlit width + Arrow normalization hotfix

## Objetivo
Eliminar os warnings de `use_container_width` e evitar o aviso/auto-fix do Streamlit/Arrow quando tabelas do dashboard contêm colunas com payloads heterogêneos (dict/list/path/datetime).

## Escopo
- `width='stretch'` em vez de `use_container_width`
- normalização tabular de payloads aninhados para strings JSON estáveis
- smoke e testes dedicados
