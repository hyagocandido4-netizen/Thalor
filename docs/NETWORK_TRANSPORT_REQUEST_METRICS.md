# Network transport resiliente + RequestMetrics

Este pacote adiciona duas camadas operacionais novas ao Thalor:

- `NetworkTransportManager`: middleware opcional de conectividade para as chamadas externas do broker/provedor.
- `RequestMetrics`: contador diário thread-safe para volume/latência/sucesso de chamadas externas.

A integração final cobre quatro superfícies:

- configuração tipada em `config/base.yaml`
- override por ambiente (`THALOR__*`, `TRANSPORT_*`, `REQUEST_METRICS_*`)
- injeção de dependência no runtime / broker adapter
- observabilidade por artifact e JSONL

## Arquivos principais

- `src/natbin/utils/network_transport.py`
- `src/natbin/utils/request_metrics.py`
- `src/natbin/runtime/connectivity.py`
- `src/natbin/adapters/iq_client.py`
- `src/natbin/brokers/iqoption.py`
- `runs/control/<scope_tag>/connectivity.json`
- `runs/logs/network_transport.jsonl`
- `runs/logs/request_metrics.jsonl`

## Dependência obrigatória para SOCKS

Quando `network.transport` estiver habilitado com endpoint `socks`, `socks4`, `socks5` ou `socks5h`, o ambiente precisa ter `PySocks` instalado.

O Thalor agora faz preflight explícito dessa dependência em duas camadas:

- `runtime_app doctor` / broker preflight
- `IQClient.connect()` antes de tentar abrir a conexão

Se `PySocks` estiver ausente, a falha passa a ser rápida e clara, com mensagem equivalente a `PySocks is required for SOCKS transport endpoints`.

## Como a ativação funciona

A ordem prática de precedência ficou assim:

1. config YAML (`config/base.yaml` e overrides)
2. variáveis de ambiente `THALOR__*`
3. aliases operacionais `TRANSPORT_*` e `REQUEST_METRICS_*`
4. arquivos secretos apontados por `*_FILE`

Para ambientes reais, a recomendação é:

- habilitar a feature só quando houver necessidade operacional
- usar `TRANSPORT_ENDPOINT_FILE` / `TRANSPORT_ENDPOINTS_FILE`
- não deixar credenciais inline em compose, YAML ou `.env` versionado

## Snippet canônico de config

```yaml
network:
  transport:
    enabled: true
    endpoint_file: secrets/transport_endpoint
    max_retries: 3
    backoff_base_s: 0.5
    backoff_max_s: 8.0
    jitter_ratio: 0.2
    failure_threshold: 3
    quarantine_base_s: 30.0
    quarantine_max_s: 300.0
    healthcheck_interval_s: 60.0
    healthcheck_timeout_s: 3.0
    healthcheck_mode: tcp
    fail_open_when_exhausted: true
    structured_log_path: runs/logs/network_transport.jsonl

observability:
  request_metrics:
    enabled: true
    timezone: America/Sao_Paulo
    structured_log_path: runs/logs/request_metrics.jsonl
    summary_log_level: INFO
    emit_summary_on_rollover: true
    emit_summary_on_close: true
```

## Ativação local

### Opção A — laboratório com endpoint inline

Útil para validar a integração rapidamente em workstation local.

### PowerShell

```powershell
$env:THALOR__NETWORK__TRANSPORT__ENABLED = '1'
$env:TRANSPORT_ENDPOINT = 'socks5://user:pass@127.0.0.1:1080?name=local-socks'
$env:THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED = '1'
$env:REQUEST_METRICS_LOG_PATH = 'runs/logs/request_metrics.local.jsonl'
$env:REQUEST_METRICS_TIMEZONE = 'America/Sao_Paulo'
```

### Bash

```bash
export THALOR__NETWORK__TRANSPORT__ENABLED=1
export TRANSPORT_ENDPOINT='socks5://user:pass@127.0.0.1:1080?name=local-socks'
export THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED=1
export REQUEST_METRICS_LOG_PATH='runs/logs/request_metrics.local.jsonl'
export REQUEST_METRICS_TIMEZONE='America/Sao_Paulo'
```

### Opção B — preferida, usando arquivo secreto

### PowerShell

```powershell
New-Item -ItemType Directory -Force .\secrets | Out-Null
Set-Content -Path .\secrets\transport_endpoint -Value 'socks5://user:pass@127.0.0.1:1080?name=local-socks'
$env:THALOR__NETWORK__TRANSPORT__ENABLED = '1'
$env:TRANSPORT_ENDPOINT_FILE = (Resolve-Path .\secrets\transport_endpoint)
$env:THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED = '1'
```

