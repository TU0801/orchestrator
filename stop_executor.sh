#!/bin/bash
# Task Executor停止スクリプト

if pgrep -f "task_executor.py" > /dev/null; then
    PID=$(pgrep -f "task_executor.py")
    echo "Stopping task executor (PID: $PID)..."
    pkill -f task_executor.py
    sleep 2

    if pgrep -f "task_executor.py" > /dev/null; then
        echo "❌ Failed to stop. Force killing..."
        pkill -9 -f task_executor.py
    else
        echo "✓ Task executor stopped"
    fi
else
    echo "Task executor is not running"
fi
