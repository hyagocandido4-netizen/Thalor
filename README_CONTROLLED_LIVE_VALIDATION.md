# Thalor controlled live validation

Use este guia quando a suíte local já passou e você quer validar o runtime em
PRACTICE e depois em REAL de forma controlada.

Leia: `docs/CONTROLLED_LIVE_VALIDATION_M72.md`

Antes do baseline/practice/real, configure `THALOR_SECRETS_FILE` apontando para um arquivo local de secrets.

Antes do stage `practice`, rode também `python -m natbin.runtime_app practice --repo-root . --config config/live_controlled_practice.yaml --json`.


Para a rodada operacional completa em PRACTICE, use o runner novo:

```powershell
python scripts/tools/controlled_practice_round.py --repo-root . --config config/live_controlled_practice.yaml --json
```
