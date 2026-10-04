"""Strategy framework MVP package."""

from . import a_share_adapter as _a_share_adapter  # noqa: F401 - register default adapter
from . import us_adapter as _us_adapter  # noqa: F401 - register explicit research-only US adapter
from .spec import StrategySpecV0

__all__ = ["StrategySpecV0"]
