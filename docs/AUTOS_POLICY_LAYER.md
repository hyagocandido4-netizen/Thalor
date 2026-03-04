# Camada de políticas dos autos

O **Pacote F** da refatoração extrai a lógica dos controladores automáticos
(`auto_volume`, `auto_isoblend`, `auto_hourthr`) para uma camada própria em
`src/natbin/autos/`.

## Objetivo

Separar:

- *loader/scan* de summaries (`summary_loader.py`)
- helpers comuns de parsing/contexto (`common.py`)
- políticas puras dos autos:
  - `volume_policy.py`
  - `isoblend_policy.py`
  - `hour_policy.py`

Os módulos CLI originais permanecem como wrappers finos para manter
compatibilidade operacional e contratos de execução já usados pelo scheduler.

## Benefícios

- menor duplicação entre os três autos
- mais testabilidade offline
- menor risco de drift silencioso entre políticas
- caminho preparado para futuras políticas compostas / engine única dos autos

## Smoke

```powershell
python scripts/tools/autos_refactor_smoke.py
```

O smoke garante que:

- o loader encontra o summary do dia atual
- `auto_volume` monta payload válido sem fail-closed quando há summaries corretos
- `auto_isoblend` e `auto_hourthr` usam a camada refatorada sem quebrar a interface
