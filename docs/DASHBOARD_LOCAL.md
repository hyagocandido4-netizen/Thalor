# Dashboard Local / Profissional Thalor

O dashboard local do Thalor agora funciona como um **control deck profissional**, mantendo o princípio de ser **read-only** e seguro.

## O que ele mostra

- estado do control plane (`health`, `security`, `release`, `practice`, `doctor`)
- equity curve e drawdown em tempo real
- KPIs de performance (`win-rate`, `EV`, `Sharpe`, `profit factor`)
- board unificado por asset
- ciclo/alocação portfolio-level
- trades recentes, submit attempts e eventos de execução
- feed de alertas recentes
- export de relatório em HTML + JSON

## Como rodar

```powershell
python -m natbin.dashboard --repo-root . --config config/multi_asset.yaml
```

Alterando a porta:

```powershell
python -m natbin.dashboard --repo-root . --config config/multi_asset.yaml --port 8502
```

## Como exportar relatório

```powershell
python -m natbin.dashboard.report --repo-root . --config config/multi_asset.yaml --json
```

## Segurança / sem efeitos colaterais

- o dashboard **não executa ordens**
- leitura do ledger de execução é feita em modo read-only quando possível
- ausência de artefatos apenas gera aviso visual

## Configuração relevante

O package adiciona a seção `dashboard:` ao config tipado.

Exemplo:

```yaml
dashboard:
  enabled: true
  title: Thalor
  theme: cyber_dragon
  default_refresh_sec: 3.0
  default_equity_start: 1000.0
  max_alerts: 50
  max_equity_points: 500
  report:
    output_dir: runs/reports/dashboard
    export_json: true
```

## Arquivos principais

- `src/natbin/dashboard/app.py`
- `src/natbin/dashboard/analytics.py`
- `src/natbin/dashboard/report.py`
- `src/natbin/dashboard/style.py`
- `tests/test_dashboard_package_3.py`
- `scripts/tools/dashboard_package_3_smoke.py`


## Streamlit direct run compatibility (Dashboard 3A)

The dashboard app now supports both launch modes:

- `python -m natbin.dashboard --repo-root . --config config/multi_asset.yaml`
- `streamlit run src/natbin/dashboard/app.py -- --repo-root . --config config/multi_asset.yaml`

This hotfix avoids relative-import failures when Streamlit executes `src/natbin/dashboard/app.py` directly as a script.

## Dashboard 3B hotfix

O dashboard local agora normaliza payloads tabulares aninhados antes de enviar dados ao Streamlit/Arrow e usa `width="stretch"` nos componentes visuais, evitando warnings de serialização e avisos deprecatórios de `use_container_width`.



## Graceful shutdown

Stopping `python -m natbin.dashboard` with `Ctrl+C` is treated as a normal operator action. The launcher now exits with code `0` and prints `Dashboard stopped by user.` instead of a traceback.


## Status cards: PRACTICE and DOCTOR

Quando o dashboard é aberto com um profile portfolio/multi-asset como `config/multi_asset.yaml`:
- `PRACTICE` pode não ser aplicável, porque a surface de controlled practice exige scope único e execução habilitada.
- `DOCTOR` pode ficar aguardando hidratação do scope (`dataset_ready`, `market_context`) antes de ficar verde.

O cockpit agora normaliza esses cenários como `N/A` e `WAIT DATA`, mantendo os payloads operacionais originais intactos.
