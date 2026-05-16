"""Strategy framework MVP package."""

from . import a_share_adapter as _a_share_adapter  # noqa: F401 - register default adapter
from .spec import StrategySpecV0

__all__ = ["StrategySpecV0"]
