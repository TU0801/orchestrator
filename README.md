# Autonomous Project Orchestrator

複数のプロジェクトを並行管理し、スマホからの短い指示で自律的に作業を進めるシステムの土台

## 概要

このオーケストレーターは以下を実現します:

- 複数プロジェクト（idiom, docflow, tagless等）の並行管理
- inbox/に指示ファイルを置くだけでタスク実行
- プロジェクト状態の自動監視とSQLiteへの記録
- ディスク満杯などの障害検知と通知
- **Google Driveに状態を同期（Claude.ai WebからGCP状態を把握可能）**
- systemdによる常駐サービス化

**現在の実装範囲**: 状態管理のみ（タスク実行は今後実装）

## 新機能: Google Drive連携

Claude.ai Web（Opus）からGCPの状態を把握できるように、Google Driveに状態をアップロードします。

- 5分ごとに自動同期（設定可能）
- プロジェクト状態、ディスク使用率、最新タスクをJSON形式で出力
- スマホからClaude.aiを開いて状態確認可能

**セットアップ**: `GDRIVE_SETUP.md` を参照

## ディレクトリ構成

```
~/orchestrator/
├── master.py                      # メインプロセス（常駐）
├── gdrive_sync.py                 # Google Drive同期スクリプト ⭐新規
├── config.json                    # 管理対象プロジェクト設定
├── requirements.txt               # Python依存パッケージ ⭐新規
├── db/
│   ├── orchestrator.db            # SQLite（状態管理）
│   └── init_schema.sql            # DBスキーマ
├── inbox/                         # 指示を受け取る場所
│   └── processed/                 # 処理済み指示
├── outbox/                        # 実行結果を出す場所
│   └── orchestrator_status.json   # 最新状態（Google Driveと同期） ⭐新規
├── scripts/
│   ├── startup.sh                 # 起動スクリプト
│   ├── disk_monitor.sh            # ディスク監視
│   └── scan_all_projects.sh       # 全プロジェクトスキャン
├── logs/                          # ログファイル
├── orchestrator.service.template  # systemdサービステンプレート
├── README.md                      # このファイル
├── INSTALL_SYSTEMD.md            # systemdインストール手順
└── GDRIVE_SETUP.md               # Google Drive連携セットアップ ⭐新規
```

## クイックスタート

### 1. Orchestratorを起動

```bash
# フォアグラウンドで起動（テスト用）
cd ~/orchestrator
python3 master.py

# または、バックグラウンドで起動
./scripts/startup.sh
```

### 2. 指示を送る

inbox/にJSONファイルを配置:

```bash
cat > ~/orchestrator/inbox/my_task.json << 'EOF'
{
  "instruction": "idiomプロジェクトの状態を確認して"
}
EOF
```

### 3. 結果を確認

最大60秒後にoutbox/に結果が出力されます:

```bash
ls -la ~/orchestrator/outbox/
cat ~/orchestrator/outbox/result_*.json
```

### 4. Google Driveに状態を同期（オプション）

Claude.ai WebからGCPの状態を確認できるようにします:

```bash
# Google Drive APIをセットアップ（初回のみ）
# 詳細は GDRIVE_SETUP.md を参照

# 手動で同期テスト
python3 ~/orchestrator/gdrive_sync.py

# 状態ファイルを確認
cat ~/orchestrator/outbox/orchestrator_status.json
```

**含まれる情報:**
- 全プロジェクトの状態（最終コミット、未コミット変更数など）
- ディスク使用率（現在: 24.74%）
- 最新のタスク履歴
- 最新の指示
- システムヘルス

**Claude.ai Webでの確認方法:**
1. Google Driveから `orchestrator_status/orchestrator_status.json` をダウンロード
2. Claude.ai Webにアップロード
3. 「このJSONファイルの内容を要約して、重要なポイントを教えて」

## 設定ファイル (config.json)

```json
{
  "projects": [
    {
      "name": "idiom",
      "path": "/home/sorakun_fukuoka/projects/idiom-metaphor-analyzer",
      "priority": "high",
      "tmux_session": "idiom",
      "auto_scan": true
    }
  ],
  "settings": {
    "scan_interval_seconds": 60,
    "disk_warning_threshold": 80,
    "notification": "file",
    "inbox_check_interval": 10,
    "gdrive_sync_enabled": true,
    "gdrive_sync_interval": 300,
    "gdrive_folder_name": "orchestrator_status"
  }
}
```

### プロジェクトを追加

config.jsonの`projects`配列に追加:

```json
{
  "name": "docflow",
  "path": "/home/sorakun_fukuoka/projects/docflow",
  "priority": "medium",
  "tmux_session": "docflow",
  "auto_scan": true
}
```

