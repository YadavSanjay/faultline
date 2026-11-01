"""Shared configuration layer: JSON loading + env overlay, pydantic validation, settings.

One implementation of the config plumbing every module needs — deep-merged per-environment
overlays, a fail-fast validation gate, and env/secret-sourced runtime settings — so a
mis-typed config is caught at startup and the same artifact promotes across environments by
changing env vars, not code. See `docs/design/AITrade_production_hardening_design.md` item 2.
"""
