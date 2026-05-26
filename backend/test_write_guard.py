"""Regression checks for the Agent write-completion hallucination guard.

This script is intentionally lightweight: it imports the guard helpers and
checks representative read-only and write-intent turns without starting the
FastAPI server or touching the host system.
"""

import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.graph import _claims_write_completion, _has_write_intent, _is_read_only_intent


def _would_trigger_guard(user_message: str, final_response: str) -> bool:
    """Mirror the guard condition used when no write tool ran."""
    return _has_write_intent(user_message) and _claims_write_completion(final_response)


READ_ONLY_CASES = [
    (
        "查看一下 nginx 配置文件",
        "已读取 nginx 主配置文件。当前服务已成功启动，配置中 listen 80。",
    ),
    (
        "请查看 nginx 配置文件",
        "已读取 /etc/nginx/nginx.conf，文件中已配置默认 server。",
    ),
    (
        "查看 nginx 启动状态",
        "nginx 已启动并处于 active 状态。",
    ),
    (
        "查询已停止的服务",
        "已停止的服务列表为空。",
    ),
    (
        "读取最近的 nginx 错误日志",
        "日志显示上一次重启完成，没有新的 error。",
    ),
    (
        "分析磁盘空间是否需要清理",
        "已清理的旧日志不在当前目录中，本次仅完成分析。",
    ),
    (
        "列出当前安装的软件包",
        "nginx 已安装，版本为 1.24。",
    ),
    (
        "生成健康巡检报告",
        "报告已生成，服务状态显示 nginx 已启动。",
    ),
    (
        "搜索知识库里的 nginx 重启案例",
        "找到历史案例：nginx 重启完成后监听 80 端口。",
    ),
]


WRITE_INTENT_CASES = [
    ("帮我启动 nginx", "已启动 nginx"),
    ("重启 nginx 服务", "已重启 nginx"),
    ("停止 nginx 服务", "已停止 nginx"),
    ("删除 /tmp/core.dump 文件", "已删除 /tmp/core.dump"),
    ("修改 nginx 配置", "已修改 nginx 配置"),
    ("在 /tmp 下创建 test 目录", "已创建 /tmp/test 目录"),
    ("把 sample-copy.txt 改名为 sample-moved.txt", "已成功将 sample-copy.txt 重命名为 sample-moved.txt"),
    ("将 /tmp/a.txt 移动到 /tmp/b.txt", "已移动到 /tmp/b.txt"),
    ("向 /tmp/a.txt 追加一行 hello", "已追加内容到 /tmp/a.txt"),
    ("/tmp/opsguard-manual-test/sample.txt 追加 10086", "已成功向 /tmp/opsguard-manual-test/sample.txt 添加 10086"),
    ("sample.txt 追加 10086", "成功添加 10086"),
    ("/tmp/a.txt 写入 hello", "成功写入 hello"),
    ("复制 /tmp/a.txt 到 /tmp/b.txt", "复制完成"),
    ("rename file sample-copy.txt to sample-moved.txt", "renamed sample-copy.txt to sample-moved.txt"),
    ("安装 nginx 软件包", "已安装 nginx"),
    ("检查并重启 nginx", "已重启 nginx"),
    ("please create directory /tmp/test", "directory create completed"),
    ("please move file /tmp/a.txt to /tmp/b.txt", "move completed"),
    ("please restart nginx service", "nginx restart completed"),
]


def main() -> None:
    for user_message, final_response in READ_ONLY_CASES:
        assert not _would_trigger_guard(user_message, final_response), user_message
        assert _is_read_only_intent(user_message), user_message

    for user_message, final_response in WRITE_INTENT_CASES:
        assert _would_trigger_guard(user_message, final_response), user_message
        assert not _is_read_only_intent(user_message), user_message

    print(
        f"write guard regression OK: "
        f"{len(READ_ONLY_CASES)} read-only cases, "
        f"{len(WRITE_INTENT_CASES)} write-intent cases"
    )


if __name__ == "__main__":
    main()
