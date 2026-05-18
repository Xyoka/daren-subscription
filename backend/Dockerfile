FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（编译依赖 + MySQL 客户端 + Playwright 浏览器所需库）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        default-libmysqlclient-dev \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxcb1 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Playwright Chromium 浏览器
RUN python -m playwright install chromium && \
    python -m playwright install-deps chromium

# 复制应用代码（排除本地 .env 避免干扰环境变量）
COPY . .
RUN rm -f .env .env.save

# 启动脚本添加执行权限
RUN chmod +x start.sh

# 暴露端口
EXPOSE 80

# 使用启动脚本（会输出详细的错误日志）
CMD ["./start.sh"]
