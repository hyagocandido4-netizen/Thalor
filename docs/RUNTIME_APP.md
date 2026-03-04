# Runtime App Shell (Package L)

Package L adds a lightweight **Python app-shell description** for Thalor.

It does **not** replace the live runtime loop. Instead, it provides a stable place to answer:

- Which `asset` / `interval_sec` / `timezone` is the runtime using?
- Which scoped runtime files belong to that scope?
- Which refactor-era Python layers are available in the installation?

Main entrypoint:

```powershell
python -m natbin.runtime_app --json
```

This is intended as a foundation for:

- future operator CLI consolidation,
- cleaner status/health tooling,
- easier milestone commits between refactor packages.
