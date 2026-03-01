# Configs

Conventions used in this repo:

- `config.yaml` (repo root) is the *main* config used by `observe_loop` and most CLI runs.
- Preset configs that are meant to be versioned live under `configs/`.
  Example: `configs/wr_meta_hgb_3x20.yaml`.

Local experimentation snapshots should NOT live in the repo root.

## Local-only variants

If you generate local variants (e.g. `config_META.yaml`, `config_k2.yaml`), place them in:

- `configs/variants/`

By default, `configs/variants/` is gitignored so you don't accidentally commit local configs.

If you decide a variant config is useful long-term:

- Move it to `configs/` (or remove the ignore rule for that file).

## Sharing a sanitized zip

Use:

- `scripts/tools/export_repo_sanitized.ps1`

