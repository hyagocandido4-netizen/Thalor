# H9.1 CI Selfcheck Hotfix

Corrige falso positivo do `scripts/tools/selfcheck_repo.py`.

## Problema
O selfcheck antigo só reconhecia imports de `env_*` vindos de:
- `from .envutil import ...`
- `from natbin.envutil import ...`

Após o H8/H9, o código canônico passou a importar de:
- `from ..config.env import ...`
- `from natbin.config.env import ...`

Em CI, isso fazia o job `Repo selfcheck` falhar mesmo com os imports corretos.

## Solução
O check agora usa AST para detectar:
- uso real de `env_bool/env_float/env_int/env_str`
- imports canônicos tanto de `envutil` quanto de `config.env`

## Efeito
- elimina o falso positivo no GitHub Actions
- preserva a intenção do check
- continua falhando quando houver uso sem import correspondente
