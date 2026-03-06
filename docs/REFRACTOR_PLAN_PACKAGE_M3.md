# Package M3 – Legacy Config Cutover Bridge

## Scope
- bridge `config2.py` to `natbin.config`
- bridge `settings.py` to `natbin.config`
- provide a compatibility shim for legacy runtime consumers
- add a smoke check for legacy consumer parity

## Done when
- `config2.py` is no longer an independent source of runtime truth
- `settings.py` is no longer an independent source of runtime truth
- `config_consumers_smoke.py` passes
- operational modules can continue importing legacy surfaces while consuming resolved runtime config underneath