### Bash

```bash
mkdir -p ./secrets
printf '%s\n' 'socks5://user:pass@127.0.0.1:1080?name=local-socks' > ./secrets/transport_endpoint
export THALOR__NETWORK__TRANSPORT__ENABLED=1
export TRANSPORT_ENDPOINT_FILE="$(pwd)/secrets/transport_endpoint"
export THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED=1
```

### Validar a configuração antes de subir o loop

```bash
python - <<'PY'
from natbin.utils import NetworkTransportManager, RequestMetrics
print(NetworkTransportManager.from_env().snapshot())
print(RequestMetrics.from_env().snapshot())
PY
```

### Rodar um ciclo único do daemon

```bash
python -m natbin.runtime_daemon --repo-root . --config config/base.yaml --once
```

### Ler o estado de conectividade resolvido

```bash
python -m natbin.runtime_app status --repo-root . --config config/base.yaml --json
```

No payload acima, a área relevante fica em:

- `control.connectivity`
- `control.connectivity_source`

Quando o daemon ainda não escreveu artifact, `connectivity_source` aparece como `computed`.
Depois que o runtime rodar, o esperado é `artifact`.

## Ativação em VPS

No VPS, a recomendação é montar o segredo em `/app/secrets` via compose.

### 1) Criar o endpoint secreto no host

```bash
mkdir -p ./secrets
printf '%s\n' 'socks5://user:pass@proxy.vps.internal:1080?name=vps-primary' > ./secrets/transport_endpoint
chmod 600 ./secrets/transport_endpoint
```

### 2) Ajustar `.env`

```dotenv
THALOR__NETWORK__TRANSPORT__ENABLED=1
TRANSPORT_ENDPOINT_FILE=/app/secrets/transport_endpoint
TRANSPORT_LOG_PATH=/app/runs/network_transport.jsonl
THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED=1
REQUEST_METRICS_LOG_PATH=/app/runs/request_metrics.jsonl
REQUEST_METRICS_TIMEZONE=America/Sao_Paulo
```

### 3) Subir o runtime com overlay VPS

```bash
docker compose -f docker-compose.vps.yml up -d --build thalor-runtime thalor-backup
```

### 4) Verificar logs e artifact

```bash
docker compose -f docker-compose.vps.yml logs -f thalor-runtime
cat runs/control/<scope_tag>/connectivity.json
```

## Ativação em produção

Para produção, o ideal é usar um arquivo com múltiplos endpoints para failover.

### 1) Criar o arquivo com endpoints

```bash
mkdir -p ./secrets
cat > ./secrets/transport_endpoints <<'TXT'
socks5://user:pass@proxy-prod-1.internal:1080?name=prod-primary&priority=10
https://user:pass@proxy-prod-2.internal:8443?name=prod-fallback&priority=20
TXT
chmod 600 ./secrets/transport_endpoints
```

### 2) Ajustar `.env`

```dotenv
THALOR__NETWORK__TRANSPORT__ENABLED=1
TRANSPORT_ENDPOINTS_FILE=/app/secrets/transport_endpoints
TRANSPORT_NO_PROXY=localhost,127.0.0.1,127.0.0.11,::1,thalor-runtime,thalor-backup,thalor-dashboard
TRANSPORT_MAX_RETRIES=5
TRANSPORT_FAILURE_THRESHOLD=2
TRANSPORT_HEALTHCHECK_INTERVAL_S=30
TRANSPORT_HEALTHCHECK_TIMEOUT_S=3
TRANSPORT_HEALTHCHECK_MODE=tcp
TRANSPORT_LOG_PATH=/app/runs/network_transport.jsonl
THALOR__OBSERVABILITY__REQUEST_METRICS__ENABLED=1
REQUEST_METRICS_LOG_PATH=/app/runs/request_metrics.jsonl
REQUEST_METRICS_TIMEZONE=America/Sao_Paulo
```

### 3) Subir o stack de produção

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

### 4) Monitorar o boot

```bash
docker compose -f docker-compose.prod.yml logs -f thalor-runtime
```

Antes de qualquer modo REAL, mantenha o checklist de segurança / release do projeto verde.

## Como testar a integração

## Testes unitários e de integração local

