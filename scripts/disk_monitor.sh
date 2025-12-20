#!/bin/bash
# ディスク監視スクリプト
# 使用率が閾値を超えたら警告を出力

OUTBOX_DIR="$HOME/orchestrator/outbox"
WARNING_FILE="$OUTBOX_DIR/disk_warning.json"
THRESHOLD=80

# ディスク使用率を取得（%を除いた数値）
USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')

# 数値チェック
if ! [[ "$USAGE" =~ ^[0-9]+$ ]]; then
    echo "Error: Failed to get disk usage"
    exit 1
fi

# 閾値チェック
if [ "$USAGE" -ge "$THRESHOLD" ]; then
    TIMESTAMP=$(date -Iseconds)

    # 警告JSON作成
    cat > "$WARNING_FILE" << EOF
{
  "event": "disk_warning",
  "severity": "warning",
  "timestamp": "$TIMESTAMP",
  "disk_usage": $USAGE,
  "threshold": $THRESHOLD,
  "message": "ディスク使用率が${THRESHOLD}%を超えました (現在: ${USAGE}%)",
  "details": {
    "disk_info": "$(df -h / | awk 'NR==2 {print "Used: "$3" / "$2" (Available: "$4")"}')"
  },
  "recommendations": [
    "古いログファイルを削除",
    "一時ファイルをクリーンアップ",
    "不要なバッチファイルを削除"
  ]
}
EOF

    echo "⚠️  WARNING: Disk usage at ${USAGE}% (threshold: ${THRESHOLD}%)"
    echo "   Alert written to: $WARNING_FILE"
    exit 1
else
    # 使用率が正常な場合、既存の警告ファイルを削除
    if [ -f "$WARNING_FILE" ]; then
        rm "$WARNING_FILE"
    fi

    echo "✓ Disk usage OK: ${USAGE}% (threshold: ${THRESHOLD}%)"
    exit 0
fi
