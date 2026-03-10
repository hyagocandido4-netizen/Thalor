# Phase 0 Closeout — arquitetura canônica

A Fase 0 encerra a transição do código “patchy” para uma estrutura canônica:

- `natbin.domain.*`  → regras/modelos/dataset/gating
- `natbin.adapters.*` → integrações externas (broker/client)
- `natbin.usecases.*` → ações operacionais (collect/dataset/refresh/observe)
- `natbin.runtime.*` → control-plane/runtime
- `natbin.state.*` → persistência/repos

Os módulos na raiz de `src/natbin/` permanecem apenas como *compatibility shims* para evitar quebra de imports/CLI legadas.
