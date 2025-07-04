#!/bin/sh
while true; do
  python /app/email_pull.py
  sleep 600  # 60秒后继续执行
done
