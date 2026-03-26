from __future__ import annotations

DASHBOARD_CSS = r'''
:root {
  --thalor-bg: #071018;
  --thalor-bg-2: #0b1724;
  --thalor-panel: rgba(10, 21, 35, 0.78);
  --thalor-panel-strong: rgba(13, 28, 45, 0.92);
  --thalor-border: rgba(89, 196, 255, 0.18);
  --thalor-accent: #5de4ff;
  --thalor-accent-2: #8a7dff;
  --thalor-ok: #5df2b8;
  --thalor-warn: #ffd166;
  --thalor-danger: #ff6b8b;
  --thalor-text: #e7f2ff;
  --thalor-muted: #94a8bf;
}

.stApp {
  background:
    radial-gradient(circle at 10% 20%, rgba(93, 228, 255, 0.10), transparent 26%),
    radial-gradient(circle at 90% 0%, rgba(138, 125, 255, 0.12), transparent 30%),
    linear-gradient(180deg, var(--thalor-bg) 0%, #030811 100%);
  color: var(--thalor-text);
}

[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"] {
  background: transparent !important;
}

[data-testid="stSidebar"] {
  background: rgba(5, 12, 20, 0.96);
  border-right: 1px solid var(--thalor-border);
}

.block-container {
  padding-top: 1.2rem;
  padding-bottom: 2.5rem;
}

.thalor-hero {
  position: relative;
  overflow: hidden;
  padding: 1.35rem 1.5rem 1.25rem 1.5rem;
  border-radius: 20px;
  border: 1px solid var(--thalor-border);
  background:
    linear-gradient(135deg, rgba(12, 27, 45, 0.92), rgba(8, 16, 28, 0.98)),
    radial-gradient(circle at top right, rgba(93, 228, 255, 0.25), transparent 34%);
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.26);
  margin-bottom: 1rem;
}

.thalor-hero h1 {
  margin: 0;
  font-size: 2rem;
  line-height: 1.1;
  letter-spacing: 0.03em;
  color: var(--thalor-text);
}

.thalor-hero .eyebrow {
  font-size: 0.8rem;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  color: var(--thalor-accent);
  font-weight: 700;
  margin-bottom: 0.3rem;
}

.thalor-hero .subtitle {
  margin-top: 0.45rem;
  color: var(--thalor-muted);
  font-size: 0.95rem;
}

.thalor-card {
  border-radius: 18px;
  border: 1px solid var(--thalor-border);
  background: linear-gradient(180deg, var(--thalor-panel), rgba(7, 15, 26, 0.9));
  padding: 1rem 1rem 0.95rem 1rem;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
  min-height: 108px;
}

.thalor-card .label {
  color: var(--thalor-muted);
  font-size: 0.78rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-bottom: 0.35rem;
}

.thalor-card .value {
  color: var(--thalor-text);
  font-size: 1.65rem;
  font-weight: 700;
  line-height: 1.1;
}

.thalor-card .meta {
  margin-top: 0.35rem;
  color: var(--thalor-muted);
  font-size: 0.83rem;
}

.thalor-card.ok .value { color: var(--thalor-ok); }
.thalor-card.warn .value { color: var(--thalor-warn); }
.thalor-card.danger .value { color: var(--thalor-danger); }
.thalor-card.accent .value { color: var(--thalor-accent); }

.thalor-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0.35rem 0 0.7rem 0;
}

.thalor-section-title h3 {
  margin: 0;
  font-size: 1.05rem;
  color: var(--thalor-text);
}

.thalor-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.3rem 0.65rem;
  border-radius: 999px;
  border: 1px solid var(--thalor-border);
  background: rgba(10, 19, 32, 0.85);
  color: var(--thalor-muted);
  font-size: 0.78rem;
}

.thalor-badge-ok {
  color: var(--thalor-ok);
  border-color: rgba(93, 242, 184, 0.25);
}

.thalor-badge-warn {
  color: var(--thalor-warn);
  border-color: rgba(255, 209, 102, 0.28);
}

.thalor-badge-danger {
  color: var(--thalor-danger);
  border-color: rgba(255, 107, 139, 0.28);
}

div[data-testid="stDataFrame"],
div[data-testid="stTable"] {
  border-radius: 18px;
  border: 1px solid var(--thalor-border);
  overflow: hidden;
}

div[data-testid="stMetric"] {
  background: linear-gradient(180deg, rgba(11, 24, 39, 0.78), rgba(6, 13, 23, 0.92));
  border-radius: 16px;
  border: 1px solid var(--thalor-border);
  padding: 0.75rem 0.85rem;
}

.stTabs [data-baseweb="tab-list"] {
  gap: 0.35rem;
}

.stTabs [data-baseweb="tab"] {
  border-radius: 12px 12px 0 0;
  background: rgba(8, 16, 28, 0.65);
  border: 1px solid transparent;
  color: var(--thalor-muted);
  padding: 0.75rem 1rem;
}

.stTabs [aria-selected="true"] {
  background: rgba(13, 28, 45, 0.95) !important;
  border-color: var(--thalor-border) !important;
  color: var(--thalor-text) !important;
}

.stAlert {
  border-radius: 14px;
}
'''

__all__ = ['DASHBOARD_CSS']
