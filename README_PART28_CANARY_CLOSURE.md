
# PART 28 — Canary Closure Kit (bounded path to Part 30)

Este pacote é a mudança de abordagem.

Em vez de continuar adicionando scanners, ele cria **três comandos operacionais finais** para fechar o canary:
- `portfolio_artifact_repair`
- `portfolio_canary_closure_report`
- `capture_canary_closure_bundle`

## Objetivo
Resolver o que ainda aparece nos bundles do canary sem tocar no Decodo, sem mexer em secrets e sem abrir execução mais agressiva.

## O que ele faz
1. **repair**: detecta scopes com artifact stale / missing / cp_meta_missing e roda `asset prepare` + `asset candidate` de forma sequencial e segura.
2. **closure report**: consolida provider, signal scan e audit full-scope em um parecer único:
   - `provider_unstable`
   - `repair_needed`
   - `actionable_scope_ready`
   - `healthy_waiting_signal`
   - `observe_only_degraded_provider`
3. **bundle**: gera um ZIP único já contendo repair, stability, signal scan, audit e closure report.

## Segurança
- não toca em `./secrets`
- não altera `transport_endpoint`
- não envia ordens
- não mexe no envelope conservador do canary

## Comandos

### 1) Repair seguro de artifacts
```powershell
.\scripts\tools\portfolio_artifact_repair.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
```

### 2) Closure report consolidado
```powershell
.\scripts\tools\portfolio_canary_closure_report.cmd --config config\practice_portfolio_canary.yaml --all-scopes --json
```

### 3) Bundle final do canary
```powershell
.\scripts\tools\capture_canary_closure_bundle.cmd --config config\practice_portfolio_canary.yaml
```

## Stop rule até a Parte 30

### Parte 28
Este pacote. O alvo é remover stale/cp_meta_missing do caminho crítico e produzir um closure report único.

### Parte 29
Só acontece se o closure report ainda sair como `repair_needed` ou `provider_unstable`.
Não entram novas features. Só correção final do blocker real.

### Parte 30
Freeze final:
- runbook
- go/no-go
- remoção de scripts redundantes
- conclusão final do envelope do canary

**Não haverá Parte 31.**
