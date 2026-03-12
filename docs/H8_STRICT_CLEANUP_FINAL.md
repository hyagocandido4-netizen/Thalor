# H8 — Strict Cleanup Final

This package closes the remaining strict-cleanup gaps:

- removes banned legacy patch scripts
- normalizes root compatibility shims
- emits `effective_config_control` in `scoped_paths`
- records `lock_mode` on lock-block payloads
- replaces internal imports that still passed through root compatibility modules

Note: ZIP extraction cannot delete already-existing files on the destination machine.
After extracting this package, run `pwsh -ExecutionPolicy Bypass -File .\scripts\tools\h8_apply_cleanup.ps1` once to delete the banned legacy patch files from an existing checkout.
