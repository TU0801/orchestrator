#!/bin/bash
# 全プロジェクトの状態をスキャン

CONFIG_FILE="$HOME/orchestrator/config.json"
OUTBOX_DIR="$HOME/orchestrator/outbox"
TIMESTAMP=$(date -Iseconds)
SCAN_REPORT="$OUTBOX_DIR/scan_report_$(date +%Y%m%d_%H%M%S).json"

echo "=========================================="
echo "全プロジェクトスキャン開始"
echo "=========================================="
echo "開始時刻: $TIMESTAMP"
echo ""

# config.jsonからプロジェクト一覧を取得
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ 設定ファイルが見つかりません: $CONFIG_FILE"
    exit 1
fi

# JSONから情報を抽出（jqがあれば使う、なければPythonで）
if command -v jq &> /dev/null; then
    PROJECTS=$(jq -r '.projects[] | .name + ":" + .path' "$CONFIG_FILE")
else
    PROJECTS=$(python3 -c "
import json
with open('$CONFIG_FILE', 'r') as f:
    config = json.load(f)
for p in config['projects']:
    print(f\"{p['name']}:{p['path']}\")
" 2>/dev/null)
fi

# スキャン結果を格納
RESULTS="["
FIRST=true

# 各プロジェクトをスキャン
while IFS=: read -r name path; do
    echo "📁 スキャン中: $name"
    echo "   パス: $path"

    if [ ! -d "$path" ]; then
        echo "   ⚠️  ディレクトリが見つかりません"
        continue
    fi

    # scan_project.py が存在すればそれを実行
    SCAN_SCRIPT="$path/scan_project.py"
    if [ -f "$SCAN_SCRIPT" ]; then
        cd "$path" || continue

        if python3 "$SCAN_SCRIPT" &> /dev/null; then
            echo "   ✓ スキャン完了"

            # PROJECT_STATE.json を読み込んで結果に追加
            if [ -f "$path/PROJECT_STATE.json" ]; then
                STATE=$(cat "$path/PROJECT_STATE.json")

                # JSON配列に追加
                if [ "$FIRST" = true ]; then
                    FIRST=false
                else
                    RESULTS="$RESULTS,"
                fi

                RESULTS="$RESULTS{\"project\":\"$name\",\"state\":$STATE}"
            fi
        else
            echo "   ❌ スキャンエラー"
        fi
    else
        echo "   ⚠️  scan_project.py が見つかりません"
    fi

    echo ""
done <<< "$PROJECTS"

RESULTS="$RESULTS]"

# レポート作成
cat > "$SCAN_REPORT" << EOF
{
  "scan_type": "all_projects",
  "timestamp": "$TIMESTAMP",
  "completed_at": "$(date -Iseconds)",
  "projects_scanned": $(echo "$PROJECTS" | wc -l),
  "results": $RESULTS
}
EOF

echo "=========================================="
echo "スキャン完了"
echo "=========================================="
echo "レポート: $SCAN_REPORT"
echo ""
