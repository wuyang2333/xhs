#!/bin/bash
cd "$(dirname "$0")"
git add -A
git commit -m "更新 $(date +%Y-%m-%d-%H%M)"
git push
echo ""
echo "=== 推送完成 ==="
echo "Windows 那边运行 update.bat 即可更新"
sleep 3
