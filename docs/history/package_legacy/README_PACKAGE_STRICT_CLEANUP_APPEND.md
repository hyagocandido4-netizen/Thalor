# Package Strict Cleanup Append

Este pacote fecha os pontos que ainda apareciam como **parciais** sob critério de cleanup restrito:

- remoção de scripts de patch legados
- consolidação do código canônico em subpastas
- `effective_config` emitido por helper nativo a cada ciclo/contexto
- lock definitivo do scheduler também em `run_once()`
- smoke + testes para impedir regressão do cleanup
