# systemd サービスのインストール手順

## 1. サービスファイルをコピー

```bash
sudo cp ~/orchestrator/orchestrator.service.template /etc/systemd/system/orchestrator.service
```

## 2. systemd をリロード

```bash
sudo systemctl daemon-reload
```

## 3. サービスを有効化（自動起動）

```bash
sudo systemctl enable orchestrator.service
```

## 4. サービスを起動

```bash
sudo systemctl start orchestrator.service
```

## 5. 状態確認

```bash
sudo systemctl status orchestrator.service
```

## 6. ログ確認

```bash
# systemd ログ
sudo journalctl -u orchestrator.service -f

# アプリケーションログ
tail -f ~/orchestrator/logs/orchestrator.log
```

## その他のコマンド

```bash
# サービス停止
sudo systemctl stop orchestrator.service

# サービス再起動
sudo systemctl restart orchestrator.service

# 自動起動を無効化
sudo systemctl disable orchestrator.service
```

## トラブルシューティング

### サービスが起動しない場合

1. ログを確認
```bash
sudo journalctl -u orchestrator.service -n 50
```

2. 手動実行で動作確認
```bash
python3 ~/orchestrator/master.py
```

3. 権限を確認
```bash
ls -la ~/orchestrator/master.py
chmod +x ~/orchestrator/master.py
```

### ユーザー名が違う場合

`orchestrator.service.template` の `User=` と `Group=` を編集してから再度コピー

## cron設定（ディスク監視）

ディスク監視を5分ごとに実行する場合:

```bash
crontab -e
```

以下を追加:
```
*/5 * * * * /home/sorakun_fukuoka/orchestrator/scripts/disk_monitor.sh
```
