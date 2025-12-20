#!/usr/bin/env python3
"""
Task Executor - orch_tasksからpendingタスクを検知して自動実行

orchestrator-dashboardから投入された指示を検知し、
Claude Codeを起動してタスクを実行する。
"""

import os
import sys
import time
import json
import re
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

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
    print("⚠️  Supabase SDKがインストールされていません")
    sys.exit(1)


class ToolCallParser:
    """Claude Code出力からツール呼び出しを解析"""

    # ツール呼び出しパターン
    PATTERNS = {
        'Read': [
            r'Reading file[:\s]+([^\n]+)',
            r'Read\s+tool.*file_path[:\s]+([^\n]+)',
            r'cat\s+-n\s+([^\s]+)',
        ],
        'Write': [
            r'Writing to file[:\s]+([^\n]+)',
            r'Write\s+tool.*file_path[:\s]+([^\n]+)',
            r'Created file[:\s]+([^\n]+)',
        ],
        'Edit': [
            r'Editing file[:\s]+([^\n]+)',
            r'Edit\s+tool.*file_path[:\s]+([^\n]+)',
            r'Modified file[:\s]+([^\n]+)',
        ],
        'Bash': [
            r'Running command[:\s]+(.+?)(?:\n|$)',
            r'Bash\s+tool.*command[:\s]+(.+?)(?:\n|$)',
            r'Executing[:\s]+(.+?)(?:\n|$)',
        ],
        'Glob': [
            r'Searching for files matching[:\s]+([^\n]+)',
            r'Glob\s+tool.*pattern[:\s]+([^\n]+)',
            r'Finding files[:\s]+([^\n]+)',
        ],
        'Grep': [
            r'Searching for pattern[:\s]+([^\n]+)',
            r'Grep\s+tool.*pattern[:\s]+([^\n]+)',
            r'Grepping for[:\s]+([^\n]+)',
        ]
    }

    @classmethod
    def parse(cls, output: str) -> list[dict]:
        """
        Claude Code出力からツール呼び出しを抽出

        Returns:
            List of tool calls with format:
            [
                {
                    'tool_name': 'Read',
                    'parameters': {'file_path': '/path/to/file'},
                    'success': True,
                    'sequence_number': 0
                },
                ...
            ]
        """
        tool_calls = []
        sequence_number = 0

        for tool_name, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                matches = re.finditer(pattern, output, re.MULTILINE | re.IGNORECASE)
                for match in matches:
                    param_value = match.group(1).strip()

                    # パラメータを構築
                    parameters = {}
                    if tool_name in ['Read', 'Write', 'Edit']:
                        parameters['file_path'] = param_value
                    elif tool_name == 'Bash':
                        parameters['command'] = param_value
                    elif tool_name == 'Glob':
                        parameters['pattern'] = param_value
                    elif tool_name == 'Grep':
                        parameters['pattern'] = param_value

                    # ツール呼び出しを記録
                    tool_calls.append({
                        'tool_name': tool_name,
                        'parameters': parameters,
                        'success': True,  # 出力に含まれている = 実行された
                        'sequence_number': sequence_number,
                        'category': cls._categorize_tool(tool_name)
                    })
                    sequence_number += 1

        return tool_calls

    @staticmethod
    def _categorize_tool(tool_name: str) -> str:
        """ツールをカテゴリ分類"""
        if tool_name in ['Read', 'Write', 'Edit']:
            return 'file_operation'
        elif tool_name == 'Bash':
            return 'command_execution'
        elif tool_name in ['Glob', 'Grep']:
            return 'search'
        else:
            return 'other'


