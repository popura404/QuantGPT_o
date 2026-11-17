"""The public entry points should all lead a research agent to the same guide."""

import json
import re
from pathlib import Path

import pytest

from quantgpt.mcp_server import mcp
from quantgpt.strategy.validator import validate_strategy_spec

ROOT = Path(__file__).resolve().parents[1]
GUIDE = "docs/agent_safe_workflow.md"


@pytest.mark.asyncio
async def test_research_agent_can_discover_the_guide_without_starting_a_task():
    assert (ROOT / GUIDE).is_file()
    for name in ("README.md", "PROMPT.md"):
        assert f"]({GUIDE})" in (ROOT / name).read_text(encoding="utf-8")

    assert GUIDE in (mcp.instructions or "")
    assert "shared_research_workflow" in {prompt.name for prompt in await mcp.list_prompts()}
    prompt = await mcp.get_prompt("shared_research_workflow")
    content = prompt.messages[0].content
    assert content.type == "text" and GUIDE in content.text


@pytest.mark.asyncio
async def test_documented_strategy_validation_example_matches_the_mcp_schema():
    guide = (ROOT / "docs/MCP_GUIDE.md").read_text(encoding="utf-8")
    section = guide.split("### StrategySpec v0 工作流", 1)[1].split("\n### ", 1)[0]
    example = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert example is not None
    arguments = json.loads(example[1])
    tool = next(tool for tool in await mcp.list_tools() if tool.name == "validate_strategy_spec")
    assert set(tool.inputSchema["required"]) <= arguments.keys() <= tool.inputSchema["properties"].keys()
    validation = validate_strategy_spec(arguments["spec"])
    assert validation.is_valid, validation.issues
