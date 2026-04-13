## Package M6 (série M) — Security & Secrets Hardening

Entrega:
- external secret files (`THALOR_SECRETS_FILE`, `THALOR_BROKER_*_FILE`)
- redaction de effective config / control artifacts / execution event log
- auditoria de postura com artefato `security.json`
- comando `runtime_app security`
- broker guard live com spacing, rate limit e time filter
- knobs formais de throttling do adapter IQ

Arquivos principais:
- `src/natbin/security/redaction.py`
- `src/natbin/security/secrets.py`
- `src/natbin/security/broker_guard.py`
- `src/natbin/security/audit.py`
- `docs/SECURITY_HARDENING_M6.md`
