#!/bin/bash
# codegraph-sync.sh - 自动同步 codegraph 索引
# 在 WSL2 中执行 codegraph sync

export PATH="$HOME/.nvm/versions/node/v22.22.3/bin:$PATH"

# 项目路径（WSL2 格式）
PROJECT_DIR="/mnt/g/AgentForge"

# 检查是否在项目目录
if [ ! -d "$PROJECT_DIR/.codegraph" ]; then
    echo "Error: .codegraph directory not found in $PROJECT_DIR"
    exit 1
fi

# 执行 sync
cd "$PROJECT_DIR" || exit 1
echo "Running codegraph sync..."
codegraph sync 2>&1
