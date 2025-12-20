#!/usr/bin/env python3
"""
Master Orchestrator - 自律型プロジェクトオーケストレーター

複数のプロジェクトを監視し、指示を受け取って自律的にタスクを実行する
"""

import os
import sys
import json
import sqlite3
import time
import logging
import signal
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# python-dotenvで環境変数を読み込み
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

# Supabase SDK (オプショナル)
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False


class OrchestratorDB:
    """データベース操作クラス"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self.logger = logging.getLogger('OrchestratorDB')

    def connect(self):
        """データベースに接続"""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.logger.info(f"データベース接続成功: {self.db_path}")
            self._initialize_schema()
        except Exception as e:
            self.logger.error(f"データベース接続エラー: {e}")
            raise

    def _initialize_schema(self):
        """スキーマを初期化"""
        schema_file = Path(self.db_path).parent / "init_schema.sql"
        if schema_file.exists():
            with open(schema_file, 'r', encoding='utf-8') as f:
                self.conn.executescript(f.read())
            self.conn.commit()
            self.logger.info("データベーススキーマを初期化しました")

    def close(self):
        """データベース接続を閉じる"""
        if self.conn:
            self.conn.close()
            self.logger.info("データベース接続を閉じました")

    def upsert_project_state(self, state: Dict[str, Any]):
        """プロジェクト状態を挿入または更新"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO project_states
                (project_name, last_scanned, status, current_task, last_commit,
                 uncommitted_changes, recent_errors, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                state['project_name'],
                state.get('last_scanned'),
                state.get('status', 'idle'),
                state.get('current_task'),
                state.get('last_commit'),
                state.get('uncommitted_changes', 0),
                json.dumps(state.get('recent_errors', []), ensure_ascii=False),
                datetime.now().isoformat()
            ))
            self.conn.commit()
            self.logger.debug(f"プロジェクト状態を更新: {state['project_name']}")
        except Exception as e:
            self.logger.error(f"プロジェクト状態の更新エラー: {e}")

    def add_instruction(self, instruction: str) -> int:
        """新しい指示を追加"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO instructions (raw_instruction, status, created_at)
                VALUES (?, ?, ?)
            ''', (instruction, 'pending', datetime.now().isoformat()))
            self.conn.commit()
            self.logger.info(f"新しい指示を追加: ID={cursor.lastrowid}")
            return cursor.lastrowid
        except Exception as e:
            self.logger.error(f"指示の追加エラー: {e}")
            return -1

    def get_pending_instructions(self) -> List[Dict[str, Any]]:
        """未処理の指示を取得"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM instructions WHERE status = 'pending' ORDER BY created_at ASC
        ''')
        return [dict(row) for row in cursor.fetchall()]

    def update_instruction_status(self, instruction_id: int, status: str,
                                   parsed_tasks: Optional[str] = None,
                                   result: Optional[str] = None):
        """指示の状態を更新"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                UPDATE instructions
                SET status = ?, parsed_tasks = ?, result = ?, processed_at = ?
                WHERE id = ?
            ''', (status, parsed_tasks, result, datetime.now().isoformat(), instruction_id))
            self.conn.commit()
            self.logger.debug(f"指示状態を更新: ID={instruction_id}, status={status}")
        except Exception as e:
            self.logger.error(f"指示状態の更新エラー: {e}")

    def add_system_event(self, event_type: str, severity: str, message: str,
                        details: Optional[Dict] = None):
        """システムイベントを記録"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO system_events (event_type, severity, message, details, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                event_type,
                severity,
                message,
                json.dumps(details, ensure_ascii=False) if details else None,
                datetime.now().isoformat()
            ))
            self.conn.commit()
        except Exception as e:
            self.logger.error(f"システムイベントの記録エラー: {e}")

    def get_project_state(self, project_name: str) -> Optional[Dict[str, Any]]:
        """プロジェクト状態を取得"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM project_states WHERE project_name = ?', (project_name,))
        row = cursor.fetchone()
        return dict(row) if row else None


