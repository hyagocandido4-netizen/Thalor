# Dashboard Local (Package V)

Este pacote adiciona um **dashboard local, read-only**, para acompanhar em tempo real:

- Estado do control-plane (health / precheck)
- Último ciclo multi-asset (portfolio observe)
- Últimas decisões por `scope_tag`
- Feed dos eventos de execução (`runs/logs/execution_events.jsonl`) quando existir

A implementação é feita em **Streamlit** (web local).

## 1) Instalação

No seu venv do Thalor:

```powershell
pip install streamlit
```

> Opcional: se você preferir travar versão, pode adicionar ao seu `requirements-dev.txt` ou equivalente.  
> O dashboard foi desenhado para **não impactar o CI** (Streamlit não é requerido pelos testes).

## 2) Como rodar

Na raiz do repo:

```powershell
python -m natbin.dashboard --repo-root . --config config/multi_asset.yaml
```

Se quiser mudar a porta:

```powershell
python -m natbin.dashboard --repo-root . --config config/multi_asset.yaml --port 8502
```

O Streamlit normalmente imprime a URL no terminal (ex.: `http://localhost:8501`).

## 3) Segurança / Sem efeitos colaterais

- O dashboard **não executa ordens**.
- Ele lê arquivos JSON e bancos SQLite **em modo read-only** quando possível.
- Se algum arquivo não existir (primeira execução), ele só mostra um aviso.

## 4) Estrutura de arquivos

Arquivos adicionados:

- `src/natbin/dashboard/__main__.py` → launcher (`python -m natbin.dashboard`)
- `src/natbin/dashboard/app.py` → app Streamlit
- `scripts/tools/run_dashboard.ps1` → helper opcional no Windows
- `tests/test_dashboard_importable.py` → garante que o módulo não quebra CI sem Streamlit