## 指示の形式

### 基本形式

```json
{
  "instruction": "抽象的な指示をここに書く"
}
```

### 対応している指示（現在の実装）

- `"idiomプロジェクトの状態を確認して"` → check_status タスク
- `"未コミットの変更をコミット"` → git_commit タスク
- `"TODOを整理"` → organize_todos タスク

**注意**: 現在は指示を解析してタスクに分解するのみ。実際の実行は今後実装予定。

## データベース構造

SQLiteで以下を管理:

### project_states
- プロジェクトの現在状態
- 最終スキャン日時、コミット情報、未コミット変更数など

### instructions
- 受信した指示の履歴
- 解析結果、処理状態、結果

### system_events
- システムイベントログ
- startup, shutdown, disk_warning など

### 確認方法

```bash
python3 -c "
import sqlite3
db = sqlite3.connect('$HOME/orchestrator/db/orchestrator.db')
db.row_factory = sqlite3.Row

print('=== Instructions ===')
for row in db.execute('SELECT * FROM instructions ORDER BY created_at DESC LIMIT 5'):
    print(dict(row))
"
```

## スクリプト

### disk_monitor.sh

ディスク使用率を監視（デフォルト: 80%で警告）

```bash
~/orchestrator/scripts/disk_monitor.sh
```

80%を超えると`outbox/disk_warning.json`に警告を出力

### scan_all_projects.sh

全プロジェクトの状態をスキャン

```bash
~/orchestrator/scripts/scan_all_projects.sh
```

結果は`outbox/scan_report_*.json`に出力

### startup.sh

Orchestratorをバックグラウンドで起動

```bash
~/orchestrator/scripts/startup.sh
```

PIDファイル: `~/orchestrator/orchestrator.pid`

## systemdサービス化

詳細は `INSTALL_SYSTEMD.md` を参照

```bash
# サービスファイルをコピー
sudo cp ~/orchestrator/orchestrator.service.template /etc/systemd/system/orchestrator.service

# 有効化して起動
sudo systemctl enable orchestrator.service
sudo systemctl start orchestrator.service

# 状態確認
sudo systemctl status orchestrator.service
```

## ログ

### アプリケーションログ

```bash
# 最新のログを確認
tail -f ~/orchestrator/logs/orchestrator_$(date +%Y%m%d).log

# すべてのログ
ls -lh ~/orchestrator/logs/
```

### systemd ログ

```bash
sudo journalctl -u orchestrator.service -f
```

## トラブルシューティング

### Orchestratorが起動しない

1. ログを確認
```bash
tail -n 50 ~/orchestrator/logs/orchestrator_*.log
```

2. 手動実行で動作確認
```bash
cd ~/orchestrator
python3 master.py
```

3. データベースを確認
```bash
ls -la ~/orchestrator/db/orchestrator.db
```

### 指示が処理されない

1. inboxにファイルがあるか確認
```bash
ls -la ~/orchestrator/inbox/
```

2. processed/に移動済みか確認
```bash
ls -la ~/orchestrator/inbox/processed/
```

3. データベースで指示の状態を確認
```bash
python3 -c "
import sqlite3
db = sqlite3.connect('$HOME/orchestrator/db/orchestrator.db')
for row in db.execute('SELECT * FROM instructions'):
    print(row)
"
```

### ディスク満杯警告

1. 警告ファイルを確認
```bash
cat ~/orchestrator/outbox/disk_warning.json
```

2. 手動でディスク使用率確認
```bash
df -h /
```

3. ログファイルをクリーンアップ
```bash
find ~/orchestrator/logs -name "*.log" -mtime +30 -delete
```

## 今後の実装予定

- [ ] タスクの実際の実行（現在は解析のみ）
- [ ] Claudeとの統合（AI判断による自律実行）
- [ ] GCP再起動の自動検知と復旧
- [ ] Slackやメールによる通知
- [ ] Web UIでの状態確認
- [ ] 複数タスクの並列実行
- [ ] タスクの優先度管理

## アーキテクチャ

```
[スマホ]
   ↓ (inbox/に指示ファイル配置)
[Orchestrator (master.py)]
   ├─ inbox監視 (10秒ごと)
   ├─ 指示解析
   ├─ タスク分解
   ├─ SQLiteに記録
   ├─ プロジェクトスキャン (60秒ごと)
   └─ 結果をoutbox/に出力
        ↓
   [スマホ] (outbox/から結果取得)
```

## ライセンス

MIT License

## 作者

Sora Kun (@sorakun_fukuoka)
