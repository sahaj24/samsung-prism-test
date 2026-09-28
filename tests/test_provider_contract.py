from google.genai import types
from livekit.agents import llm
from livekit.plugins.google.utils import create_tools_config

from reprise.agent import make_tool
from reprise.catalog import TOOLS


def test_all_tools_convert_to_live_api_schema():
    context = llm.ToolContext([make_tool(spec, None) for spec in TOOLS])
    tools, mixed = create_tools_config(
        context, tool_behavior=types.Behavior.NON_BLOCKING,
        use_parameters_json_schema=False,
    )
    assert not mixed
    assert {d.name for t in tools for d in t.function_declarations} == {
        s.name for s in TOOLS
    }
