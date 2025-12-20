#!/usr/bin/env python3
"""
Supabase Sync - Orchestrator状態をSupabaseに同期

GCPのorchestrator状態をSupabaseのorch_*テーブルに保存し、
どのClaudeからでも状態を把握できるようにする
"""

import os
import sys
import json
import sqlite3
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

# python-dotenvで環境変数を読み込み
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

# Supabase SDK
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False


class SupabaseSync:
    """Supabase同期クラス"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = {}
        self.db_path = None
        self.logger = self._setup_logging()
        self.supabase: Optional[Client] = None

    def _setup_logging(self) -> logging.Logger:
        """ロギングを設定"""
        logger = logging.getLogger('SupabaseSync')
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
        log_file = log_dir / f"supabase_sync_{datetime.now().strftime('%Y%m%d')}.log"

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

    def initialize_supabase(self) -> bool:
        """Supabase APIを初期化"""
        if not SUPABASE_AVAILABLE:
            self.logger.warning("Supabase SDKがインストールされていません")
            self.logger.info("インストール: pip install supabase python-dotenv")
            return False

        # 環境変数から認証情報を取得
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_KEY')

        if not supabase_url or not supabase_key:
            self.logger.warning("Supabase認証情報が環境変数に設定されていません")
            self.logger.info("必要な環境変数: SUPABASE_URL, SUPABASE_KEY")
            self.logger.info(".envファイルを確認してください")
            return False

        try:
            # Supabaseクライアントを作成
            self.supabase = create_client(supabase_url, supabase_key)
            self.logger.info("✓ Supabase API初期化成功")
            return True

        except Exception as e:
            self.logger.error(f"Supabase API初期化エラー: {e}")
            return False

    def collect_project_states(self) -> List[Dict[str, Any]]:
        """全プロジェクトの状態データを収集"""
        self.logger.info("プロジェクト状態を収集中...")
        project_states = []

        for project in self.config['projects']:
            project_name = project['name']
            project_path = Path(project['path'])

            try:
                # Git情報を取得
                git_branch = self._get_git_branch(project_path)
                git_last_commit = self._get_git_last_commit(project_path)
                git_uncommitted = self._get_git_uncommitted_count(project_path)

                # ディスク使用率
                disk_usage = self._get_disk_usage()

                # 状態データを構築
                state = {
                    'project_id': project_name,
                    'git_branch': git_branch,
                    'git_last_commit': git_last_commit,
                    'git_uncommitted_changes': git_uncommitted,
                    'recent_errors': [],  # TODO: ログから取得
                    'current_focus': None,  # TODO: 実装
                    'next_steps': [],  # TODO: 実装
                    'blockers': [],  # TODO: 実装
                    'disk_usage_percent': disk_usage.get('usage_percent', 0.0)
                }

                project_states.append(state)
                self.logger.info(f"✓ {project_name}: {git_branch} ({git_uncommitted}件の未コミット変更)")

            except Exception as e:
                self.logger.error(f"プロジェクト状態収集エラー ({project_name}): {e}")

        return project_states

    def _get_git_branch(self, project_path: Path) -> Optional[str]:
        """Gitブランチ名を取得"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except:
            return None

    def _get_git_last_commit(self, project_path: Path) -> Optional[str]:
        """最新コミットハッシュを取得"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--short', 'HEAD'],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except:
            return None

    def _get_git_uncommitted_count(self, project_path: Path) -> int:
        """未コミット変更の数を取得"""
        try:
            result = subprocess.run(
                ['git', 'status', '--porcelain'],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return len([line for line in result.stdout.split('\n') if line.strip()])
            return 0
        except:
            return 0

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
                'usage_percent': round(usage_percent, 2)
            }
        except Exception as e:
            self.logger.error(f"ディスク使用状況取得エラー: {e}")
            return {'error': str(e), 'usage_percent': 0.0}

    def sync_to_supabase(self, project_states: List[Dict[str, Any]]) -> bool:
        """Supabaseにプロジェクト状態を同期"""
        if not self.supabase:
            self.logger.warning("Supabase API が初期化されていません")
            return False

        try:
            # 各プロジェクトの状態をINSERT
            for state in project_states:
                self.supabase.table('orch_project_states').insert(state).execute()
                self.logger.info(f"✓ {state['project_id']} の状態をSupabaseに保存")

            # 古いレコードを削除（最新100件だけ保持）
            self._cleanup_old_records()

            self.logger.info("✅ Supabase同期完了")
            return True

        except Exception as e:
            self.logger.error(f"Supabase同期エラー: {e}")
            return False

    def _cleanup_old_records(self):
        """古いレコードを削除"""
        try:
            # 各プロジェクトごとに最新100件を保持
            for project in self.config['projects']:
                project_id = project['name']

                # 全レコードを取得してID順にソート
                response = self.supabase.table('orch_project_states') \
                    .select('id') \
                    .eq('project_id', project_id) \
                    .order('scanned_at', desc=True) \
                    .execute()

                if len(response.data) > 100:
                    # 100件より古いものを削除
                    ids_to_delete = [record['id'] for record in response.data[100:]]
                    for id_to_delete in ids_to_delete:
                        self.supabase.table('orch_project_states') \
                            .delete() \
                            .eq('id', id_to_delete) \
                            .execute()

                    self.logger.info(f"✓ {project_id}: {len(ids_to_delete)}件の古いレコードを削除")

        except Exception as e:
            self.logger.error(f"古いレコード削除エラー: {e}")

    def sync(self) -> bool:
        """同期を実行"""
        self.logger.info("="*60)
        self.logger.info("Supabase 同期開始")
        self.logger.info("="*60)

        try:
            # 設定読み込み
            self.load_config()

            # Supabase初期化
            if not self.initialize_supabase():
                return False

            # プロジェクト状態を収集
            project_states = self.collect_project_states()

            if not project_states:
                self.logger.warning("プロジェクト状態が収集できませんでした")
                return False

            # Supabaseに同期
            success = self.sync_to_supabase(project_states)

            if success:
                self.logger.info("✅ 同期完了")
            else:
                self.logger.warning("⚠️  同期失敗")

            return success

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

    syncer = SupabaseSync(str(config_path))
    success = syncer.sync()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
