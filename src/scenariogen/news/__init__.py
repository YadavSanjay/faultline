"""News-event consumption for scenariogen (market-intelligence P3, workstream A).

Mirrors ``arag/`` and ``macro/``: a provider that resolves the SEC 8-K event stream + GDELT
salience into a clamped per-name multiplier on the ARag idiosyncratic leg (mode-(ii), no Σ cost),
a gate (freshness + minimum coverage), a mapping (item codes + salience -> multiplier), and a
``ToolSource`` adapter so the agentic ``llm/`` path sees the same events. See design §2.
"""
