# Package T — Hardening Operacional e Segurança

Este pacote adiciona um **throttle cross-process** no `IQClient` para reduzir bursts de chamadas quando o pipeline roda **múltiplos subprocessos em paralelo** (ex.: `multi_asset.max_parallel_assets > 1`).

A ideia é simples:

- cada processo “agenda” o início da próxima chamada usando um **arquivo de estado compartilhado**;
- os inícios das chamadas ficam espaçados por um intervalo mínimo;
- opcionalmente adiciona-se um jitter para evitar alinhamento perfeito.

> Importante: isto é **para estabilidade** (suavizar carga / reduzir risco de rate-limit), **não** para evasão / bypass.

## Variáveis de ambiente (IQClient)

### Throttle / pacing (novas)

- `IQ_THROTTLE_MIN_INTERVAL_S` *(float; default `0.0`)*  
  Intervalo mínimo entre **inícios** de chamadas à API do IQOption (entre processos).  
  Se `0.0`, o throttle fica desabilitado.

- `IQ_THROTTLE_JITTER_S` *(float; default `0.0`)*  
  Jitter aleatório adicional `0..jitter` aplicado ao agendamento.

- `IQ_THROTTLE_STATE_FILE` *(path; default `runs/iq_throttle_state.json`)*  
  Arquivo de estado compartilhado (o `.lock` é criado ao lado).

### Recomendação de valores

Para começar (ajuste conforme estabilidade/latência):

- `IQ_THROTTLE_MIN_INTERVAL_S=0.25`
- `IQ_THROTTLE_JITTER_S=0.05`

Se você usa `multi_asset.stagger_sec`, normalmente você pode manter o throttle mais baixo.

## Credenciais

### Boas práticas

- mantenha credenciais fora do git (use `.env` local);
- nunca commite `.env`;
- prefira placeholders versionados via `.env.example`.

### Exemplo (`.env.example`)

Veja o arquivo `.env.example` na raiz do repo.

## Notas de implementação

- O throttle é **best-effort**: se houver falha ao ler/escrever o estado/lock, a chamada segue normalmente (sem quebrar o pipeline).
- Lock cross-platform:
  - Windows: `msvcrt.locking`
  - Linux/macOS: `fcntl.flock`
