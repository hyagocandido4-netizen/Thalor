# Package M3 – Config Cutover Bridge

This document explains the low-risk cutover strategy used in Package M3.

## Problem
Historically, operational modules were not consistently reading from the same configuration source. Some modules used legacy loaders / env probes while newer runtime components started using `natbin.config`.

That creates two risks:
1. runtime drift between steps in the same cycle
2. hidden behavior changes between CLI, CI, and daemon execution

## Strategy
Package M3 does **not** try to rewrite every operational module at once.

Instead it introduces a compatibility bridge:

- `natbin.config.compat_runtime`
- `natbin.config2`
- `natbin.settings`

The bridge delegates to the new loader if available and exports a small stable legacy surface for old modules.

## Goal
Move runtime behavior onto `ResolvedConfig` **without** forcing a dangerous big-bang cutover.

## Principles
- prefer `natbin.config.loader.load_resolved_config(...)`
- preserve old import surfaces where possible
- export resolved runtime values to env for legacy callers
- keep the bridge deterministic and minimal