```bash
pytest -q \
  tests/test_network_transport.py \
  tests/test_iq_client_network_transport.py \
  tests/test_request_metrics.py \
  tests/test_runtime_connectivity_registration.py
```

## Smoke do ciclo único

```bash
python -m natbin.runtime_daemon --repo-root . --config config/base.yaml --once
python -m natbin.runtime_app status --repo-root . --config config/base.yaml --json
python scripts/tools/runtime_health_report.py
```

## Smoke em Docker

### Local

```bash
docker compose up --build thalor-runtime
```

### VPS

```bash
docker compose -f docker-compose.vps.yml up -d --build thalor-runtime
```

### Produção

```bash
docker compose -f docker-compose.prod.yml up -d --build thalor-runtime
```

## Teste manual de failover

Quando existir mais de um endpoint válido, o teste prático é:

1. subir com `TRANSPORT_ENDPOINTS_FILE`
2. derrubar o endpoint primário fora do Thalor
3. observar no `runs/logs/network_transport.jsonl` eventos de falha / quarantine / nova seleção
4. confirmar no `runs/control/<scope_tag>/connectivity.json` que o snapshot continua `transport_ready=true`

Eventos típicos esperados no JSONL:

- `network_transport_binding_selected`
- `network_transport_failure`
- `network_transport_retry_scheduled`
- `network_transport_healthcheck_result`
- `network_transport_success`

## Como monitorar o comportamento

## 1) Artifact de conectividade

Arquivo:

- `runs/control/<scope_tag>/connectivity.json`

Esse artifact resume:

- se a camada está habilitada
- se há endpoints prontos
- endpoints conhecidos e estado de saúde
- config efetiva do `RequestMetrics`

## 2) Status do control plane

```bash
python -m natbin.runtime_app status --repo-root . --config config/base.yaml --json
```

Campos operacionais mais úteis:

- `control.connectivity_source`
- `control.connectivity.transport_enabled`
- `control.connectivity.transport_ready`
- `control.connectivity.transport.endpoints[]`
- `control.connectivity.request_metrics.enabled`

## 3) Relatório local consolidado

```bash
python scripts/tools/runtime_health_report.py
```

O relatório agora também expõe:

- `connectivity_path`
- `connectivity_source`
- `connectivity`

## 4) JSONL do transporte

Arquivo padrão:

- `runs/logs/network_transport.jsonl`

Exemplos úteis:

```bash
tail -f runs/logs/network_transport.jsonl
```

```bash
grep -E 'network_transport_(failure|success|healthcheck|retry)' runs/logs/network_transport.jsonl | tail -n 50
```

## 5) JSONL de métricas diárias

Arquivo padrão:

- `runs/logs/request_metrics.jsonl`

Exemplos úteis:

```bash
tail -f runs/logs/request_metrics.jsonl
```

Procure especialmente por `request_metrics_summary`, que consolida o dia atual ou o dia encerrado.

## 6) Logs do container

```bash
docker compose logs -f thalor-runtime
```

Em overlays específicos:

```bash
docker compose -f docker-compose.vps.yml logs -f thalor-runtime
```

```bash
docker compose -f docker-compose.prod.yml logs -f thalor-runtime
```

## Checklist operacional rápido

- `transport_enabled=true` apenas quando houver endpoint configurado de propósito
- preferir `*_FILE` para segredo de proxy/transporte
- `request_metrics.enabled=true` em VPS/prod para ter trilha diária de volume externo
- validar `connectivity_source=artifact` após o primeiro ciclo do daemon
- confirmar criação de `runs/logs/network_transport.jsonl`
- confirmar criação de `runs/logs/request_metrics.jsonl` quando houver chamadas externas
- acompanhar `runtime_connectivity_registered` no structured log do runtime

## Referências rápidas

- variáveis de ambiente: `docs/ENV_VARS.md`
- Docker / Compose: `docs/DOCKER.md`
- observabilidade: `docs/OBSERVABILITY.md`


## Validar o contrato efetivo dentro do container

Depois do boot, valide o que o runtime realmente resolveu, em vez de confiar apenas no YAML renderizado:

### VPS

```bash
docker compose -f docker-compose.vps.yml run --rm thalor-runtime bash /app/scripts/docker/contract_check.sh --strict
```

### Produção

```bash
docker compose -f docker-compose.prod.yml run --rm thalor-runtime bash /app/scripts/docker/contract_check.sh --strict
```

O comando acima falha com exit code diferente de zero quando houver mismatch entre flags pedidas e flags efetivas, ou quando o transporte estiver habilitado sem endpoint utilizável.
