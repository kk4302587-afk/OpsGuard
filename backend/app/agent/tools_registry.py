"""MCP Tools Registry.

Registers all available tools and provides metadata for LLM function calling.
Each tool has a risk_level that determines whether approval is needed.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Callable, Any

from loguru import logger


class RiskLevel(str, Enum):
    """Risk classification for tool operations."""
    READ = "read"           # Safe, no approval needed
    WRITE = "write"         # Modifying, needs approval
    DESTRUCTIVE = "destructive"  # Dangerous, needs explicit approval + warning


@dataclass
class ToolDefinition:
    """Definition of a registered MCP tool."""
    name: str
    description: str
    parameters: dict
    function: Callable
    risk_level: RiskLevel
    category: str  # process, disk, network, log, service, config, system


class ToolsRegistry:
    """Central registry of all available MCP tools."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._register_all_tools()

    def _register_all_tools(self):
        """Register all MCP tools from tool modules."""
        from app.mcp_tools import process_tools, disk_tools, network_tools, log_tools, service_tools, config_tools, system_tools

        # Process tools
        self._register("list_processes", "列出运行中的进程，按资源使用排序", {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "enum": ["cpu", "memory", "pid"], "description": "排序方式"},
                "limit": {"type": "integer", "description": "返回数量上限", "default": 20},
            },
        }, process_tools.list_processes, RiskLevel.READ, "process")

        self._register("find_zombie_processes", "查找僵尸进程", {
            "type": "object", "properties": {},
        }, process_tools.find_zombie_processes, RiskLevel.READ, "process")

        self._register("get_process_detail", "获取指定进程的详细信息", {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "进程ID"},
            },
            "required": ["pid"],
        }, process_tools.get_process_detail, RiskLevel.READ, "process")

        self._register("kill_process", "向进程发送信号（终止进程）", {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "进程ID"},
                "signal": {"type": "integer", "description": "信号编号 (15=TERM, 9=KILL)", "default": 15},
            },
            "required": ["pid"],
        }, process_tools.kill_process, RiskLevel.WRITE, "process")

        # Disk tools
        self._register("get_disk_usage", "获取磁盘使用情况", {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件系统路径", "default": "/"},
            },
        }, disk_tools.get_disk_usage, RiskLevel.READ, "disk")

        self._register("find_large_files", "查找大文件", {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "搜索目录", "default": "/"},
                "min_size": {"type": "string", "description": "最小文件大小 (如 100M, 1G)", "default": "100M"},
                "limit": {"type": "integer", "description": "返回数量上限", "default": 20},
            },
        }, disk_tools.find_large_files, RiskLevel.READ, "disk")

        self._register("get_directory_size", "获取目录总大小", {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
            },
            "required": ["path"],
        }, disk_tools.get_directory_size, RiskLevel.READ, "disk")

        self._register("get_inode_usage", "获取 inode 使用情况", {
            "type": "object", "properties": {},
        }, disk_tools.get_inode_usage, RiskLevel.READ, "disk")

        self._register("check_file_info", "获取文件详细信息（权限、引用进程等）", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "文件路径"},
            },
            "required": ["filepath"],
        }, disk_tools.check_file_info, RiskLevel.READ, "disk")

        # Network tools
        self._register("get_listening_ports", "获取所有监听端口及关联进程", {
            "type": "object", "properties": {},
        }, network_tools.get_listening_ports, RiskLevel.READ, "network")

        self._register("get_connections", "获取网络连接（按状态过滤）", {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "连接状态过滤", "default": "established"},
            },
        }, network_tools.get_connections, RiskLevel.READ, "network")

        self._register("get_connection_count", "获取连接数统计", {
            "type": "object", "properties": {},
        }, network_tools.get_connection_count, RiskLevel.READ, "network")

        self._register("check_port", "检查指定端口被哪个进程占用", {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "端口号"},
            },
            "required": ["port"],
        }, network_tools.check_port, RiskLevel.READ, "network")

        self._register("ping_host", "Ping 主机检查连通性", {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "主机名或IP"},
                "count": {"type": "integer", "description": "Ping 次数", "default": 4},
            },
            "required": ["host"],
        }, network_tools.ping_host, RiskLevel.READ, "network")

        # Log tools
        self._register("get_journal_logs", "获取 systemd 日志", {
            "type": "object",
            "properties": {
                "unit": {"type": "string", "description": "服务名称（可选）"},
                "since": {"type": "string", "description": "时间范围", "default": "1h ago"},
                "priority": {"type": "string", "description": "最低优先级 (err, warning, info 等)"},
                "lines": {"type": "integer", "description": "行数上限", "default": 50},
            },
        }, log_tools.get_journal_logs, RiskLevel.READ, "log")

        self._register("get_recent_errors", "获取最近的错误日志", {
            "type": "object",
            "properties": {
                "lines": {"type": "integer", "description": "行数上限", "default": 30},
            },
        }, log_tools.get_recent_errors, RiskLevel.READ, "log")

        self._register("tail_log_file", "读取日志文件末尾", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "日志文件路径"},
                "lines": {"type": "integer", "description": "行数", "default": 50},
            },
            "required": ["filepath"],
        }, log_tools.tail_log_file, RiskLevel.READ, "log")

        self._register("search_logs", "搜索日志中的模式", {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索模式（正则）"},
                "filepath": {"type": "string", "description": "指定日志文件（可选，默认搜索 journal）"},
                "lines": {"type": "integer", "description": "结果上限", "default": 30},
            },
            "required": ["pattern"],
        }, log_tools.search_logs, RiskLevel.READ, "log")

        self._register("get_boot_logs", "获取本次启动的警告和错误日志", {
            "type": "object", "properties": {},
        }, log_tools.get_boot_logs, RiskLevel.READ, "log")

        # Service tools
        self._register("list_services", "列出系统服务", {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "按状态过滤 (running, failed, inactive)"},
            },
        }, service_tools.list_services, RiskLevel.READ, "service")

        self._register("get_service_status", "获取服务详细状态", {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "服务名称"},
            },
            "required": ["service"],
        }, service_tools.get_service_status, RiskLevel.READ, "service")

        self._register("get_failed_services", "获取所有失败的服务", {
            "type": "object", "properties": {},
        }, service_tools.get_failed_services, RiskLevel.READ, "service")

        self._register("restart_service", "重启服务", {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "服务名称"},
            },
            "required": ["service"],
        }, service_tools.restart_service, RiskLevel.WRITE, "service")

        self._register("stop_service", "停止服务", {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "服务名称"},
            },
            "required": ["service"],
        }, service_tools.stop_service, RiskLevel.WRITE, "service")

        self._register("get_service_logs", "获取服务日志", {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "服务名称"},
                "lines": {"type": "integer", "description": "行数", "default": 50},
            },
            "required": ["service"],
        }, service_tools.get_service_logs, RiskLevel.READ, "service")

        # Config tools
        self._register("read_config_file", "读取配置文件内容", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "配置文件路径"},
            },
            "required": ["filepath"],
        }, config_tools.read_config_file, RiskLevel.READ, "config")

        self._register("check_config_syntax", "检查配置文件语法", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "配置文件路径"},
            },
            "required": ["filepath"],
        }, config_tools.check_config_syntax, RiskLevel.READ, "config")

        self._register("diff_config", "对比配置文件与基线版本", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "当前配置文件路径"},
                "baseline_path": {"type": "string", "description": "基线配置文件路径"},
            },
            "required": ["filepath", "baseline_path"],
        }, config_tools.diff_config, RiskLevel.READ, "config")

        # System composite tools
        self._register("system_overview", "获取系统综合概览（CPU/内存/磁盘/负载/内核）", {
            "type": "object", "properties": {},
        }, system_tools.system_overview, RiskLevel.READ, "system")

        self._register("health_check", "快速健康检查（识别明显问题）", {
            "type": "object", "properties": {},
        }, system_tools.health_check, RiskLevel.READ, "system")

        self._register("get_crontab_list", "列出定时任务", {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "指定用户（可选）"},
            },
        }, system_tools.get_crontab_list, RiskLevel.READ, "system")

        self._register("get_user_sessions", "获取当前登录用户会话", {
            "type": "object", "properties": {},
        }, system_tools.get_user_sessions, RiskLevel.READ, "system")

        logger.info(f"Tools registry loaded: {len(self._tools)} tools")

    def _register(
        self,
        name: str,
        description: str,
        parameters: dict,
        function: Callable,
        risk_level: RiskLevel,
        category: str,
    ):
        """Register a single tool."""
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            function=function,
            risk_level=risk_level,
            category=category,
        )

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_all_tools_for_llm(self) -> list[dict]:
        """Get all tools formatted for LLM function calling."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in self._tools.values()
        ]

    def get_tools_by_category(self, category: str) -> list[ToolDefinition]:
        """Get tools filtered by category."""
        return [t for t in self._tools.values() if t.category == category]

    def execute_tool(self, name: str, arguments: dict) -> Any:
        """Execute a tool by name with given arguments."""
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        return tool.function(**arguments)


# Global registry instance
tools_registry = ToolsRegistry()
