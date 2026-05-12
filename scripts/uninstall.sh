#!/bin/bash
# OpsGuard Uninstall Script
# Usage: bash scripts/uninstall.sh

set -e

echo "========================================="
echo "  OpsGuard 卸载脚本"
echo "========================================="

read -p "确认卸载 OpsGuard？(y/N) " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "已取消"
    exit 0
fi

# Stop and disable service
if systemctl is-active --quiet opsguard 2>/dev/null; then
    sudo systemctl stop opsguard
    echo "[INFO] 服务已停止"
fi

if [ -f /etc/systemd/system/opsguard.service ]; then
    sudo systemctl disable opsguard 2>/dev/null || true
    sudo rm /etc/systemd/system/opsguard.service
    sudo systemctl daemon-reload
    echo "[INFO] systemd 服务已移除"
fi

# Remove sudoers
if [ -f /etc/sudoers.d/opsguard ]; then
    sudo rm /etc/sudoers.d/opsguard
    echo "[INFO] sudoers 白名单已移除"
fi

# Remove user (optional)
read -p "是否删除 opsguard 系统用户？(y/N) " del_user
if [ "$del_user" = "y" ] || [ "$del_user" = "Y" ]; then
    sudo userdel -r opsguard 2>/dev/null || true
    echo "[INFO] opsguard 用户已删除"
fi

echo ""
echo "[INFO] 卸载完成。项目文件未删除，如需清理请手动删除项目目录。"
