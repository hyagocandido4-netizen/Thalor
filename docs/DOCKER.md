# Docker (Package U)

Este repositório agora vem com `Dockerfile` e `docker-compose.yml` para facilitar:

- **paper/CI mode** (sem broker): roda smoke tests em loop
- **runtime opcional (IQ)**: instala `requirements.txt` e roda `portfolio observe` em loop

## 1) Paper mode (não precisa de IQ_EMAIL)

```bash
docker compose up --build thalor-paper
```

Isso vai rodar em loop:

- `python scripts/ci/smoke_execution_layer.py`
- `python scripts/ci/smoke_runtime_app.py`

E manter o container vivo (útil para validar que o runtime está “ok” sem dependências externas).

Logs / artefatos ficam em `./runs` (montado como volume).

## 2) Runtime com IQ (opcional)

1. Crie `.env` a partir de `.env.example` e preencha as credenciais:

   - `IQ_EMAIL`
   - `IQ_PASSWORD`

   (opcional) ajuste throttling: `IQ_*_RPS`, `IQ_*_BURST`.

2. Suba o serviço com o profile `iq`:

```bash
docker compose --profile iq up --build thalor-runtime
```

- O build usa `INSTALL_IQ=1` (instala `requirements.txt`, que inclui `iqoptionapi`).
- O serviço roda `portfolio observe` com `--once` dentro de um loop `sleep 60`.

## Observações

- Se você não quiser executar trades de verdade, mantenha:
  - `THALOR__EXECUTION__MODE=paper`
  - `THALOR__EXECUTION__ENABLED=1`
- Em produção, você pode trocar para `live` **somente depois** de validar guardrails/failsafes.
