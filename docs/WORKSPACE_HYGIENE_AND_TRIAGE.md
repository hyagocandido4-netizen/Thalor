# Workspace hygiene e triage operacional

## Objetivo

Fechar o ruído operacional que atrapalha diagnóstico sem tocar na lógica live do broker:

- ignorar artefatos gerados de `test_battery/`, `diag_zips/`, caches e relatórios locais;
- oferecer limpeza segura e reproduzível do workspace;
- materializar um resumo curto da causa primária (`triage`) para anexar aos bundles e incidentes.

## Comandos novos

### Preview da limpeza segura

```bash
python -m natbin.runtime_app workspace-hygiene --repo-root . --json
```

### Aplicar limpeza segura

```bash
python -m natbin.runtime_app workspace-hygiene --repo-root . --apply --json
```

Esse comando remove apenas ruído gerado de testes/diagnósticos/caches, como:

- `test_battery/`
- `diag_zips/`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `coverage.xml`
- `.coverage`
- `pytest*.xml`
- `junit*.xml`
- `src/*.egg-info/`

Ele **não** apaga `runs/`, `data/`, `secrets/` ou `.env`.

### Resumo curto da causa primária

```bash
python -m natbin.runtime_app triage --repo-root . --config config/base.yaml --json
```

O payload consolida:

- `primary_cause`
- `current_symptom`
- `connectivity`
- checks bloqueantes do `doctor`
- `recommended_actions`

Artifact emitido:

```text
runs/control/<scope_tag>/triage.json
```

Artifact repo-level emitido pelo hygiene:

```text
runs/control/_repo/workspace_hygiene.json
```

## Uso recomendado após uma bateria de testes

```bash
python -m natbin.runtime_app workspace-hygiene --repo-root . --json
python -m natbin.runtime_app triage --repo-root . --config config/base.yaml --json
python -m natbin.runtime_app sync --repo-root . --json
```

Se o preview do hygiene estiver correto:

```bash
python -m natbin.runtime_app workspace-hygiene --repo-root . --apply --json
python -m natbin.runtime_app sync --repo-root . --json
```

## Integração com os runners de diagnóstico

Os runners `run_thalor_diagnostics_v4.ps1` e `run_thalor_diagnostics_v4_1.ps1` passaram a incluir:

- `workspace-hygiene` na fase offline;
- `triage` na fase offline;
- `triage` na fase de practice;
- `triage` na fase de real preflight.

Com isso, os bundles ficam melhores para distinguir:

- causa primária do breaker;
- sintoma atual;
- checks realmente bloqueantes do doctor;
- ruído local gerado pelo próprio processo de teste.
