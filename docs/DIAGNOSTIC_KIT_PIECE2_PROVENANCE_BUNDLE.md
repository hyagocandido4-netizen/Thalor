# Diagnostic Kit – Peça 2: `config-provenance-audit` e `support-bundle`

Esta peça adiciona duas ferramentas canônicas para acelerar diagnóstico operacional e readiness:

- `config-provenance-audit`
- `support-bundle`

## 1. `config-provenance-audit`

Audita a proveniência dos campos críticos do profile ativo e mostra:

- quais arquivos YAML participaram do merge;
- quais env vars modernas e compat foram vistas;
- se existe secret bundle;
- qual fonte venceu para campos críticos (`execution.*`, `broker.*`, `multi_asset.*`, paths de dados);
- se o secret bundle está tentando sobrescrever campos proibidos, como `broker.balance_mode`;
- se a capacidade multi-asset cobre os scopes selecionados;
- como o transporte/proxy foi resolvido e se a dependência de SOCKS está satisfeita.

### Comandos

```bash
python -m natbin.runtime_app config-provenance-audit \
  --repo-root . \
  --config config/live_controlled_real.yaml \
  --json
```

```bash
python -m natbin.runtime_app config-provenance-audit \
  --repo-root . \
  --config config/multi_asset.yaml \
  --all-scopes \
  --json
```

### Artifacts emitidos

- `runs/control/_repo/config_provenance.json`
- `runs/control/<scope>/config_provenance.json`

## 2. `support-bundle`

Gera um ZIP sanitizado e curto, pronto para análise externa. O bundle inclui:

- `config_provenance_audit.json`
- `security.json`
- `provider_probe.json`
- `production_gate.json`
- `doctor/<scope>.json`
- `release.json`
- metadata de Python, git e env seguro
- cópias sanitizadas do config selecionado, secret bundle e `transport_endpoint`
- artifacts de controle do repo e dos scopes selecionados
- logs estruturados selecionados, quando `--include-logs` é usado

### Comandos

Bundle curto e passivo:

```bash
python -m natbin.runtime_app support-bundle \
  --repo-root . \
  --config config/live_controlled_real.yaml \
  --json
```

Bundle multi-asset com probe ativo do provider:

```bash
python -m natbin.runtime_app support-bundle \
  --repo-root . \
  --config config/multi_asset.yaml \
  --all-scopes \
  --probe-provider \
  --include-logs \
  --json
```

### Opções principais

- `--all-scopes`: coleta todos os scopes do profile.
- `--probe-provider`: faz probe remoto no provider dentro do bundle.
- `--include-logs`: inclui logs estruturados selecionados.
- `--max-log-bytes`: limita o tamanho de cada log copiado.
- `--output-dir`: muda a pasta de saída do ZIP.
- `--bundle-prefix`: muda o prefixo do arquivo ZIP.

### Saída padrão

Por padrão, o ZIP sai em:

```text
diag_zips/support_bundle_<timestamp>.zip
```

### Artifacts emitidos

- `runs/control/_repo/support_bundle.json`

## Uso recomendado para o objetivo de operar os 6 assets

Antes de partir para observe/execute multi-asset real:

1. rode `config-provenance-audit --all-scopes`;
2. confirme que `multi_asset_capacity` está `ok`;
3. confirme que `broker.balance_mode` não está sendo controlado pelo secret bundle;
4. rode `provider-probe --all-scopes`;
5. rode `production-gate --all-scopes --probe-provider`;
6. gere um `support-bundle --all-scopes --probe-provider` para congelar a evidência operacional.
