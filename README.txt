Package W hotfix: dataset2 session_id robust under pandas groupby.apply include_groups changes

What it fixes:
- CI failure: KeyError: ['session_id'] not in index (tests/test_dataset2_last_candle_included.py)

How to apply:
- Copy src/natbin/dataset2.py into your repo (overwriting existing).
- Run: pytest -q

