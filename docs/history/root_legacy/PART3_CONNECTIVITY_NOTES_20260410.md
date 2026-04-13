# Thalor — Parte 3: conectividade, pacing e start gate

Esta parte é **cumulativa** sobre as Partes 1 e 2.

## O que foi atacado

1. **Healthcheck de proxy mais realista**
   - `network.transport.healthcheck_mode: http` agora valida túnel real até alvo HTTPS.
   - Para proxy HTTP/HTTPS, o probe usa `CONNECT target:443`.
   - Para proxy SOCKS/SOCKS5H, o probe valida `connect()` através do proxy até o alvo remoto.
   - `407 Proxy Authentication Required` passa a ser tratado como endpoint **unhealthy**, não como “quase saudável”.

2. **Fail-open desativado para soak/canary**
   - Profiles principais passam a usar `fail_open_when_exhausted: false`.
   - `IQClient.connect()` e chamadas retryadas deixam de aceitar fail-open quando o transport está esgotado.
   - Isso evita cair em caminho indireto/poluído quando o endpoint está realmente ruim.

3. **Timeout real no login da IQ**
   - `IQConfig` ganha `connect_timeout_s`.
   - `IQClient.connect()` agora aplica timeout por tentativa no login real da IQ.
   - `IQOptionAdapter` propaga `broker.timeout_connect_s` para o cliente.

4. **Provider probe com orçamento menor**
   - `provider-probe` deixa de usar o mesmo budget pesado do reconnect normal.
   - Por padrão, ele passa a capar para `3` tentativas e `1.0s` de sleep entre tentativas.
   - Overrides disponíveis:
     - `THALOR_PROVIDER_PROBE_CONNECT_RETRIES`
     - `THALOR_PROVIDER_PROBE_CONNECT_SLEEP_S`
     - `THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S`

5. **Start gate operacional**
   - Novo tool: `scripts/tools/provider_start_gate.py`
   - Wrapper: `scripts/tools/provider_start_gate.cmd`
   - Objetivo: exigir N `provider-probe` frescos consecutivos antes de subir soak longo.

6. **Plumbing de request metrics no bridge live**
   - `IQOptionAdapter` agora constrói `RequestMetrics` e `NetworkTransportManager` próprios quando cria `IQClient`.
   - Isso melhora a coleta em caminhos de execução/reconcile também.

7. **Defaults de log path reforçados**
   - Se transport/request metrics estiverem habilitados sem path explícito, o runtime força:
     - `runs/logs/network_transport.jsonl`
     - `runs/logs/request_metrics.jsonl`

## Mudanças de perfil

Profiles principais foram alinhados para:
- `healthcheck_mode: http`
- `healthcheck_url: https://iqoption.com/api/appinit`
- `fail_open_when_exhausted: false`

## Risco

- **Baixo a médio** no caminho crítico, porque muda a semântica de “proxy saudável” e torna a seleção de endpoint mais conservadora.
- O efeito esperado é **menos thrash**, **menos falso verde** e **menos insistência em endpoint ruim**.

## Benefício esperado

- Menos discrepância entre “proxy parece de pé” e “provider-probe falha”.
- Menos probes de 200s quando o provider está ruim.
- Menos ruído de fail-open em soak longo.
- Melhor leitura operacional do estado real da sessão.
