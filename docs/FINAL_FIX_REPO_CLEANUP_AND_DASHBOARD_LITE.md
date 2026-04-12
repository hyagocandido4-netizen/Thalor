# Final fix: repo cleanup, config layering and lightweight dashboard

## O que foi corrigido

### 1. Warnings `sklearn/scipy`

Os warnings vinham do caminho `LogisticRegression(..., solver="lbfgs")` usado como
baseline/stacking binário. O solver foi trocado para uma construção compatível
(`liblinear`) através de um helper comum (`natbin.ml_compat.build_binary_logreg`).

### 2. Config duplicado entre `.env`, `base.yaml` e perfis

Foram feitas duas mudanças:

- perfis YAML agora suportam `extends: base.yaml`
- `.env` deixou de ser um override irrestrito de comportamento por padrão

Com isso:

- comportamento fica no YAML
- secrets e knobs locais continuam no `.env`
- process env `THALOR__*` continua tendo precedência total

Se você realmente quiser o comportamento antigo da `.env` controlando qualquer
`THALOR__*`, exporte no processo:

```powershell
$env:THALOR_DOTENV_ALLOW_BEHAVIOR="1"
```

### 3. Dashboard pesado para VPS

O repositório ganhou `natbin.dashboard_lite`, que gera HTML estático e pode ser
servido por um HTTP server padrão, sem Streamlit.

Uso recomendado para VPS:

```powershell
python -m natbin.dashboard_lite --repo-root . --config config/multi_asset.yaml --serve --port 8501 --loop-sec 15
```

O Streamlit continua opcional para exploração local:

```powershell
pip install -r requirements-dashboard.txt
python -m natbin.dashboard --repo-root . --config config/multi_asset.yaml
```

### 4. `.dockerignore` e imagem mais enxutos

- `requirements.txt` não inclui mais Streamlit
- Docker compose passou a usar `dashboard_lite`
- `.dockerignore` foi endurecido para excluir testes, diretórios de runtime,
  package notes legados e relatórios pesados

### 5. Package notes antigos

Os arquivos `README_PACKAGE_*` antigos podem ser arquivados em:

- `docs/package_history/legacy/`

Comando:

```powershell
python scripts/tools/final_fix_apply.py --repo-root . --json
```

## Smoke test do pacote

```powershell
$env:PYTHONPATH="src"
python scripts/tools/final_fix_package_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
