# Google Drive 連携セットアップ

Claude.ai Web（Opus）からGCPの状態を把握できるようにするため、Google Driveに状態をアップロードします。

## 目的

- スマホからClaude.ai Webを開いて、GCPの状態を確認
- Orchestratorの全プロジェクト状態をJSON形式で共有
- ディスク使用率、最新タスク、エラーログなどを把握

## 前提条件

1. Googleアカウント
2. Google Cloud Projectの作成（無料）
3. Python環境

## セットアップ手順

### 1. Google Cloud Projectを作成

1. [Google Cloud Console](https://console.cloud.google.com/)にアクセス
2. 新しいプロジェクトを作成
   - プロジェクト名: `orchestrator-sync`
   - プロジェクトID: 任意（例: `orchestrator-sync-12345`）

### 2. Google Drive APIを有効化

1. Google Cloud Consoleで「APIとサービス」→「ライブラリ」
2. 「Google Drive API」を検索
3. 「有効にする」をクリック

### 3. サービスアカウントを作成

1. 「APIとサービス」→「認証情報」
2. 「認証情報を作成」→「サービスアカウント」
3. 以下を入力:
   - サービスアカウント名: `orchestrator-gdrive`
   - サービスアカウントID: `orchestrator-gdrive`
   - 説明: `Orchestrator Google Drive Sync`
4. 「作成して続行」
5. ロールは不要（スキップ）
6. 「完了」

### 4. 認証キーを作成してダウンロード

1. 作成したサービスアカウントをクリック
2. 「キー」タブ
3. 「鍵を追加」→「新しい鍵を作成」
4. キーのタイプ: JSON
5. 「作成」→ JSONファイルがダウンロードされる

### 5. 認証ファイルを配置

ダウンロードしたJSONファイルを `~/orchestrator/gdrive_credentials.json` として保存:

```bash
mv ~/Downloads/orchestrator-sync-*.json ~/orchestrator/gdrive_credentials.json
chmod 600 ~/orchestrator/gdrive_credentials.json
```

### 6. 必要なPythonパッケージをインストール

```bash
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

または:

```bash
pip install -r ~/orchestrator/requirements.txt
```

### 7. Google Driveでフォルダを共有（重要）

**初回実行後**に、Google Driveに `orchestrator_status` フォルダが作成されます。

このフォルダをサービスアカウントと共有する必要があります:

1. Google Driveを開く
2. `orchestrator_status` フォルダを右クリック→「共有」
3. サービスアカウントのメールアドレスを追加
   - メールアドレス: `orchestrator-gdrive@orchestrator-sync-12345.iam.gserviceaccount.com`
   - 役割: 編集者
4. 「送信」

**または、スクリプトで自動的に誰でも閲覧可能にする設定も実装済みです。**

## テスト実行

```bash
cd ~/orchestrator
python3 gdrive_sync.py
```

成功すると:
```
✓ Google Drive API初期化成功
✓ 1個のプロジェクト状態を収集
✓ ローカルステータス保存: /home/sorakun_fukuoka/orchestrator/outbox/orchestrator_status.json
✓ Google Driveファイル作成: orchestrator_status.json
✅ 同期完了
```

## Google Driveで確認

1. [Google Drive](https://drive.google.com/)を開く
2. `orchestrator_status` フォルダを開く
3. `orchestrator_status.json` ファイルが存在することを確認
4. ファイルを開いて内容を確認

## Claude.ai Webから状態を確認する方法

### 方法1: ファイルを直接アップロード

1. Claude.ai Webを開く
2. Google Driveから `orchestrator_status.json` をダウンロード
3. Claudeにアップロード
4. 「このJSONファイルの内容を要約して」と指示

### 方法2: 共有リンクを使用（推奨）

1. Google Driveで `orchestrator_status.json` を右クリック
2. 「リンクを取得」→「リンクをコピー」
3. Claude.ai Webで「このGoogle DriveのファイルのURLから内容を読み取って要約して: <URL>」

**注意**: Claude.ai WebはGoogle Drive URLから直接読み取れない場合があります。その場合は方法1を使用してください。

### 方法3: 自動通知（今後実装予定）

- SlackやDiscordに定期的に状態を通知
- 重要なイベント（ディスク満杯、エラー発生）を即座に通知

## 定期実行の設定

### cronで5分ごとに実行

```bash
crontab -e
```

以下を追加:
```
*/5 * * * * /usr/bin/python3 /home/sorakun_fukuoka/orchestrator/gdrive_sync.py >> /home/sorakun_fukuoka/orchestrator/logs/gdrive_sync_cron.log 2>&1
```

### master.pyから自動実行（推奨）

master.pyを更新して、gdrive_sync.pyを5分ごとに呼び出すように実装できます。

## orchestrator_status.json の内容

```json
{
  "timestamp": "2025-12-20T15:50:00.000000",
  "gcp_instance": "instance-name",
  "projects": [
    {
      "project_name": "idiom",
      "last_scanned": "2025-12-20T15:46:23.771596",
      "status": "idle",
      "last_commit": "dde7715...",
      "uncommitted_changes": 0,
      "recent_errors": [...]
    }
  ],
  "disk_usage": {
    "total_gb": 100,
    "used_gb": 26,
    "free_gb": 74,
    "usage_percent": 26.0,
    "warning": false
  },
  "recent_tasks": [...],
  "recent_instructions": [...],
  "system_health": "ok"
}
```

## トラブルシューティング

### 認証エラー

```
Google Drive API初期化エラー: ...
```

- `gdrive_credentials.json` が正しい場所にあるか確認
- ファイルの権限を確認（600推奨）
- サービスアカウントのキーが有効か確認

### アップロードエラー

```
Google Driveアップロードエラー: ...
```

- Google Drive APIが有効化されているか確認
- サービスアカウントに必要な権限があるか確認
- フォルダがサービスアカウントと共有されているか確認

### ライブラリがない

```
Google Drive APIライブラリがインストールされていません
```

以下を実行:
```bash
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

## セキュリティ注意事項

1. **認証ファイルの保護**
   ```bash
   chmod 600 ~/orchestrator/gdrive_credentials.json
   ```

2. **gitにコミットしない**
   `.gitignore` に追加:
   ```
   gdrive_credentials.json
   orchestrator_status.json
   ```

3. **サービスアカウントの権限を最小限に**
   - Drive APIのみ有効化
   - 他のAPIは無効化

4. **定期的な監査**
   - Google Cloud Consoleで使用状況を確認
   - 不要なサービスアカウントは削除

## 料金について

- Google Drive API: 無料（制限内）
- 1日あたり1,000,000リクエストまで無料
- 5分ごとの同期 = 1日288リクエスト（無料範囲内）

詳細: [Google Drive API Pricing](https://developers.google.com/drive/api/guides/limits)

## 今後の拡張

- [ ] 複数のステータスファイルを保持（履歴機能）
- [ ] グラフや可視化データの生成
- [ ] Slackへの自動通知
- [ ] Webダッシュボード
- [ ] アラート機能（ディスク満杯、エラー急増など）
