# Docker (Package U + M6 notes)

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

Com M6, o caminho preferido é usar **external secret file** montado read-only no container, em vez de deixar `IQ_EMAIL` / `IQ_PASSWORD` direto no `.env`.

### Opção recomendada

1. Copie `config/broker_secrets.yaml.example` para um arquivo real ignorado pelo git, por exemplo:

   - `secrets/broker.yaml`

2. Monte esse diretório no container e aponte `THALOR_SECRETS_FILE` para ele.

Exemplo de extensão do serviço runtime:

```yaml
services:
  thalor-runtime:
    environment:
      THALOR_SECRETS_FILE: /app/secrets/broker.yaml
      THALOR__SECURITY__DEPLOYMENT_PROFILE: live
      THALOR__SECURITY__LIVE_REQUIRE_EXTERNAL_CREDENTIALS: "1"
    volumes:
      - ./secrets:/app/secrets:ro
      - ./runs:/app/runs
      - ./data:/app/data
```

### Compatibilidade

Ainda é possível usar `.env` com `IQ_EMAIL` / `IQ_PASSWORD` para testes locais, mas isso deve ficar restrito a laboratório / paper quando possível.

## Observações

- Se você não quiser executar trades de verdade, mantenha:
  - `THALOR__EXECUTION__MODE=paper`
  - `THALOR__EXECUTION__ENABLED=1`
- Em produção, só troque para `live` depois de validar guardrails, `runtime_app security`, `health` e o policy set da conta.


## 3) Dashboard local (M7)

```bash
docker compose --profile dashboard up --build thalor-dashboard
```

O serviço publica o Streamlit em `localhost:8501`.

## 4) Runtime live + dashboard (M7)

Use o compose de produção:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
```

Pré-requisitos recomendados:

- `THALOR_SECRETS_FILE` apontando para um bundle externo
- `THALOR__EXECUTION__MODE=live` somente após `runtime_app release` limpo
- `notifications.telegram.*` configurado se quiser alertas reais
