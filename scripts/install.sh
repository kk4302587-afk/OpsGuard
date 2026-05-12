#!/bin/bash
# OpsGuard Installation Script for Kylin V11 (LoongArch / x86_64)
# Usage: bash scripts/install.sh

set -e

echo "========================================="
echo "  OpsGuard - 智能运维 Agent 安装脚本"
echo "========================================="

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "请不要以 root 用户运行此脚本。请使用普通用户运行。"
fi

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
info "项目目录: $PROJECT_DIR"

# ============================================
# Step 1: System dependencies
# ============================================
info "检查系统依赖..."

# Check Python version
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    info "Python 版本: $PYTHON_VERSION"
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
        error "需要 Python 3.10+，当前版本: $PYTHON_VERSION"
    fi
else
    error "未找到 python3，请先安装 Python 3.10+"
fi

# Check Node.js
if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version)
    info "Node.js 版本: $NODE_VERSION"
else
    warn "未找到 Node.js，将跳过前端构建。如需前端，请安装 Node.js 18+"
fi

# ============================================
# Step 2: Create opsguard system user
# ============================================
info "配置 opsguard 系统用户..."

if id "opsguard" &>/dev/null; then
    info "opsguard 用户已存在"
else
    warn "创建 opsguard 用户需要 sudo 权限"
    sudo useradd -r -s /bin/bash -m -d /var/lib/opsguard opsguard
    info "opsguard 用户已创建"
fi

# Create necessary directories
sudo mkdir -p /var/lib/opsguard/backups
sudo chown opsguard:opsguard /var/lib/opsguard/backups

# ============================================
# Step 3: Configure sudoers whitelist
# ============================================
info "配置 sudoers 白名单..."

SUDOERS_FILE="/etc/sudoers.d/opsguard"
if [ ! -f "$SUDOERS_FILE" ]; then
    warn "写入 sudoers 白名单需要 sudo 权限"
    sudo tee "$SUDOERS_FILE" > /dev/null << 'EOF'
# OpsGuard - Minimum privilege sudoers whitelist
# Only these commands can be run with elevated privileges

opsguard ALL=(ALL) NOPASSWD: /bin/systemctl restart *
opsguard ALL=(ALL) NOPASSWD: /bin/systemctl stop *
opsguard ALL=(ALL) NOPASSWD: /bin/systemctl start *
opsguard ALL=(ALL) NOPASSWD: /bin/systemctl status *
opsguard ALL=(ALL) NOPASSWD: /bin/journalctl *
opsguard ALL=(ALL) NOPASSWD: /bin/kill *
opsguard ALL=(ALL) NOPASSWD: /bin/rm /tmp/*
opsguard ALL=(ALL) NOPASSWD: /bin/rm /var/log/*.log.*
EOF
    sudo chmod 440 "$SUDOERS_FILE"
    info "sudoers 白名单已配置"
else
    info "sudoers 白名单已存在"
fi

# ============================================
# Step 4: Install Python dependencies
# ============================================
info "安装 Python 依赖..."

cd "$PROJECT_DIR/backend"

# Create virtual environment
if [ ! -d "venv" ]; then
    python3 -m venv venv
    info "虚拟环境已创建"
fi

source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
info "Python 依赖安装完成"

# ============================================
# Step 5: Build frontend (if Node.js available)
# ============================================
if command -v node &> /dev/null; then
    info "构建前端..."
    cd "$PROJECT_DIR/frontend"
    npm install --silent
    npm run build
    info "前端构建完成"

    # Copy build output to backend static directory
    mkdir -p "$PROJECT_DIR/backend/static"
    cp -r dist/* "$PROJECT_DIR/backend/static/"
    info "前端静态文件已复制到 backend/static/"
else
    warn "跳过前端构建（未安装 Node.js）"
fi

# ============================================
# Step 6: Initialize database
# ============================================
info "初始化数据库..."

cd "$PROJECT_DIR/backend"
source venv/bin/activate
python3 -c "
import asyncio
from app.database import init_db
asyncio.run(init_db())
print('Database initialized')
"
info "数据库初始化完成"

# ============================================
# Step 7: Create systemd service
# ============================================
info "创建 systemd 服务..."

SERVICE_FILE="/etc/systemd/system/opsguard.service"
if [ ! -f "$SERVICE_FILE" ]; then
    warn "创建 systemd 服务需要 sudo 权限"
    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=OpsGuard Intelligent Operations Agent
After=network.target

[Service]
Type=simple
User=opsguard
Group=opsguard
WorkingDirectory=$PROJECT_DIR/backend
Environment=PATH=$PROJECT_DIR/backend/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$PROJECT_DIR/backend/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$PROJECT_DIR/backend/data /var/lib/opsguard

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    info "systemd 服务已创建"
else
    info "systemd 服务已存在"
fi

# ============================================
# Step 8: Set permissions
# ============================================
info "设置文件权限..."

# Backend data directory
mkdir -p "$PROJECT_DIR/backend/data"
sudo chown -R opsguard:opsguard "$PROJECT_DIR/backend/data"

# Config file should be readable by opsguard
sudo chown root:opsguard "$PROJECT_DIR/backend/config.yaml"
sudo chmod 640 "$PROJECT_DIR/backend/config.yaml"

# ============================================
# Done
# ============================================
echo ""
echo "========================================="
echo -e "${GREEN}  OpsGuard 安装完成!${NC}"
echo "========================================="
echo ""
echo "后续步骤:"
echo "  1. 编辑配置文件: $PROJECT_DIR/backend/config.yaml"
echo "     - 填入 LLM API Key"
echo ""
echo "  2. 启动服务:"
echo "     sudo systemctl start opsguard"
echo "     sudo systemctl enable opsguard  # 开机自启"
echo ""
echo "  3. 访问界面:"
echo "     http://localhost:8000"
echo ""
echo "  4. 查看日志:"
echo "     sudo journalctl -u opsguard -f"
echo ""
echo "  5. 开发模式（不使用 systemd）:"
echo "     cd $PROJECT_DIR/backend"
echo "     source venv/bin/activate"
echo "     python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
echo ""
