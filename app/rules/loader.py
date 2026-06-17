"""Scene rules loader (YAML canonical config with JSON fallback)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


DEFAULT_SCENE_CONFIG_PATH = Path(__file__).with_name("scene_config.yaml")


def load_scene_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load scene configuration from YAML (or JSON).

    If ``path`` is omitted, uses ``app/rules/scene_config.yaml``.
    """
    if path is None:
        path = DEFAULT_SCENE_CONFIG_PATH
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"scene config not found: {path}")

    text = path.read_text(encoding="utf-8")

    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise ImportError("pyyaml is required; run: pip install pyyaml") from exc
        return yaml.safe_load(text) or {}

    import json

    return json.loads(text)


def get_scene_rules(state_scene_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return scene rules from state if complete, else fall back to canonical YAML.

    Nodes call this so tests can pass a slim ``scene_config`` while production
    uses the full YAML in ``app/rules/scene_config.yaml``.
    """
    if state_scene_config and "scenes" in state_scene_config:
        return state_scene_config
    return load_scene_config()


# Canonical scoring constants used when scene_config omits the ``scoring`` block.
_DEFAULT_SCORING = {
    "base_match": 60,
    "base_uncertain": 30,
    "base_no_match": 0,
    "signal_weight": 5,
    "signal_min": -20,
    "signal_max": 25,
    "category_bonus": 8,
    "consistency_bonus": 10,
    "prior_bonus_max": 15,
    "match_threshold": 70,
    "quality_low_threshold": 40,
}

_DEFAULT_CATEGORIES = ["兴趣类", "副业类", "金融保险类"]


def get_scoring_config(state_scene_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return scoring constants, overlaying config values on canonical defaults.

    Always returns a complete dict (every key present), so callers can read keys
    without guarding. Used by ``score.py`` so all magic numbers live in config.
    """
    cfg = get_scene_rules(state_scene_config)
    scoring = cfg.get("scoring") or {}
    return {**_DEFAULT_SCORING, **{k: v for k, v in scoring.items() if v is not None}}


def get_categories(state_scene_config: dict[str, Any] | None = None) -> list[str]:
    """Return the authoritative 二级 category label set."""
    cfg = get_scene_rules(state_scene_config)
    cats = cfg.get("categories")
    if cats:
        return list(cats)
    return list(_DEFAULT_CATEGORIES)
