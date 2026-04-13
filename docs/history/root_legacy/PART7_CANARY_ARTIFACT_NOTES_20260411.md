Parte 7 — fechamento do gargalo de NO_GO_REPAIR do canary

Resumo do achado dominante
- O problema principal não era o provider nem o cp_meta em si.
- O problema era operacional/CLI: `python -m natbin.control.app ...` não executava `main()` quando chamado como módulo.
- Por causa disso, `asset prepare`, `asset candidate` e `intelligence-refresh` disparados pelo maintenance e pelos testes manuais viravam no-op silencioso.
- Além disso, `intelligence-refresh` nem entrava no branch correto quando os argumentos globais vinham antes do subcomando, porque `intelligence-refresh` não estava no conjunto `known` usado pelo preparse de `main()`.

Correções desta parte
1. `src/natbin/control/app.py`
- adiciona `if __name__ == "__main__": raise SystemExit(main())`
- inclui `intelligence-refresh` e `intelligence_refresh` no conjunto `known`
- isso faz o CLI realmente executar e aceitar a forma de invocação já usada no projeto (`--repo-root/--config` antes do subcomando)

2. `src/natbin/ops/intelligence_maintenance.py`
- endurece `_run_runtime_app_json()` para marcar sucesso apenas quando há payload JSON parseável
- marca `missing_payload=true` quando o subprocesso retorna 0 mas não entrega JSON
- muda a ordem do maintenance para:
  `asset_prepare -> asset_candidate -> intelligence_refresh`
- isso garante que o refresh enxergue o decision artifact recém-materializado pelo candidate

3. `tests/test_part31_intelligence_maintenance.py`
- atualiza a expectativa de ordem dos passos
- adiciona teste para `missing_payload`
- adiciona teste para `control.app.main([... intelligence-refresh ...])` com globais antes do subcomando
- mantém um smoke de `python -m natbin.control.app status --json`

Impacto esperado
- `portfolio_cp_meta_maintenance` deixa de reportar passos "ok" que eram no-op silencioso
- `intelligence-refresh` passa a realmente escrever `pack.json`
- `evidence_window_scan` / `intelligence_surface` devem começar a ver `pack_available=true` nos scopes reparados
- o `canary_go_no_go` deve poder sair de `NO_GO_REPAIR` depois de uma nova rodada correta de repair

Risco
- baixo a médio
- mexe em caminho crítico de CLI/ops, mas a mudança é pequena, direta e fácil de verificar

Validação executada
- 11 testes verdes:
  `tests/test_part31_intelligence_convergence.py`
  `tests/test_part31_intelligence_maintenance.py`
