"""Strategy MVP documentation checks."""

import json
import re
from pathlib import Path

from quantgpt.strategy.validator import validate_strategy_spec


ROOT = Path(__file__).resolve().parents[1]


def _read_doc(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_strategy_spec_json_example_validates():
    text = _read_doc("docs/STRATEGY_SPEC.md")
    match = re.search(r"## JSON Example\s+```json\n(.*?)\n```", text, re.DOTALL)

    assert match is not None
    result = validate_strategy_spec(json.loads(match.group(1)))

    assert result.is_valid, [issue.to_dict() for issue in result.issues]


def test_strategy_mvp_docs_cover_entrypoints_and_boundaries():
    strategy_spec = _read_doc("docs/STRATEGY_SPEC.md")
    mcp_guide = _read_doc("docs/MCP_GUIDE.md")
    api_doc = _read_doc("docs/API_DOC.md")
    quickstart = _read_doc("docs/QUICKSTART.md")

    for required in (
        "validate_strategy_spec",
        "run_strategy_backtest",
        "score_strategy",
        "generate_strategy_report",
    ):
        assert required in mcp_guide

    for required in (
        "GET /api/v1/strategy/markets",
        "GET /api/v1/strategy/data-fields",
        "POST /api/v1/strategy/validate",
        "POST /api/v1/strategy/backtest",
    ):
        assert required in api_doc

    assert "YAML Example" in strategy_spec
    assert "MVP Non-Goals" in strategy_spec
    assert "Post-MVP" in strategy_spec
    assert "/api/v1/strategy/backtest" in quickstart