class TaskExecutor:
    """タスク実行エンジン"""

    def __init__(self):
        self.logger = self._setup_logging()
        self.supabase: Optional[Client] = None
        self.projects_dir = Path.home() / 'projects'
        self.current_task_id: Optional[int] = None

    def _setup_logging(self) -> logging.Logger:
        """ロギングを設定"""
        logger = logging.getLogger('TaskExecutor')
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
        log_file = log_dir / f"executor_{datetime.now().strftime('%Y%m%d')}.log"

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        return logger

    def initialize_supabase(self) -> bool:
        """Supabase APIを初期化"""
        if not SUPABASE_AVAILABLE:
            self.logger.error("Supabase SDKがインストールされていません")
            return False

        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_KEY')

        if not supabase_url or not supabase_key:
            self.logger.error("Supabase認証情報が環境変数に設定されていません")
            return False

        try:
            self.supabase = create_client(supabase_url, supabase_key)
            self.logger.info("✓ Supabase接続成功")
            return True
        except Exception as e:
            self.logger.error(f"Supabase接続エラー: {e}")
            return False

    def get_pending_tasks(self) -> list:
        """pendingタスクを取得"""
        try:
            response = self.supabase.table('orch_tasks').select('*').eq('status', 'pending').order('created_at').execute()
            return response.data or []
        except Exception as e:
            self.logger.error(f"タスク取得エラー: {e}")
            return []

    def update_task_status(self, task_id: int, status: str, completion_note: Optional[str] = None):
        """タスクのステータスを更新"""
        try:
            update_data = {'status': status}

            if status in ['done', 'failed']:
                update_data['completed_at'] = datetime.now().isoformat()

            if completion_note:
                update_data['completion_note'] = completion_note

            self.supabase.table('orch_tasks').update(update_data).eq('id', task_id).execute()
            self.logger.info(f"タスク#{task_id}のステータスを'{status}'に更新")
        except Exception as e:
            self.logger.error(f"タスク更新エラー: {e}")

    def save_suggestions(self, project_id: str, output: str):
        """Claude Codeの出力から提案を抽出してorch_suggestionsに保存"""
        try:
            import re

            # ```suggestions ... ``` ブロックを抽出
            match = re.search(r'```suggestions\s*\n(.*?)\n```', output, re.DOTALL)
            if not match:
                self.logger.debug("提案ブロックが見つかりませんでした")
                return

            suggestions_text = match.group(1)

            # 各行を解析（例: "1. タイトル - 説明"）
            lines = suggestions_text.strip().split('\n')
            for line in lines:
                # "数字. タイトル - 説明" の形式を解析
                suggestion_match = re.match(r'^\d+\.\s*(.+?)\s*-\s*(.+)$', line.strip())
                if suggestion_match:
                    title = suggestion_match.group(1).strip()
                    description = suggestion_match.group(2).strip()

                    # orch_suggestionsに保存
                    self.supabase.table('orch_suggestions').insert({
                        'project_id': project_id,
                        'title': title,
                        'description': description,
                        'source': 'ai_proposal',
                        'priority': 0,
                        'created_by': 'claude_code'
                    }).execute()

                    self.logger.info(f"提案を保存: {title}")

        except Exception as e:
            self.logger.error(f"提案保存エラー: {e}")

    def save_project_summary(self, project_id: str, output: str):
        """Claude Codeの出力からプロジェクトサマリーを抽出してorch_project_summariesに保存"""
        try:
            import re

            # ```summary ... ``` ブロックを抽出
            match = re.search(r'```summary\s*\n(.*?)\n```', output, re.DOTALL)
            if not match:
                self.logger.debug("サマリーブロックが見つかりませんでした")
                return

            summary_text = match.group(1)

            # 各行を解析
            current_status = ""
            next_milestone = ""
            recent_progress = ""

            for line in summary_text.strip().split('\n'):
                if line.startswith('現在の状態:'):
                    current_status = line.replace('現在の状態:', '').strip()
                elif line.startswith('次の予定:'):
                    next_milestone = line.replace('次の予定:', '').strip()
                elif line.startswith('最近の進捗:'):
                    recent_progress = line.replace('最近の進捗:', '').strip()

            # orch_project_summariesに保存（upsert）
            if current_status or next_milestone or recent_progress:
                # 既存レコードを確認
                existing = self.supabase.table('orch_project_summaries').select('id').eq('project_id', project_id).execute()

                summary_data = {
                    'project_id': project_id,
                    'current_status': current_status,
                    'next_milestone': next_milestone,
                    'recent_progress': recent_progress,
                    'updated_at': datetime.now().isoformat()
                }

                if existing.data:
                    # 更新
                    self.supabase.table('orch_project_summaries').update(summary_data).eq('project_id', project_id).execute()
                    self.logger.info(f"プロジェクトサマリーを更新: {project_id}")
                else:
                    # 新規作成
                    self.supabase.table('orch_project_summaries').insert(summary_data).execute()
                    self.logger.info(f"プロジェクトサマリーを作成: {project_id}")

        except Exception as e:
            self.logger.error(f"サマリー保存エラー: {e}")

    def read_claude_md(self, project_dir: Path) -> Optional[str]:
        """プロジェクトのCLAUDE.mdを読む"""
        claude_md = project_dir / 'CLAUDE.md'
        if claude_md.exists():
            try:
                return claude_md.read_text(encoding='utf-8')
            except Exception as e:
                self.logger.warning(f"CLAUDE.md読み込みエラー: {e}")
        return None

    def _create_run_record(self, task_id: int, project_id: str, instruction: str) -> Optional[int]:
        """orch_runsにレコードを作成し、run_idを返す"""
        try:
            result = self.supabase.table('orch_runs').insert({
                'task_id': task_id,
                'project_id': project_id,
                'instruction': instruction,
                'status': 'running',
                'timeout_seconds': 600,
                'claude_code_version': 'latest'
            }).execute()

            if result.data and len(result.data) > 0:
                run_id = result.data[0]['id']
                self.logger.info(f"Run record created: #{run_id}")
                return run_id
            else:
                self.logger.error("Failed to create run record: no data returned")
                return None
        except Exception as e:
            self.logger.error(f"Run record creation error: {e}")
            return None

    def _complete_run_record(self, run_id: int, success: bool, exit_code: int, output: str, duration_seconds: int):
        """orch_runsのレコードを更新"""
        try:
            # 完全な出力をファイルに保存
            output_path = self._save_full_output(run_id, output)

            # DBには最初の5000文字のみ保存
            stdout_preview = output[:5000] if output else ""

            update_data = {
                'status': 'completed' if success else 'failed',
                'exit_code': exit_code,
                'stdout_preview': stdout_preview,
                'full_output_path': str(output_path) if output_path else None,
                'completed_at': datetime.now().isoformat(),
                'duration_seconds': duration_seconds
            }

            self.supabase.table('orch_runs').update(update_data).eq('id', run_id).execute()
            self.logger.info(f"Run record #{run_id} updated: {'success' if success else 'failed'}")
        except Exception as e:
            self.logger.error(f"Run record update error: {e}")

    def _save_full_output(self, run_id: int, output: str) -> Optional[Path]:
        """完全な出力をログファイルに保存"""
        try:
            log_dir = Path.home() / "orchestrator" / "logs" / "runs"
            log_dir.mkdir(parents=True, exist_ok=True)

            log_file = log_dir / f"run_{run_id}.log"
            log_file.write_text(output, encoding='utf-8')

            self.logger.debug(f"Full output saved to: {log_file}")
            return log_file
        except Exception as e:
            self.logger.error(f"Failed to save full output: {e}")
            return None

    def _save_tool_calls(self, run_id: int, output: str):
        """Claude Code出力からツール呼び出しを抽出してorch_tool_callsに保存"""
        try:
            # ツール呼び出しを解析
            tool_calls = ToolCallParser.parse(output)

            if not tool_calls:
                self.logger.debug("No tool calls found in output")
                return

            # orch_tool_callsに保存
            for tool_call in tool_calls:
                self.supabase.table('orch_tool_calls').insert({
                    'run_id': run_id,
                    'tool_name': tool_call['tool_name'],
                    'parameters': json.dumps(tool_call['parameters']),
                    'success': tool_call['success'],
                    'sequence_number': tool_call['sequence_number'],
                    'category': tool_call['category']
                }).execute()

            self.logger.info(f"Saved {len(tool_calls)} tool calls for run #{run_id}")

        except Exception as e:
            self.logger.error(f"Failed to save tool calls: {e}")

    def execute_with_claude_code(self, project_id: str, instruction: str) -> tuple[bool, int, str]:
        """Claude Codeでタスクを実行"""
        # プロジェクトIDとディレクトリ名のマッピング
        project_dir_mapping = {
            'idiom': 'idiom-metaphor-analyzer',
            'orchestrator-dashboard': 'orchestrator-dashboard',
            'docflow': 'docflow',
            'tagless': 'tagless',
            'orchestrator': '../orchestrator'  # orchestratorは~/orchestratorにある
        }

        dir_name = project_dir_mapping.get(project_id, project_id)
        project_dir = self.projects_dir / dir_name

        if not project_dir.exists():
            error_msg = f"プロジェクトディレクトリが見つかりません: {project_dir}"
            self.logger.error(error_msg)
            return False, -1, error_msg

        # CLAUDE.mdを読む（文脈として）
        claude_md = self.read_claude_md(project_dir)
        if claude_md:
            self.logger.info(f"CLAUDE.mdを読み込みました（{len(claude_md)}文字）")

        # 実行する指示を構築
        full_instruction = f"""## 背景

orchestrator-dashboardから指示が投入されました。
プロジェクト: {project_id}

## 指示

{instruction}

## 注意

- 短く簡潔に作業してください
- 完了したら「完了しました」と報告してください
- エラーが発生したら「失敗しました: [理由]」と報告してください

## 完了後のアクション

タスク完了後、以下を出力してください：

1. プロジェクトの現在の状態を1-2文で要約（何を実装中で、次に何をする予定か）：

```summary
現在の状態: [1-2文で要約]
次の予定: [1文で要約]
最近の進捗: [1文で要約]
```

2. このプロジェクトで次にやるべきことを3つ提案：

```suggestions
1. [タイトル] - [簡潔な説明]
2. [タイトル] - [簡潔な説明]
3. [タイトル] - [簡潔な説明]
```
"""

        self.logger.info(f"Claude Codeを起動: プロジェクト={project_id}")
        self.logger.debug(f"指示内容:\n{full_instruction}")

        try:
            # 一時ファイルに指示を書き出す（改行・エスケープ問題を回避）
            temp_instruction_file = Path('/tmp') / f'orchestrator_task_{self.current_task_id}.txt'
            temp_instruction_file.write_text(full_instruction, encoding='utf-8')

            # claudeコマンドを実行（非対話モード）
            result = subprocess.run(
                ['bash', '-c', f'cd {project_dir} && cat {temp_instruction_file} | claude --dangerously-skip-permissions --print'],
                capture_output=True,
                text=True,
                timeout=600  # 10分でタイムアウト
            )

            # 一時ファイルを削除
            temp_instruction_file.unlink(missing_ok=True)

            output = result.stdout + result.stderr

            if result.returncode == 0:
                self.logger.info("Claude Code実行成功")
                return True, result.returncode, output
            else:
                self.logger.error(f"Claude Code実行失敗（exit code: {result.returncode}）")
                return False, result.returncode, output

        except subprocess.TimeoutExpired:
            error_msg = "タイムアウト（10分）"
            self.logger.error(error_msg)
            return False, -2, error_msg
        except Exception as e:
            error_msg = f"実行エラー: {str(e)}"
            self.logger.error(error_msg)
            return False, -3, error_msg

    def execute_task(self, task: Dict[str, Any]):
        """タスクを実行"""
        task_id = task['id']
        project_id = task['project_id']
        instruction = task['title']

        self.current_task_id = task_id
        self.logger.info(f"=" * 60)
        self.logger.info(f"タスク実行開始: #{task_id}")
        self.logger.info(f"  プロジェクト: {project_id}")
        self.logger.info(f"  指示: {instruction}")
        self.logger.info(f"=" * 60)

        # orch_runsにレコードを作成
        run_id = self._create_run_record(task_id, project_id, instruction)

        # ステータスをin_progressに更新
        self.update_task_status(task_id, 'in_progress')

        # 開始時刻を記録
        start_time = time.time()

        # Claude Codeで実行
        success, exit_code, output = self.execute_with_claude_code(project_id, instruction)

        # 実行時間を計算
        duration_seconds = int(time.time() - start_time)

        # orch_runsレコードを更新
        if run_id:
            self._complete_run_record(run_id, success, exit_code, output, duration_seconds)
            # ツール呼び出しを解析して保存
            self._save_tool_calls(run_id, output)

        # 結果を記録（既存のタスクステータス更新）
        if success:
            # 最初の1000文字のみタスクに保存
            self.update_task_status(task_id, 'done', f"実行完了\n\n{output[:1000]}")
            self.logger.info(f"タスク#{task_id}が完了しました")
            # 次の提案を保存
            self.save_suggestions(project_id, output)
            # プロジェクトサマリーを保存
            self.save_project_summary(project_id, output)
        else:
            self.update_task_status(task_id, 'failed', f"実行失敗\n\n{output[:500]}")
            self.logger.error(f"タスク#{task_id}が失敗しました: {output[:500]}")

        self.current_task_id = None

    def run(self):
        """メインループ"""
        self.logger.info("=" * 60)
        self.logger.info("Task Executor 起動")
        self.logger.info("=" * 60)

        if not self.initialize_supabase():
            self.logger.error("Supabase初期化に失敗しました")
            return

        self.logger.info("ポーリング開始（1分間隔）")

        while True:
            try:
                # pendingタスクを取得
                tasks = self.get_pending_tasks()

                if tasks:
                    self.logger.info(f"{len(tasks)}件のpendingタスクを検出")

                    # 1件ずつ実行（同時実行は1タスクまで）
                    for task in tasks:
                        self.execute_task(task)
                        # タスク間に少し待機
                        time.sleep(5)
                else:
                    self.logger.debug("pendingタスクなし")

                # 1分待機
                time.sleep(60)

            except KeyboardInterrupt:
                self.logger.info("中断されました")
                break
            except Exception as e:
                self.logger.error(f"予期しないエラー: {e}", exc_info=True)
                time.sleep(60)


def main():
    """メイン処理"""
    executor = TaskExecutor()
    executor.run()


if __name__ == '__main__':
    main()
