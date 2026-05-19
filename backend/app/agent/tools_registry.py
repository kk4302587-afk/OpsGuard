"""MCP Tools Registry.

Registers all available tools and provides metadata for LLM function calling.
Each tool has a risk_level that determines whether approval is needed.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Any

from loguru import logger


class RiskLevel(str, Enum):
    """Risk classification for tool operations."""
    READ = "read"           # Safe, no approval needed
    WRITE = "write"         # Modifying, needs approval
    DESTRUCTIVE = "destructive"  # Dangerous, needs explicit approval + warning


# Friendly Chinese names for every registered tool. Keyed by the technical
# tool name (which the LLM tool-calling protocol requires to be a stable
# English identifier). Only the UI consumes display_name; the LLM API never
# sees it, so it's safe to localize freely without breaking tool calling.
#
# If a tool is missing from this map, display_name falls back to `name`.
_DISPLAY_NAMES: dict[str, str] = {
    # Process
    "list_processes": "进程列表",
    "find_zombie_processes": "查找僵尸进程",
    "get_process_detail": "进程详情",
    "kill_process": "终止进程",
    # Disk
    "get_disk_usage": "磁盘使用情况",
    "find_large_files": "查找大文件",
    "get_directory_size": "目录大小",
    "get_inode_usage": "Inode 使用情况",
    "check_file_info": "文件信息",
    # Network
    "get_listening_ports": "监听端口",
    "get_connections": "网络连接",
    "get_connection_count": "连接数统计",
    "check_port": "端口占用查询",
    "ping_host": "Ping 主机",
    # Log
    "get_journal_logs": "systemd 日志",
    "get_recent_errors": "最近错误日志",
    "tail_log_file": "查看日志末尾",
    "search_logs": "搜索日志",
    "get_boot_logs": "启动日志",
    # Service
    "list_services": "服务列表",
    "get_service_status": "服务状态",
    "get_failed_services": "失败的服务",
    "restart_service": "重启服务",
    "start_service": "启动服务",
    "stop_service": "停止服务",
    "get_service_logs": "服务日志",
    # Config
    "read_config_file": "读取配置文件",
    "check_config_syntax": "检查配置语法",
    "diff_config": "对比配置",
    # System
    "system_overview": "系统概览",
    "health_check": "健康检查",
    "get_crontab_list": "定时任务列表",
    "get_user_sessions": "登录用户会话",
    # File
    "write_file": "写入文件",
    "delete_file": "删除文件",
    "delete_directory": "删除目录",
    "move_file": "移动/重命名文件",
    "copy_file": "复制文件",
    "change_permissions": "修改文件权限",
    "change_owner": "修改文件所有者",
    # Package
    "list_installed_packages": "已安装软件包",
    "search_package": "搜索软件包",
    "install_package": "安装软件包",
    "remove_package": "卸载软件包",
    "check_package_updates": "检查软件更新",
    # User
    "list_users": "用户列表",
    "list_groups": "用户组列表",
    "get_user_info": "用户详情",
    "create_user": "创建用户",
    "delete_user": "删除用户",
    "lock_user": "锁定用户",
    "unlock_user": "解锁用户",
    # Firewall
    "get_firewall_status": "防火墙状态",
    "list_open_ports": "已开放端口",
    "allow_port": "开放端口",
    "block_port": "关闭端口",
    # Cron
    "list_cron_jobs": "定时任务详情",
    "list_system_timers": "systemd 定时器",
    "add_cron_job": "添加定时任务",
    "remove_cron_job": "删除定时任务",
}


@dataclass
class ToolDefinition:
    """Definition of a registered MCP tool.

    `name` is the stable identifier used by the LLM tool-calling protocol;
    it MUST stay English / snake_case so OpenAI / Qwen tool schemas validate.
    `display_name` is the human-friendly Chinese label shown in the UI.
    """
    name: str
    description: str
    parameters: dict
    function: Callable
    risk_level: RiskLevel
    category: str  # process, disk, network, log, service, config, system, ...
    display_name: str = field(default="")


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

        self._register("restart_service", "重启正在运行的服务；如果用户要求启动已停止服务，应使用 start_service", {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "服务名称"},
            },
            "required": ["service"],
        }, service_tools.restart_service, RiskLevel.WRITE, "service")

        self._register("start_service", "启动已停止的服务；不会重启已经运行的服务", {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "服务名称"},
            },
            "required": ["service"],
        }, service_tools.start_service, RiskLevel.WRITE, "service")

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

        # File management tools
        from app.mcp_tools import file_tools

        self._register("write_file", "写入或追加文件内容", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "目标文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
                "append": {"type": "boolean", "description": "是否追加模式", "default": False},
            },
            "required": ["filepath", "content"],
        }, file_tools.write_file, RiskLevel.WRITE, "file")

        self._register("delete_file", "删除文件", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "要删除的文件路径"},
            },
            "required": ["filepath"],
        }, file_tools.delete_file, RiskLevel.DESTRUCTIVE, "file")

        self._register("delete_directory", "删除目录", {
            "type": "object",
            "properties": {
                "dirpath": {"type": "string", "description": "要删除的目录路径"},
                "force": {"type": "boolean", "description": "是否强制删除非空目录", "default": False},
            },
            "required": ["dirpath"],
        }, file_tools.delete_directory, RiskLevel.DESTRUCTIVE, "file")

        self._register("move_file", "移动或重命名文件", {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "源路径"},
                "destination": {"type": "string", "description": "目标路径"},
            },
            "required": ["source", "destination"],
        }, file_tools.move_file, RiskLevel.WRITE, "file")

        self._register("copy_file", "复制文件", {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "源文件路径"},
                "destination": {"type": "string", "description": "目标路径"},
            },
            "required": ["source", "destination"],
        }, file_tools.copy_file, RiskLevel.WRITE, "file")

        self._register("change_permissions", "修改文件权限", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "文件路径"},
                "mode": {"type": "string", "description": "权限模式 (如 644, 755)"},
            },
            "required": ["filepath", "mode"],
        }, file_tools.change_permissions, RiskLevel.WRITE, "file")

        self._register("change_owner", "修改文件所有者", {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "文件路径"},
                "owner": {"type": "string", "description": "新所有者 (格式: user:group)"},
            },
            "required": ["filepath", "owner"],
        }, file_tools.change_owner, RiskLevel.WRITE, "file")

        # Package management tools
        from app.mcp_tools import package_tools

        self._register("list_installed_packages", "列出已安装的软件包", {
            "type": "object",
            "properties": {
                "filter_name": {"type": "string", "description": "按名称过滤", "default": ""},
            },
        }, package_tools.list_installed_packages, RiskLevel.READ, "package")

        self._register("search_package", "搜索可用软件包", {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "包名关键词"},
            },
            "required": ["name"],
        }, package_tools.search_package, RiskLevel.READ, "package")

        self._register("install_package", "安装软件包", {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要安装的包名"},
            },
            "required": ["name"],
        }, package_tools.install_package, RiskLevel.WRITE, "package")

        self._register("remove_package", "卸载软件包", {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要卸载的包名"},
            },
            "required": ["name"],
        }, package_tools.remove_package, RiskLevel.WRITE, "package")

        self._register("check_package_updates", "检查可用的软件更新", {
            "type": "object", "properties": {},
        }, package_tools.check_package_updates, RiskLevel.READ, "package")

        # User management tools
        from app.mcp_tools import user_tools

        self._register("list_users", "列出系统用户", {
            "type": "object", "properties": {},
        }, user_tools.list_users, RiskLevel.READ, "user")

        self._register("list_groups", "列出系统用户组", {
            "type": "object", "properties": {},
        }, user_tools.list_groups, RiskLevel.READ, "user")

        self._register("get_user_info", "获取用户详细信息", {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "用户名"},
            },
            "required": ["username"],
        }, user_tools.get_user_info, RiskLevel.READ, "user")

        self._register("create_user", "创建系统用户", {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "用户名"},
                "home_dir": {"type": "string", "description": "主目录路径（可选）", "default": ""},
                "shell": {"type": "string", "description": "登录 shell", "default": "/bin/bash"},
            },
            "required": ["username"],
        }, user_tools.create_user, RiskLevel.WRITE, "user")

        self._register("delete_user", "删除系统用户", {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "用户名"},
                "remove_home": {"type": "boolean", "description": "是否删除主目录", "default": False},
            },
            "required": ["username"],
        }, user_tools.delete_user, RiskLevel.DESTRUCTIVE, "user")

        self._register("lock_user", "锁定用户账户（禁止登录）", {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "用户名"},
            },
            "required": ["username"],
        }, user_tools.lock_user, RiskLevel.WRITE, "user")

        self._register("unlock_user", "解锁用户账户", {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "用户名"},
            },
            "required": ["username"],
        }, user_tools.unlock_user, RiskLevel.WRITE, "user")

        # Firewall tools
        from app.mcp_tools import firewall_tools

        self._register("get_firewall_status", "获取防火墙状态和规则", {
            "type": "object", "properties": {},
        }, firewall_tools.get_firewall_status, RiskLevel.READ, "firewall")

        self._register("list_open_ports", "列出防火墙放行的端口", {
            "type": "object", "properties": {},
        }, firewall_tools.list_open_ports, RiskLevel.READ, "firewall")

        self._register("allow_port", "开放防火墙端口", {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "端口号"},
                "protocol": {"type": "string", "description": "协议 (tcp/udp)", "default": "tcp"},
            },
            "required": ["port"],
        }, firewall_tools.allow_port, RiskLevel.WRITE, "firewall")

        self._register("block_port", "关闭防火墙端口", {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "端口号"},
                "protocol": {"type": "string", "description": "协议 (tcp/udp)", "default": "tcp"},
            },
            "required": ["port"],
        }, firewall_tools.block_port, RiskLevel.WRITE, "firewall")

        # Cron/timer tools
        from app.mcp_tools import cron_tools

        self._register("list_cron_jobs", "列出定时任务（详细）", {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "指定用户（可选）", "default": ""},
            },
        }, cron_tools.list_cron_jobs, RiskLevel.READ, "cron")

        self._register("list_system_timers", "列出 systemd 定时器", {
            "type": "object", "properties": {},
        }, cron_tools.list_system_timers, RiskLevel.READ, "cron")

        self._register("add_cron_job", "添加定时任务", {
            "type": "object",
            "properties": {
                "schedule": {"type": "string", "description": "Cron 表达式 (如 '0 2 * * *' 表示每天凌晨2点)"},
                "command": {"type": "string", "description": "要执行的命令"},
                "user": {"type": "string", "description": "指定用户（可选）", "default": ""},
            },
            "required": ["schedule", "command"],
        }, cron_tools.add_cron_job, RiskLevel.WRITE, "cron")

        self._register("remove_cron_job", "删除匹配的定时任务", {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "匹配文本（包含此文本的任务将被删除）"},
                "user": {"type": "string", "description": "指定用户（可选）", "default": ""},
            },
            "required": ["pattern"],
        }, cron_tools.remove_cron_job, RiskLevel.WRITE, "cron")

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
        """Register a single tool. display_name is auto-looked-up from
        ``_DISPLAY_NAMES``; falls back to ``name`` if not registered there."""
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            function=function,
            risk_level=risk_level,
            category=category,
            display_name=_DISPLAY_NAMES.get(name, name),
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
