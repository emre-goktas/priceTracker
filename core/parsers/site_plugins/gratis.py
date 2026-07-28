from __future__ import annotations

from pathlib import Path

import yaml

SITE_NAME = "gratis"
CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "tr" / "gratis.yaml"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
