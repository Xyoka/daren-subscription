#!/bin/bash

echo "=== 启动服务 ==="
echo "Python: $(python --version 2>&1)"

# 初始化数据库
python -c "from app.db import init_db; init_db(); print('数据库就绪')" 2>&1

# 后台启动 Worker（定时爬取 + 推送）
echo "--- 启动 Worker ---"
python -m app.services.worker &
echo "Worker PID: $!"

# 前台启动 API
echo "--- 启动 API ---"
exec uvicorn app.main:app --host 0.0.0.0 --port 80 --log-level info 2>&1
