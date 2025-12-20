#!/usr/bin/env python3
"""
Google Drive Sync - Orchestrator状態をGoogle Driveにアップロード

Claude.ai Web（Opus）からGCPの状態を把握できるようにする
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Google Drive API（オプショナル）
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GDRIVE_AVAILABLE = True
except ImportError:
    GDRIVE_AVAILABLE = False


class GDriveSync:
    """Google Drive同期クラス"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = {}
        self.db_path = None
        self.logger = self._setup_logging()
        self.service = None

    def _setup_logging(self) -> logging.Logger:
        """ロギングを設定"""
        logger = logging.getLogger('GDriveSync')
        logger.setLevel(logging.DEBUG)

        # コンソールハンドラ
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # ファイルハンドラ
        log_dir = Path.home() / "orchestrator" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"gdrive_sync_{datetime.now().strftime('%Y%m%d')}.log"

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        return logger

    def load_config(self):
        """設定ファイルを読み込む"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            self.db_path = self.config['paths']['db']
            self.logger.info(f"設定ファイルを読み込みました: {self.config_path}")
        except Exception as e:
            self.logger.error(f"設定ファイルの読み込みエラー: {e}")
            raise

    def initialize_gdrive(self) -> bool:
        """Google Drive APIを初期化"""
        if not GDRIVE_AVAILABLE:
            self.logger.warning("Google Drive APIライブラリがインストールされていません")
            self.logger.info("インストール: pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client")
            return False

        # 認証ファイルのパス
        creds_path = Path.home() / "orchestrator" / "gdrive_credentials.json"

        if not creds_path.exists():
            self.logger.warning(f"認証ファイルが見つかりません: {creds_path}")
            self.logger.info("セットアップ手順については GDRIVE_SETUP.md を参照してください")
            return False

        try:
            # サービスアカウント認証
            credentials = service_account.Credentials.from_service_account_file(
                str(creds_path),
                scopes=['https://www.googleapis.com/auth/drive.file']
            )

            # Drive APIサービスを構築
            self.service = build('drive', 'v3', credentials=credentials)
            self.logger.info("✓ Google Drive API初期化成功")
            return True

        except Exception as e:
            self.logger.error(f"Google Drive API初期化エラー: {e}")
            return False

    def collect_status_data(self) -> Dict[str, Any]:
        """全プロジェクトの状態データを収集"""
        self.logger.info("状態データを収集中...")

        status_data = {
            'timestamp': datetime.now().isoformat(),
            'gcp_instance': os.uname().nodename,
            'projects': [],
            'disk_usage': self._get_disk_usage(),
            'recent_tasks': [],
            'recent_instructions': [],
            'system_health': 'ok'
        }

        # データベースから情報を取得
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            # プロジェクト状態
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM project_states')
            for row in cursor.fetchall():
                project = dict(row)
                # JSON文字列をパース
                if project.get('recent_errors'):
                    try:
                        project['recent_errors'] = json.loads(project['recent_errors'])
                    except:
                        pass
                status_data['projects'].append(project)

            # 最新のタスク履歴（10件）
            cursor.execute('''
                SELECT * FROM task_history
                ORDER BY started_at DESC
                LIMIT 10
            ''')
            status_data['recent_tasks'] = [dict(row) for row in cursor.fetchall()]

            # 最新の指示（5件）
            cursor.execute('''
                SELECT * FROM instructions
                ORDER BY created_at DESC
                LIMIT 5
            ''')
            status_data['recent_instructions'] = [dict(row) for row in cursor.fetchall()]

            conn.close()

            self.logger.info(f"✓ {len(status_data['projects'])}個のプロジェクト状態を収集")

        except Exception as e:
            self.logger.error(f"データベース読み込みエラー: {e}")
            status_data['system_health'] = 'error'
            status_data['error'] = str(e)

        return status_data

    def _get_disk_usage(self) -> Dict[str, Any]:
        """ディスク使用状況を取得"""
        try:
            import shutil
            stat = shutil.disk_usage('/')
            usage_percent = (stat.used / stat.total) * 100

            return {
                'total_gb': round(stat.total / (1024**3), 2),
                'used_gb': round(stat.used / (1024**3), 2),
                'free_gb': round(stat.free / (1024**3), 2),
                'usage_percent': round(usage_percent, 2),
                'warning': usage_percent > self.config['settings']['disk_warning_threshold']
            }
        except Exception as e:
            self.logger.error(f"ディスク使用状況取得エラー: {e}")
            return {'error': str(e)}

    def save_local_status(self, status_data: Dict[str, Any]) -> Path:
        """ローカルにステータスファイルを保存"""
        outbox = Path(self.config['paths']['outbox'])
        status_file = outbox / "orchestrator_status.json"

        try:
            with open(status_file, 'w', encoding='utf-8') as f:
                json.dump(status_data, f, indent=2, ensure_ascii=False)

            self.logger.info(f"✓ ローカルステータス保存: {status_file}")
            return status_file

        except Exception as e:
            self.logger.error(f"ローカルステータス保存エラー: {e}")
            raise

    def upload_to_gdrive(self, local_file: Path) -> bool:
        """Google Driveにアップロード"""
        if not self.service:
            self.logger.warning("Google Drive API が初期化されていません")
            return False

        try:
            folder_name = self.config['settings']['gdrive_folder_name']

            # フォルダを探す（なければ作成）
            folder_id = self._get_or_create_folder(folder_name)

            # 既存のファイルを検索
            file_name = "orchestrator_status.json"
            existing_file = self._find_file(file_name, folder_id)

            # メタデータ
            file_metadata = {
                'name': file_name,
                'parents': [folder_id]
            }

            # メディア
            media = MediaFileUpload(
                str(local_file),
                mimetype='application/json',
                resumable=True
            )

            if existing_file:
                # 既存ファイルを更新
                file = self.service.files().update(
                    fileId=existing_file['id'],
                    media_body=media
                ).execute()
                self.logger.info(f"✓ Google Driveファイル更新: {file_name}")
            else:
                # 新規ファイル作成
                file = self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id,name,webViewLink'
                ).execute()
                self.logger.info(f"✓ Google Driveファイル作成: {file_name}")

            # 共有リンクを取得
            if 'webViewLink' in file:
                self.logger.info(f"   リンク: {file['webViewLink']}")
            else:
                # 共有設定を追加（誰でも閲覧可能）
                try:
                    self.service.permissions().create(
                        fileId=file['id'],
                        body={'type': 'anyone', 'role': 'reader'}
                    ).execute()
                    self.logger.info("   共有設定: 誰でも閲覧可能")
                except:
                    pass

            return True

        except Exception as e:
            self.logger.error(f"Google Driveアップロードエラー: {e}")
            return False

    def _get_or_create_folder(self, folder_name: str) -> str:
        """フォルダを取得または作成"""
        # 既存フォルダを検索
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = self.service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()

        files = results.get('files', [])

        if files:
            return files[0]['id']

        # フォルダを作成
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = self.service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()

        self.logger.info(f"✓ フォルダ作成: {folder_name}")
        return folder['id']

    def _find_file(self, file_name: str, folder_id: str) -> Optional[Dict]:
        """フォルダ内のファイルを検索"""
        query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
        results = self.service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name, webViewLink)'
        ).execute()

        files = results.get('files', [])
        return files[0] if files else None

    def sync(self) -> bool:
        """同期を実行"""
        self.logger.info("="*60)
        self.logger.info("Google Drive 同期開始")
        self.logger.info("="*60)

        try:
            # 設定読み込み
            self.load_config()

            # Google Drive同期が無効の場合
            if not self.config['settings'].get('gdrive_sync_enabled', False):
                self.logger.info("Google Drive同期は無効化されています")
                return False

            # 状態データを収集
            status_data = self.collect_status_data()

            # ローカルに保存
            local_file = self.save_local_status(status_data)

            # Google Driveにアップロード
            if self.initialize_gdrive():
                success = self.upload_to_gdrive(local_file)
                if success:
                    self.logger.info("✅ 同期完了")
                    return True
                else:
                    self.logger.warning("⚠️  Google Driveアップロード失敗（ローカル保存は成功）")
                    return False
            else:
                self.logger.warning("⚠️  Google Drive API初期化失敗（ローカル保存は成功）")
                return False

        except Exception as e:
            self.logger.error(f"同期エラー: {e}")
            return False


def main():
    """メイン関数"""
    orchestrator_dir = Path.home() / "orchestrator"
    config_path = orchestrator_dir / "config.json"

    if not config_path.exists():
        print(f"❌ 設定ファイルが見つかりません: {config_path}")
        sys.exit(1)

    syncer = GDriveSync(str(config_path))
    success = syncer.sync()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
