# Package FINAL FIX

Este pacote fecha os problemas abertos do pacote anterior e estabiliza a base de
configuração/inteligência sem regressão de multi-asset, proteção, dashboard ou
produção.

## Corrigido neste pacote

### 1. `extends` em YAML voltou a funcionar de verdade

O loader agora resolve cadeias de `extends` antes da validação Pydantic, com:

- merge recursivo para mapas aninhados
- substituição explícita de listas e escalares pelo filho
- detecção de cadeia de arquivos no `source_trace`

Isso elimina o `ValidationError: extends extra_forbidden` e mantém os perfis de
`config/` limpos e reutilizáveis.

### 2. `.env` deixou de causar drift silencioso de comportamento

A estratégia agora é:

- process env `THALOR__*` continua com precedência total
- `.env` local aplica por padrão apenas chaves modernas seguras
- seções comportamentais (`execution.*`, `decision.*`, `quota.*`, `runtime.*`,
  `multi_asset.*`, `intelligence.*`, `runtime_overrides.*`, etc.) ficam
  bloqueadas no `.env` por padrão
- para reabilitar o comportamento antigo de override irrestrito do `.env`, use:

```powershell
$env:THALOR_DOTENV_ALLOW_BEHAVIOR="1"
```

Isso mantém o bot conservador e reduz o risco de ligar execução/mudar política
sem perceber por causa de um `.env` local antigo.

### 3. warnings `sklearn/scipy` removidos na trilha logística binária

O projeto já possuía o helper `natbin.ml_compat.build_binary_logreg`, mas parte
código ainda instanciava `LogisticRegression(..., solver="lbfgs")` direto.
Agora os caminhos relevantes usam o helper compatível (`liblinear`):

- `src/natbin/intelligence/learned_gate.py`
- `src/natbin/domain/gate_meta.py`
- `src/natbin/research/train_walkforward.py`
- `src/natbin/research/paper_backtest.py`

Isso remove o warning do L-BFGS-B nas versões afetadas de `scikit-learn` /
`SciPy` e unifica a política de regressão logística binária do projeto.

### 4. documentação alinhada com o comportamento real

Foram atualizados:

- `.env.example`
- `README.md`
- `docs/CONFIGURATION_V2.md`
- `docs/ENV_VARS.md`

## Testes executados

```powershell
python -m pytest -q
```

Cobertura de regressão adicionada/atualizada para:

- `extends` em YAML
- `source_trace` com cadeia de herança
- filtro seguro do `.env`
- opt-in explícito via `THALOR_DOTENV_ALLOW_BEHAVIOR`
- warning do solver logístico

## Como aplicar

1. Extraia o **overlay** na raiz do projeto existente.
2. Rode:

```powershell
$env:PYTHONPATH="src"
python -m pytest -q
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```

## Observação de segurança

O pacote completo gerado para compartilhamento foi sanitizado: não inclui `.env`,
`.venv`, `.git`, caches locais nem artefatos efêmeros de runtime.
