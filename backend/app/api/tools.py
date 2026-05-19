"""MCP Tools registry API - displays all available tools."""

from fastapi import APIRouter

from app.agent.tools_registry import tools_registry

router = APIRouter()


@router.get("/")
async def list_tools():
    """List all registered MCP tools with metadata."""
    tools = []
    for name, tool_def in tools_registry._tools.items():
        tools.append({
            "name": tool_def.name,
            "display_name": tool_def.display_name or tool_def.name,
            "description": tool_def.description,
            "category": tool_def.category,
            "risk_level": tool_def.risk_level.value,
            "parameters": tool_def.parameters,
        })

    # Group by category
    categories = {}
    for tool in tools:
        cat = tool["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool)

    category_labels = {
        "process": "进程管理",
        "disk": "磁盘文件",
        "network": "网络诊断",
        "log": "日志分析",
        "service": "服务管理",
        "config": "配置检查",
        "system": "系统概览",
    }

    return {
        "total": len(tools),
        "categories": [
            {
                "key": cat,
                "label": category_labels.get(cat, cat),
                "tools": cat_tools,
                "count": len(cat_tools),
            }
            for cat, cat_tools in categories.items()
        ],
    }


@router.get("/{tool_name}")
async def get_tool_detail(tool_name: str):
    """Get detailed info about a specific tool."""
    tool_def = tools_registry.get_tool(tool_name)
    if not tool_def:
        return {"error": f"Tool '{tool_name}' not found"}

    return {
        "name": tool_def.name,
        "display_name": tool_def.display_name or tool_def.name,
        "description": tool_def.description,
        "category": tool_def.category,
        "risk_level": tool_def.risk_level.value,
        "parameters": tool_def.parameters,
    }
