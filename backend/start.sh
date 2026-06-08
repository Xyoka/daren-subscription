#!/bin/bash

echo "=== 启动服务 ==="

# 数据库连接选择
DB_URL_FINAL="sqlite:///./darensub.db"

if [ -n "$DATABASE_URL" ]; then
    # 构造带超时的 MySQL URL
    RAW_URL="$DATABASE_URL"
    DB_URL_FINAL="${RAW_URL}?charset=utf8mb4&connect_timeout=10&read_timeout=30&write_timeout=30"

    echo "--- 检测 MySQL ---"
    HOST=$(echo "$RAW_URL" | python3 -c "
import sys, os
s = sys.stdin.read().strip()
from urllib.parse import urlparse
print(urlparse(s).hostname or 'unknown')
")

    # 先 ping MySQL 把它唤醒
    python3 -c "
import socket, time
s = socket.socket()
s.settimeout(2)
for i in range(3):
    try:
        s.connect(('$HOST', 3306))
        print('MySQL 可达')
        s.close()
        break
    except:
        print(f'第{i+1}次连接失败，重试...')
        time.sleep(3)
        s = socket.socket()
        s.settimeout(2)
else:
    print('MySQL 不可达，回退 SQLite')
    exit(1)
" 2>/dev/null

    if [ $? -ne 0 ]; then
        DB_URL_FINAL="sqlite:///./darensub.db"
        echo "⚠️  使用 SQLite（数据不持久）"
    else
        echo "✅ 数据将持久化到 MySQL"
    fi
fi

# 导出让 Python 使用
export DATABASE_URL="$DB_URL_FINAL"

# 初始化数据库
python3 -c "from app.db import init_db; init_db(); print('数据库就绪')" 2>&1

# 后台 Worker
python -m app.services.worker &

# 前台 API
exec uvicorn app.main:app --host 0.0.0.0 --port 80 --log-level info 2>&1
