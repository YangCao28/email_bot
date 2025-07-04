#!/bin/bash

# 镜像名称
PULL_IMAGE=yc403/email_bot-pull_email:latest
RESPONSE_IMAGE=yc403/email_bot-process_response:latest

# .env 文件路径
ENV_FILE=.env

echo "🔧 Linux: using --network=host for direct Redis access"

# 启动 pull_email 容器
echo "🚀 Starting pull_email container..."
docker rm -f pull_email 2>/dev/null
docker run -d \
    --name pull_email \
    --network=host \
    --env-file $ENV_FILE \
    $PULL_IMAGE

# 启动 process_response 容器
echo "🚀 Starting process_response container..."
docker rm -f process_response 2>/dev/null
docker run -d \
    --name process_response \
    --network=host \
    --env-file $ENV_FILE \
    $RESPONSE_IMAGE

echo "✅ Containers started using host networking"
