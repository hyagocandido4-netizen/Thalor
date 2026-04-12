# PHASE3 — Fix robusto do `collect_recent` + `IQClient`

## Problema

No ciclo multi-asset, parte dos ativos OTC falhava na coleta com dois sintomas:

- `Asset XXX not found on consts`
- `'IQ_Option' object has no attribute 'api'`

Os ativos mais afetados eram aqueles que dependem de refresh dinâmico do mapa de
ativos do broker ou de um alias diferente do nome configurado no profile.

## Causa raiz

1. O `iqoptionapi` depende do mapa interno `ACTIVES` para `get_candles()`.
   Alguns ativos não estavam presentes nesse mapa logo no primeiro uso.
2. Depois de recriar o client (`_new_api()`), o objeto novo podia ficar sem
   atributo `api`, enquanto o flag global de websocket ainda indicava conexão.
   Isso fazia o `check_connect()` retornar um estado enganoso.
3. Em alguns casos o nome configurado no Thalor e o nome exposto pelo broker não
   eram idênticos, embora representassem o mesmo ativo.

## Solução implementada

### 1) Refresh dinâmico do catálogo de ativos

O `IQClient` agora faz refresh do catálogo via APIs já expostas pela lib:

- `update_ACTIVES_OPCODE()`
- `get_all_ACTIVES_OPCODE()`
- `get_all_open_time()`

Isso é usado apenas como fallback robusto quando o asset não está presente no
mapa já carregado.

### 2) Resolução genérica de aliases

Foi adicionada uma resolução por normalização de nome:

- remove separadores
- compara variantes OTC / não-OTC
- compara variantes com sufixo final genérico
- escolhe o melhor match disponível no catálogo do broker

Isso evita hardcodes por asset e permite operar com aliases como:

- `BTCUSD-OTC` → `BTCUSD-L`
- `XAUUSD-OTC` → `XAUUSD`

quando o broker expõe o ativo dessa forma.

### 3) Reconnect real quando o client novo ainda não tem `api`

`ensure_connection()` agora exige simultaneamente:

- `check_connect() == true`
- `self.iq.api` existir de fato

Se o objeto foi recriado e ainda não abriu a sessão corretamente, o reconnect é
forçado antes do próximo call.

### 4) Logging mais claro no `collect_recent`

O fluxo agora registra explicitamente:

- `requested_asset`
- `broker_asset`

Assim fica claro quando houve resolução dinâmica do nome remoto.

## Smoke recomendado

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts/tools/phase3_collect_recent_iq_fix_smoke.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```

## Testes recomendados

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m pytest -q tests/test_phase3_collect_recent_iq_fix.py
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```
