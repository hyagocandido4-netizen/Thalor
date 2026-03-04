# Runtime daemon (Package J)

O módulo `src/natbin/runtime_daemon.py` introduz uma fundação Python-nativa para
rodar o ciclo do Thalor em loop, sem depender diretamente do agendador
PowerShell como orquestrador principal.

## Estado atual

Ele é **aditivo**:
- o `observe_loop_auto.ps1` continua sendo o entrypoint operacional principal
- o daemon Python existe para permitir a futura redução da camada shell

## CLI

Planejamento apenas:

```bash
python -m natbin.runtime_daemon --plan-json
```

Um ciclo só:

```bash
python -m natbin.runtime_daemon --once --topk 3
```

Loop contínuo:

```bash
python -m natbin.runtime_daemon --topk 3
```

Wrapper PowerShell:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto_py.ps1 -TopK 3
```

## Responsabilidades

- montar o plano canônico via `runtime_cycle`
- executar o plano em loop
- alinhar o sleep ao próximo candle
- aplicar lock escopado por `asset + interval_sec`

## Não-responsabilidades (por enquanto)

- não substitui o scheduler PowerShell atual
- não reimplementa quota/pacing em Python ainda
- não toma posse do status/sidecars atuais
