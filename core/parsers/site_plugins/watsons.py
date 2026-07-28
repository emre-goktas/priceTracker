from __future__ import annotations

from pathlib import Path

import yaml

SITE_NAME = "watsons"
CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "tr" / "watsons.yaml"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
