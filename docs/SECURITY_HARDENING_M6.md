# Security & Secrets Hardening (M6)

O M6 fecha a base mínima de segurança operacional para o runtime novo sem
quebrar compatibilidade com o fluxo local já existente.

## O que entrou

### 1. External secret files

O loader tipado agora suporta sobrepor credenciais do broker por arquivos
externos, sem depender de credenciais embutidas no YAML canônico.

Fontes suportadas:

- `THALOR_SECRETS_FILE` → bundle YAML / JSON / KEY=VALUE
- `THALOR_BROKER_EMAIL_FILE`
- `THALOR_BROKER_PASSWORD_FILE`

Precedência dentro dessa fase:

- arquivos separados de email/password
- bundle de secrets
- valores já resolvidos pelo merge normal de config/env

Exemplo de bundle:

```yaml
broker:
  email: you@example.com
  password: YOUR_PASSWORD_HERE
  balance_mode: PRACTICE
```

Arquivo-modelo: `config/broker_secrets.yaml.example`.

## 2. Redaction de artefatos

O runtime agora redige valores sensíveis antes de persistir:

- `runs/config/effective_config_latest_<scope>.json`
- `runs/control/<scope>/effective_config.json`
- `runs/logs/execution_events.jsonl`

Marcadores usados:

- `***REDACTED***`
- `***REDACTED_EMAIL***`

Isso evita vazar `broker.password`, `token`, `authorization`, `session`,
`cookie` e emails de credenciais em dumps compartilháveis.

## 3. Security audit / control artifact

O `build_context()` agora executa uma auditoria best-effort e grava:

- `runs/control/<scope>/security.json`

Cheque atuais:

- `.env` protegido por `.gitignore`
- credenciais embutidas no YAML canônico
- presença/ausência de credenciais do broker
- política de live exigir credenciais externas
- verificação de que effective config / control artifact / structured log não vazaram segredo
- estado atual do broker guard

CLI novo:

```powershell
python -m natbin.runtime_app security --repo-root . --json
```

O comando também aparece no dashboard local como painel **Security (M6)**.

## 4. Broker guard (spacing / rate limit / time filter)

Antes de um submit live, o runtime avalia regras locais de proteção:

- espaçamento mínimo entre submits
- máximo de submits por minuto
- janela local permitida (`allowed_start_local` / `allowed_end_local`)
- weekdays bloqueados

Estado persistido em:

- `runs/security/broker_guard_state.json`

Razões possíveis de bloqueio:

- `security_time_filter_closed`
- `security_submit_spacing`
- `security_submit_rate_limit`

Config baseline:

```yaml
security:
  deployment_profile: live
  live_require_external_credentials: true
  guard:
    enabled: true
    live_only: true
    min_submit_spacing_sec: 10
    max_submit_per_minute: 4
    time_filter_enable: true
    allowed_start_local: "09:00"
    allowed_end_local: "17:00"
    blocked_weekdays_local: []
```

## 5. IQ adapter throttling knobs

O adapter IQ passa a receber, via config tipada, knobs formais de pacing:

```yaml
broker:
  api_throttle_min_interval_s: 0.10
  api_throttle_jitter_s: 0.05
```

Eles são repassados ao client por env compatível somente durante a janela de
conexão/chamada do adapter, evitando depender exclusivamente de env vars soltas.

## Operação recomendada

### Desenvolvimento local

- mantenha `deployment_profile: local`
- use `config/broker_secrets.yaml` ou `secrets/broker.yaml` ignorado pelo git
- mantenha `execution.enabled: false` ou `mode: paper` até validar os guardrails

### Live controlado

- `security.deployment_profile: live`
- `security.live_require_external_credentials: true`
- `security.guard.enabled: true`
- `security.guard.time_filter_enable: true`
- throttling do broker configurado
- acompanhar `runtime_app security` e `health` a cada mudança de credencial/config

## Testes

- `tests/test_security_redaction.py`
- `tests/test_security_loader.py`
- `tests/test_security_audit.py`
- `tests/test_broker_guard.py`
- `scripts/tools/security_hardening_smoke.py`
