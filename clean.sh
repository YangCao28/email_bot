#!/bin/bash

# 停止并删除所有停止状态的容器
echo "Removing all stopped containers..."
docker container prune -f

# 删除指定名字的容器（比如 pull_email 和 process_response）
for cname in pull_email process_response; do
  cid=$(docker ps -aq -f name="^${cname}$")
  if [ -n "$cid" ]; then
    echo "Stopping and removing container $cname ($cid)..."
    docker stop "$cid"
    docker rm "$cid"
  else
    echo "No container named $cname found."
  fi
done

echo "Cleanup done."