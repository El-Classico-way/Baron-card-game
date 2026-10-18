"""Výdrž mode: collect locations by out-lasting the others on the drinks drawn."""
from modes.vydrz.game import ActionResult, Phase, VydrzGame
from modes.vydrz.loader import VydrzContent, load_content, load_default_content
from modes.vydrz.models import DRINKS

__all__ = [
    "ActionResult", "Phase", "VydrzGame", "VydrzContent",
    "load_content", "load_default_content", "DRINKS",
]