class Orchestrator:
    """メインオーケストレータークラス"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = {}
        self.db = None
        self.running = False
        self.logger = self._setup_logging()
        self.supabase = None
        self._initialize_supabase()

    def _setup_logging(self) -> logging.Logger:
        """ロギングを設定"""
        logger = logging.getLogger('Orchestrator')
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
        log_file = log_dir / f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log"

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        return logger

    def _initialize_supabase(self):
        """Supabaseクライアントを初期化（オプショナル）"""
        if not SUPABASE_AVAILABLE:
            return

        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_KEY')

        if supabase_url and supabase_key:
            try:
                self.supabase = create_client(supabase_url, supabase_key)
                self.logger.info("✓ Supabase連携有効")
            except Exception as e:
                self.logger.warning(f"Supabase初期化エラー: {e}")

    def _save_task_to_supabase(self, task: Dict[str, Any], instruction_id: int):
        """タスクをSupabaseのorch_tasksに保存"""
        if not self.supabase:
            return

        try:
            task_data = {
                'project_id': task.get('project'),
                'title': task.get('description'),
                'description': task.get('description'),
                'why': f"Instruction ID: {instruction_id}",
                'status': 'pending',
                'priority': 'normal',
                'estimated_hours': None,
                'actual_hours': None,
                'blockers': [],
                'dependencies': []
            }

            self.supabase.table('orch_tasks').insert(task_data).execute()
            self.logger.debug(f"✓ タスクをSupabaseに保存: {task.get('description')}")

        except Exception as e:
            self.logger.warning(f"Supabaseタスク保存エラー: {e}")

    def load_config(self):
        """設定ファイルを読み込む"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            self.logger.info(f"設定ファイルを読み込みました: {self.config_path}")
            self.logger.info(f"管理プロジェクト数: {len(self.config['projects'])}")
        except Exception as e:
            self.logger.error(f"設定ファイルの読み込みエラー: {e}")
            raise

    def initialize(self):
        """初期化処理"""
        self.logger.info("="*60)
        self.logger.info("Orchestrator 初期化開始")
        self.logger.info("="*60)

        # 設定読み込み
        self.load_config()

        # データベース接続
        db_path = self.config['paths']['db']
        self.db = OrchestratorDB(db_path)
        self.db.connect()

        # システムイベント記録
        self.db.add_system_event('startup', 'info', 'Orchestrator started')

        # プロジェクト状態の初期読み込み
        self._load_project_states()

        self.logger.info("初期化完了")

    def _load_project_states(self):
        """各プロジェクトの状態を読み込む"""
        for project in self.config['projects']:
            project_name = project['name']
            project_path = Path(project['path'])
            state_file = project_path / "PROJECT_STATE.json"

            try:
                if state_file.exists():
                    with open(state_file, 'r', encoding='utf-8') as f:
                        state_data = json.load(f)

                    # データベースに保存
                    project_state = {
                        'project_name': project_name,
                        'last_scanned': state_data.get('scan_timestamp'),
                        'status': 'idle',
                        'last_commit': state_data.get('git_status', {}).get('latest_commit', {}).get('hash'),
                        'uncommitted_changes': len(state_data.get('git_status', {}).get('uncommitted_changes', [])),
                        'recent_errors': state_data.get('recent_logs', {}).get('recent_errors', [])
                    }
                    self.db.upsert_project_state(project_state)
                    self.logger.info(f"✓ プロジェクト状態を読み込み: {project_name}")
                else:
                    self.logger.warning(f"⚠️  PROJECT_STATE.json が見つかりません: {project_name}")
            except Exception as e:
                self.logger.error(f"プロジェクト状態の読み込みエラー ({project_name}): {e}")

    def check_inbox(self):
        """inboxに新しい指示がないかチェック"""
        inbox_path = Path(self.config['paths']['inbox'])

        for file_path in inbox_path.glob('*.json'):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                instruction = data.get('instruction')
                if instruction:
                    self.logger.info(f"📨 新しい指示を受信: {file_path.name}")
                    self.logger.info(f"   内容: {instruction}")

                    # データベースに保存
                    instruction_id = self.db.add_instruction(instruction)

                    # 処理
                    self.process_instruction(instruction_id, instruction)

                    # 処理済みファイルを移動
                    processed_dir = inbox_path / "processed"
                    processed_dir.mkdir(exist_ok=True)
                    file_path.rename(processed_dir / file_path.name)

            except Exception as e:
                self.logger.error(f"指示ファイルの処理エラー ({file_path.name}): {e}")

    def process_instruction(self, instruction_id: int, instruction: str):
        """指示を処理してタスクに分解"""
        self.logger.info(f"📋 指示を処理中: ID={instruction_id}")

        try:
            # 指示を解析（簡易実装）
            parsed_tasks = self._parse_instruction(instruction)

            # データベースに保存
            self.db.update_instruction_status(
                instruction_id,
                'processing',
                json.dumps(parsed_tasks, ensure_ascii=False)
            )

            # Supabaseにタスクを保存（オプショナル）
            for task in parsed_tasks:
                self._save_task_to_supabase(task, instruction_id)

            # 結果をoutboxに出力
            self._output_result(instruction_id, instruction, parsed_tasks)

            # 完了
            self.db.update_instruction_status(
                instruction_id,
                'done',
                result='Tasks parsed and output to outbox'
            )

            self.logger.info(f"✅ 指示処理完了: ID={instruction_id}")

        except Exception as e:
            self.logger.error(f"指示処理エラー: {e}")
            self.db.update_instruction_status(
                instruction_id,
                'failed',
                result=f"Error: {str(e)}"
            )

    def _parse_instruction(self, instruction: str) -> List[Dict[str, Any]]:
        """指示を解析してタスクに分解"""
        tasks = []
        instruction_lower = instruction.lower()

        # プロジェクト名の抽出
        project_names = [p['name'] for p in self.config['projects']]
        target_project = None

        for project_name in project_names:
            if project_name in instruction_lower:
                target_project = project_name
                break

        # タスクの推測
        if '状態' in instruction or 'status' in instruction_lower:
            tasks.append({
                'type': 'check_status',
                'project': target_project,
                'description': f'{target_project}プロジェクトの状態を確認'
            })

        if 'コミット' in instruction or 'commit' in instruction_lower:
            tasks.append({
                'type': 'git_commit',
                'project': target_project,
                'description': f'{target_project}の変更をコミット'
            })

        if 'todo' in instruction_lower:
            tasks.append({
                'type': 'organize_todos',
                'project': target_project,
                'description': f'{target_project}のTODOを整理'
            })

        if not tasks:
            tasks.append({
                'type': 'unknown',
                'project': target_project,
                'description': '指示の内容が不明です'
            })

        return tasks

    def _output_result(self, instruction_id: int, instruction: str,
                      parsed_tasks: List[Dict[str, Any]]):
        """処理結果をoutboxに出力"""
        outbox_path = Path(self.config['paths']['outbox'])
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = outbox_path / f"result_{instruction_id}_{timestamp}.json"

        result = {
            'instruction_id': instruction_id,
            'instruction': instruction,
            'parsed_tasks': parsed_tasks,
            'processed_at': datetime.now().isoformat(),
            'status': 'Tasks identified but not executed yet (state management only)'
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        self.logger.info(f"📤 結果を出力: {output_file.name}")

    def scan_projects(self):
        """全プロジェクトの状態をスキャン"""
        for project in self.config['projects']:
            if not project.get('auto_scan', True):
                continue

            project_name = project['name']
            project_path = Path(project['path'])

            # scan_project.pyが存在すればそれを実行
            scan_script = project_path / "scan_project.py"
            if scan_script.exists():
                try:
                    import subprocess
                    result = subprocess.run(
                        ['python3', str(scan_script)],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )

                    if result.returncode == 0:
                        self.logger.debug(f"✓ スキャン完了: {project_name}")
                        # 状態を再読み込み
                        self._load_project_states()
                    else:
                        self.logger.warning(f"スキャンエラー ({project_name}): {result.stderr}")

                except Exception as e:
                    self.logger.error(f"スキャン実行エラー ({project_name}): {e}")

    def run(self):
        """メインループ"""
        self.running = True
        scan_interval = self.config['settings']['scan_interval_seconds']
        inbox_interval = self.config['settings'].get('inbox_check_interval', 10)

        last_scan = 0
        last_inbox_check = 0

        self.logger.info("="*60)
        self.logger.info("Orchestrator メインループ開始")
        self.logger.info(f"スキャン間隔: {scan_interval}秒")
        self.logger.info(f"inbox確認間隔: {inbox_interval}秒")
        self.logger.info("="*60)

        try:
            while self.running:
                current_time = time.time()

                # inbox確認
                if current_time - last_inbox_check >= inbox_interval:
                    self.check_inbox()
                    last_inbox_check = current_time

                # プロジェクトスキャン
                if current_time - last_scan >= scan_interval:
                    self.logger.info("🔍 定期スキャン実行中...")
                    self.scan_projects()
                    last_scan = current_time

                # 未処理の指示を処理
                pending = self.db.get_pending_instructions()
                for instruction in pending:
                    self.process_instruction(
                        instruction['id'],
                        instruction['raw_instruction']
                    )

                # 短いスリープ
                time.sleep(1)

        except KeyboardInterrupt:
            self.logger.info("キーボード割り込みを受信しました")
        except Exception as e:
            self.logger.error(f"メインループエラー: {e}")
        finally:
            self.shutdown()

    def shutdown(self):
        """シャットダウン処理"""
        self.logger.info("="*60)
        self.logger.info("Orchestrator シャットダウン中...")
        self.logger.info("="*60)

        if self.db:
            self.db.add_system_event('shutdown', 'info', 'Orchestrator stopped')
            self.db.close()

        self.running = False
        self.logger.info("シャットダウン完了")


def main():
    """メイン関数"""
    orchestrator_dir = Path.home() / "orchestrator"
    config_path = orchestrator_dir / "config.json"

    if not config_path.exists():
        print(f"❌ 設定ファイルが見つかりません: {config_path}")
        sys.exit(1)

    orchestrator = Orchestrator(str(config_path))

    # シグナルハンドラ設定
    def signal_handler(sig, frame):
        print("\n割り込みシグナルを受信しました")
        orchestrator.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        orchestrator.initialize()
        orchestrator.run()
    except Exception as e:
        print(f"❌ 致命的エラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
