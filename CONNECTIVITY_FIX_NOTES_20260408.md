# Thalor — connectivity hardening overlay (2026-04-08)

Este pacote foi preparado para corrigir as causas **internas ao código/configuração** que estavam contribuindo para a instabilidade de conexão observada no Thalor.

## O que foi corrigido

1. **Healthcheck do transport agora é auth-aware / tunnel-aware**
   - Antes o modo `tcp` só validava abertura de porta no proxy.
   - Agora os profiles relevantes usam `healthcheck_mode: http` com `healthcheck_url: https://iqoption.com/api/appinit`.
   - Para proxies HTTP, o healthcheck faz um CONNECT real e trata `407` como `unhealthy`.
   - Para proxies SOCKS, o healthcheck testa conexão real através do proxy até um alvo TLS, em vez de apenas abrir TCP no host do proxy.

2. **Fail-open desativado para o fluxo crítico**
   - `fail_open_when_exhausted` foi desativado nos profiles principais.
   - `IQClient` também passou a selecionar binding com `allow_fail_open=False` no `iqoption_connect` e nas chamadas retryadas.
   - Isso evita insistir em endpoint já quarentenado e reduz thrash operacional.

3. **Timeout real do connect propagado para o login da IQ**
   - `IQConfig` agora carrega `connect_timeout_s`.
   - `IQClient.connect()` aplica timeout real por tentativa via thread join + `TimeoutError`.
   - O budget respeita a precedência: `connect_timeout_s` explícito > config do broker > `IQ_CONNECT_TIMEOUT_S` > timeout do endpoint.

4. **Provider probe deixou de usar orçamento exagerado de retry**
   - O `provider-probe` agora usa um orçamento reduzido e controlado para connect.
   - Isso torna o diagnóstico mais rápido e evita probes de ~200s quando o provider está indisponível.
   - Variáveis de override:
     - `THALOR_PROVIDER_PROBE_CONNECT_RETRIES`
     - `THALOR_PROVIDER_PROBE_CONNECT_SLEEP_S`
     - `THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S`

5. **Bridge explícita de proxy para HTTP + WebSocket do IQOption**
   - O adapter voltou a usar o bridge endurecido que injeta o binding do transport tanto no requests/session quanto no `run_forever(**websocket_options)`.
   - Isso reduz divergência entre healthcheck, login e websocket.

6. **Request metrics / connectivity plumbing reforçados**
   - O `broker_surface` passa a injetar também `request_metrics_config` no adapter.
   - O stack de runtime connectivity já fica coerente com transport + request metrics.

7. **Compatibilidade de teste restaurada para precheck com drain mode**
   - `run_precheck(..., allow_drain_mode=...)` e `RuntimeFailsafe.precheck(..., allow_drain_mode=...)` foram harmonizados.
   - Isso não é a causa principal do problema de rede, mas foi corrigido para manter a suíte consistente.

## Arquivos alterados

- `src/natbin/adapters/iq_client.py`
- `src/natbin/brokers/iqoption.py`
- `src/natbin/runtime/connectivity.py`
- `src/natbin/runtime/broker_surface.py`
- `src/natbin/runtime/failsafe.py`
- `src/natbin/runtime/precheck.py`
- `src/natbin/utils/network_transport.py`
- `src/natbin/ops/provider_probe.py`
- `config/base.yaml`
- `config/practice_portfolio_canary.yaml`
- `config/live_controlled_practice.yaml`
- `config/live_controlled_practice.yaml.example`
- `config/live_controlled_real.yaml`
- `config/live_controlled_real.yaml.example`
- `config/multi_asset.yaml`
- `tests/test_network_transport.py`
- `tests/test_iq_client_network_transport.py`

## O que este pacote NÃO faz

- Não altera `./secrets`
- Não altera `secrets/transport_endpoint`
- Não altera Decodo
- Não garante disponibilidade externa do proxy / rota / IQ Option

## Validação executada

- Suíte conectividade/proxy/provider: **64 passed**
