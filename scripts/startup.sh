#!/bin/bash
# Orchestrator 起動スクリプト

ORCHESTRATOR_DIR="$HOME/orchestrator"
MASTER_PY="$ORCHESTRATOR_DIR/master.py"
PID_FILE="$ORCHESTRATOR_DIR/orchestrator.pid"
LOG_DIR="$ORCHESTRATOR_DIR/logs"
LOG_FILE="$LOG_DIR/startup_$(date +%Y%m%d_%H%M%S).log"

# ログディレクトリ作成
mkdir -p "$LOG_DIR"

echo "=========================================="
echo "Orchestrator 起動"
echo "=========================================="
echo "開始時刻: $(date -Iseconds)" | tee -a "$LOG_FILE"

# 既に起動中かチェック
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")

    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️  Orchestrator は既に起動中です (PID: $OLD_PID)" | tee -a "$LOG_FILE"
        exit 1
    else
        echo "古いPIDファイルを削除します" | tee -a "$LOG_FILE"
        rm "$PID_FILE"
    fi
fi

# master.pyの存在確認
if [ ! -f "$MASTER_PY" ]; then
    echo "❌ master.py が見つかりません: $MASTER_PY" | tee -a "$LOG_FILE"
    exit 1
fi

# Pythonのバージョン確認
if ! command -v python3 &> /dev/null; then
    echo "❌ python3 が見つかりません" | tee -a "$LOG_FILE"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "Python version: $PYTHON_VERSION" | tee -a "$LOG_FILE"

# バックグラウンドで起動
echo "🚀 Orchestrator を起動中..." | tee -a "$LOG_FILE"

nohup python3 "$MASTER_PY" >> "$LOG_FILE" 2>&1 &
PID=$!

# PIDを保存
echo "$PID" > "$PID_FILE"

# 起動確認（2秒待つ）
sleep 2

if ps -p "$PID" > /dev/null 2>&1; then
    echo "✅ Orchestrator が起動しました" | tee -a "$LOG_FILE"
    echo "   PID: $PID" | tee -a "$LOG_FILE"
    echo "   PIDファイル: $PID_FILE" | tee -a "$LOG_FILE"
    echo "   ログファイル: $LOG_FILE" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
    echo "停止するには: kill $PID" | tee -a "$LOG_FILE"
    echo "または: kill \$(cat $PID_FILE)" | tee -a "$LOG_FILE"
    exit 0
else
    echo "❌ Orchestrator の起動に失敗しました" | tee -a "$LOG_FILE"
    rm "$PID_FILE"
    exit 1
fi
