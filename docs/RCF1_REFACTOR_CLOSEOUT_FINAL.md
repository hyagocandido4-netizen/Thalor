# RCF-1 — Refactor Closeout Final

Data: **2026-03-21**

RCF-1 fecha a última milha de refatoração que ainda mantinha o observer em um
estado “quase migrado”, mas com duas fontes de verdade escondidas.

## O que foi fechado

### 1) Observer sem fallback interno para `config.yaml`

`observe_signal_topk_perday.py` e `observe_signal_latest.py` não fazem mais
`Path("config.yaml")` hardcoded nem leem YAML legado por conta própria.

Agora ambos:

- resolvem a config selecionada via `natbin.config.loader.load_resolved_config`
- aceitam `config/base.yaml` como caminho preferido
- continuam aceitando `config.yaml` apenas quando ele é o caminho selecionado
  ou quando `config/base.yaml` não existe

Resultado: o control plane e o observer passaram a compartilhar a mesma lógica
canônica de resolução de config.

### 2) `decision.bounds` virou contrato tipado

Os bounds de regime deixaram de ser um `dict[str, float]` solto e passaram a
ser validados por um modelo próprio:

- `decision.bounds.vol_lo/vol_hi`
- `decision.bounds.bb_lo/bb_hi`
- `decision.bounds.atr_lo/atr_hi`

Isso evita configs “quebradas silenciosamente”, como `*_lo > *_hi`.

### 3) CPREG / CP_ALPHA / SLOT2_MULT formalizados na config tipada

A superfície residual de knobs legados foi colocada no contrato moderno:

- `decision.cp_alpha`
- `decision.cpreg.*`
- `runtime_overrides.cpreg_*`
- `runtime_overrides.cp_alpha`

O bridge `natbin.config.compat_runtime` agora exporta essas decisões para o env
legado do observer somente no momento da execução.

### 4) `runtime_overrides` passou a valer no observer de verdade

Antes do RCF-1, vários overrides tipados existiam no schema, mas não chegavam
até o observer legado.

Agora `prepare_observer_environment()` exporta para o runtime:

- `THRESHOLD`
- `CP_ALPHA`
- `CPREG_ENABLE`
- `CPREG_ALPHA_START`
- `CPREG_ALPHA_END`
- `CPREG_SLOT2_MULT`
- `META_ISO_BLEND`
- `REGIME_MODE`
- `PAYOUT`
- `MARKET_OPEN`

### 5) Inventory freeze para root shims

O pacote também congela o inventário atual de compatibility shims na raiz de
`src/natbin/`, impedindo crescimento acidental do topo do namespace.

## Validação

Rodar no repo:

```bash
pytest -q
python scripts/ci/smoke_execution_layer.py
python scripts/ci/smoke_runtime_app.py
PYTHONPATH=src python scripts/tools/phase1_h11_stack_calibration_smoke.py
PYTHONPATH=src python scripts/tools/phase1_h12_retrain_allocator_smoke.py
PYTHONPATH=src python -m natbin.leak_check
PYTHONPATH=src python scripts/tools/selfcheck_repo.py
```

## Resultado esperado

Depois do RCF-1, ainda existe compatibilidade com `config.yaml`, mas ele deixa
claramente de ser uma fonte paralela implícita para o observer. O caminho
canônico passa a ser: **config selecionada -> loader tipado -> bridge legado -> observer**.
