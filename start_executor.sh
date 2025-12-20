#!/bin/bash
# Task Executor起動スクリプト

cd ~/orchestrator

# ログディレクトリを作成
mkdir -p logs

# 既存のプロセスをチェック
if pgrep -f "task_executor.py" > /dev/null; then
    echo "⚠️  Task executor is already running"
    echo "   PID: $(pgrep -f 'task_executor.py')"
    exit 1
fi

# バックグラウンドで起動
nohup python3 task_executor.py > logs/executor.log 2>&1 &

PID=$!
echo "✓ Task executor started"
echo "  PID: $PID"
echo "  Log: ~/orchestrator/logs/executor.log"
echo ""
echo "To view logs:"
echo "  tail -f ~/orchestrator/logs/executor.log"
echo ""
echo "To stop:"
echo "  kill $PID"
echo "  or: pkill -f task_executor.py"
