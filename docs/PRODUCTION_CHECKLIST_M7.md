# Production Checklist (M7)

O comando canônico do checklist final é:

```bash
python -m natbin.runtime_app release --repo-root . --config config/multi_asset.yaml --json
```

## Gates avaliados

- posture de segurança (`security_posture`)
- kill-switch e drain mode
- stale artifact guard / lock refresh
- modo de execução (`paper` vs `live`)
- readiness do Telegram
- import do dashboard
- perfis Docker (`Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`)
- runbooks/documentação final
- release hygiene (`natbin.release_hygiene`)
- multi-asset habilitado/desabilitado

## Interpretação

- `severity=ok` → checklist limpo
- `severity=warn` → liberável só com decisão consciente
- `severity=error` → não chamar de production-ready live
- `ready_for_live=true` → checklist limpo **e** execução live IQ habilitada

## Regra operacional recomendada

Antes de virar live:

1. `runtime_app security`
2. `runtime_app health`
3. `runtime_app release`
4. `runtime_app alerts release`
5. só então habilitar `execution.mode=live`

## Artefato persistido

O checklist é salvo em:

- `runs/control/<scope_tag>/release.json`


## Hardening fino (M7.1)

Antes de qualquer go-live, rodar também:

```powershell
python -m natbin.runtime_app incidents status --repo-root . --config config/multi_asset.yaml --json
python -m natbin.runtime_app incidents drill --repo-root . --config config/multi_asset.yaml --scenario broker_down --json
```
