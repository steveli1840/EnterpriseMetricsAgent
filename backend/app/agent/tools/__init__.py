from app.agent.tools.catalog import DescribeTableTool, ListTablesTool
from app.agent.tools.memory_tools import SaveMemoryTool, SearchMemoryTool
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.run_sql import RunSqlTool
from app.agent.tools.time_range import ResolveTimeRangeTool


def build_explore_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SearchMemoryTool())
    registry.register(ResolveTimeRangeTool())
    registry.register(ListTablesTool())
    registry.register(DescribeTableTool())
    registry.register(RunSqlTool())
    registry.register(SaveMemoryTool())
    return registry
