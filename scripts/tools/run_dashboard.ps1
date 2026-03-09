param(
  [string]$RepoRoot = ".",
  [string]$Config   = "config/multi_asset.yaml",
  [int]$Port        = 8501
)

# Helper: run local dashboard (Streamlit).
python -m natbin.dashboard --repo-root $RepoRoot --config $Config --port $Port
