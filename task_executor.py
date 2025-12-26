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
import threading
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


class ParallelTaskExecutor:
    """並列タスク実行管理"""

    def __init__(self, max_concurrent: int = 3):
        """
        Args:
            max_concurrent: 最大同時実行数（デフォルト: 3）
        """
        self.running_projects: Dict[str, Dict[str, Any]] = {}
        self.max_concurrent = max_concurrent
        self.lock = threading.Lock()
        self.logger = logging.getLogger('ParallelTaskExecutor')

    def can_start_task(self, project_id: str) -> bool:
        """
        タスクを開始できるかチェック

        Args:
            project_id: プロジェクトID

        Returns:
            開始可能ならTrue
        """
        with self.lock:
            # 同じプロジェクトで実行中なら不可
            if project_id in self.running_projects:
                self.logger.info(f"Project {project_id} is already running")
                return False

            # 最大同時実行数チェック
            if len(self.running_projects) >= self.max_concurrent:
                self.logger.info(f"Max concurrent tasks reached ({self.max_concurrent})")
                return False

            return True

    def register_task(self, project_id: str, run_id: int, thread: threading.Thread):
        """
        実行中タスクを登録

        Args:
            project_id: プロジェクトID
            run_id: 実行ID
            thread: 実行スレッド
        """
        with self.lock:
            self.running_projects[project_id] = {
                'run_id': run_id,
                'thread': thread,
                'started_at': datetime.now()
            }
            self.logger.info(f"Registered task for {project_id} (run_id: {run_id})")

    def unregister_task(self, project_id: str):
        """
        実行完了タスクを登録解除

        Args:
            project_id: プロジェクトID
        """
        with self.lock:
            if project_id in self.running_projects:
                del self.running_projects[project_id]
                self.logger.info(f"Unregistered task for {project_id}")

    def get_running_count(self) -> int:
        """実行中のタスク数を取得"""
        with self.lock:
            return len(self.running_projects)

    def get_running_projects(self) -> list[str]:
        """実行中のプロジェクトIDリストを取得"""
        with self.lock:
            return list(self.running_projects.keys())


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
        ],
        'Skill': [
            r'Skill\s+tool.*skill[:\s]+"?([^"\n]+)"?',
            r'Using skill[:\s]+([^\n]+)',
            r'Invoking skill[:\s]+([^\n]+)',
        ],
        'Task': [
            r'Task\s+tool.*subagent_type[:\s]+"?([^"\n]+)"?',
            r'Launching agent[:\s]+([^\n]+)',
            r'Starting.*agent.*[:\s]+([^\n]+)',
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
                    elif tool_name == 'Skill':
                        parameters['skill'] = param_value
                    elif tool_name == 'Task':
                        parameters['subagent_type'] = param_value

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
        elif tool_name == 'Skill':
            return 'skill_usage'
        elif tool_name == 'Task':
            return 'agent_invocation'
        else:
            return 'other'


class TaskExecutor:
    """タスク実行エンジン"""

    def __init__(self):
        self.logger = self._setup_logging()
        self.supabase: Optional[Client] = None
        self.projects_dir = Path.home() / 'projects'
        self.current_task_id: Optional[int] = None
        self.parallel_executor = ParallelTaskExecutor(max_concurrent=3)

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

    def get_project_config(self, project_id: str) -> dict:
        """
        プロジェクト設定をDBから取得

        Returns:
            {
                'directory': str,  # ローカルディレクトリパス
                'session_name': str,  # Resume セッション名
                'repo_url': str  # リポジトリURL
            }
        """
        try:
            result = self.supabase.table('orch_projects').select(
                'local_directory, resume_session_name, repository_url'
            ).eq('id', project_id).single().execute()

            if result.data:
                return {
                    'directory': result.data.get('local_directory') or project_id,
                    'session_name': result.data.get('resume_session_name') or f"orch-{project_id}",
                    'repo_url': result.data.get('repository_url')
                }
        except Exception as e:
            self.logger.warning(f"Failed to get project config from DB: {e}. Using defaults.")

        # デフォルト設定
        return {
            'directory': project_id,
            'session_name': f"orch-{project_id}",
            'repo_url': None
        }

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
                'timeout_seconds': 600
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

    def _perform_self_evaluation(self, run_id: int, task_id: int, instruction: str, output: str, success: bool, exit_code: int):
        """タスク実行結果を自己評価してorch_evaluationsに保存"""
        try:
            # 使用されたツールを取得
            tool_calls = ToolCallParser.parse(output)
            skills_used = [tc for tc in tool_calls if tc['tool_name'] == 'Skill']
            agents_used = [tc for tc in tool_calls if tc['tool_name'] == 'Task']

            tools_summary = f"\n使用されたスキル ({len(skills_used)}件):\n"
            for skill in skills_used:
                tools_summary += f"  - {skill['parameters'].get('skill', 'unknown')}\n"

            tools_summary += f"\n起動されたエージェント ({len(agents_used)}件):\n"
            for agent in agents_used:
                tools_summary += f"  - {agent['parameters'].get('subagent_type', 'unknown')}\n"

            # 評価プロンプトを構築
            evaluation_prompt = f"""あなたは自分自身の実行を評価するAIです。以下のタスク実行を評価してください。

## タスク指示
{instruction}

## 実行結果
成功: {success}
終了コード: {exit_code}

## 使用したツール・スキル・エージェント
{tools_summary}

## 出力（最初の3000文字）
{output[:3000]}

## 評価項目

以下の形式でJSON形式で評価を返してください：

```json
{{
  "overall_score": <1-10の数値>,
  "failure_category": "<失敗した場合のカテゴリ: tool_usage_error, skill_ineffective, agent_misconfigured, permission_error, logic_error, timeout, unknown, または null>",
  "evaluation_details": {{
    "task_completion": "<タスクが完了したかどうか>",
    "quality": "<実装の質>",
    "efficiency": "<効率性>"
  }},
  "improvement_suggestions": [
    "<改善提案1>",
    "<改善提案2>",
    "<改善提案3>"
  ],
  "tool_usage_analysis": {{
    "appropriate_tools": <適切なツールを使用したか: true/false>,
    "tool_sequence": "<ツール呼び出しの順序は適切だったか>"
  }},
  "skill_effectiveness": {{
    "skills_used": ["<使用したスキル名>"],
    "effective_skills": ["<効果的だったスキル>"],
    "ineffective_skills": ["<効果がなかった/問題を起こしたスキル>"],
    "missing_skills": ["<あれば良かったスキル>"]
  }},
  "agent_effectiveness": {{
    "agents_used": ["<使用したエージェントタイプ>"],
    "appropriate_agent_choice": <エージェント選択が適切だったか: true/false>,
    "agent_performance": "<各エージェントのパフォーマンス評価>",
    "better_agent_suggestion": "<より適切なエージェントがあれば提案>"
  }},
  "error_patterns": [
    "<検出されたエラーパターン>"
  ]
}}
```

注意:
- overall_scoreは1-10で評価（10が最高）
- 成功した場合はfailure_categoryをnullに
- スキル・エージェントの効果を具体的に評価すること
- 効果のないスキルは削除を、不足しているスキルは作成を提案
- 具体的で実行可能な改善提案を3つ以上
"""

            self.logger.info(f"Performing self-evaluation for run #{run_id}")

            # 一時ファイルに評価プロンプトを書き出す
            temp_eval_file = Path('/tmp') / f'orchestrator_eval_{run_id}.txt'
            temp_eval_file.write_text(evaluation_prompt, encoding='utf-8')

            # Claude APIを使って評価を取得（claudeコマンド経由）
            result = subprocess.run(
                ['bash', '-c', f'cat {temp_eval_file} | claude --dangerously-skip-permissions --print'],
                capture_output=True,
                text=True,
                timeout=120  # 2分でタイムアウト
            )

            # 一時ファイルを削除
            temp_eval_file.unlink(missing_ok=True)

            if result.returncode != 0:
                self.logger.warning(f"Evaluation failed with exit code {result.returncode}")
                return

            eval_output = result.stdout

            # JSON部分を抽出
            json_match = re.search(r'```json\s*\n(.*?)\n```', eval_output, re.DOTALL)
            if not json_match:
                self.logger.warning("Failed to extract JSON from evaluation output")
                return

            evaluation_data = json.loads(json_match.group(1))

            # tool_usage_analysisにスキル・エージェント評価を含める
            tool_usage = evaluation_data.get('tool_usage_analysis', {})
            tool_usage['skill_effectiveness'] = evaluation_data.get('skill_effectiveness', {})
            tool_usage['agent_effectiveness'] = evaluation_data.get('agent_effectiveness', {})

            # orch_evaluationsに保存
            self.supabase.table('orch_evaluations').insert({
                'run_id': run_id,
                'task_id': task_id,
                'overall_score': evaluation_data.get('overall_score', 5.0),
                'failure_category': evaluation_data.get('failure_category'),
                'evaluation_details': json.dumps(evaluation_data.get('evaluation_details', {})),
                'improvement_suggestions': json.dumps(evaluation_data.get('improvement_suggestions', [])),
                'tool_usage_analysis': json.dumps(tool_usage),
                'error_patterns': json.dumps(evaluation_data.get('error_patterns', [])),
                'evaluator': 'claude_code'
            }).execute()

            # スキル・エージェント評価のサマリーをログ
            skill_eff = evaluation_data.get('skill_effectiveness', {})
            agent_eff = evaluation_data.get('agent_effectiveness', {})

            if skill_eff.get('ineffective_skills'):
                self.logger.warning(f"Ineffective skills detected: {skill_eff['ineffective_skills']}")
            if skill_eff.get('missing_skills'):
                self.logger.info(f"Missing skills suggested: {skill_eff['missing_skills']}")
            if agent_eff.get('better_agent_suggestion'):
                self.logger.info(f"Better agent suggestion: {agent_eff['better_agent_suggestion']}")

            self.logger.info(f"Self-evaluation saved for run #{run_id}: score={evaluation_data.get('overall_score')}")

        except subprocess.TimeoutExpired:
            self.logger.warning("Self-evaluation timed out")
        except json.JSONDecodeError as e:
            self.logger.warning(f"Failed to parse evaluation JSON: {e}")
        except Exception as e:
            self.logger.error(f"Self-evaluation error: {e}")

    def execute_with_claude_code(self, project_id: str, instruction: str) -> tuple[bool, int, str]:
        """Claude Codeでタスクを実行"""
        # プロジェクト設定をDBから取得
        config = self.get_project_config(project_id)
        project_dir = self.projects_dir / config['directory']

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

        # セッション名を取得
        session_name = config['session_name']

        try:
            # 一時ファイルに指示を書き出す（改行・エスケープ問題を回避）
            temp_instruction_file = Path('/tmp') / f'orchestrator_task_{self.current_task_id}.txt'
            temp_instruction_file.write_text(full_instruction, encoding='utf-8')

            # Claude Codeを--printモードで実行
            # Note: --printモードではセッション永続化はサポートされていない (--no-session-persistence)
            # 各タスクは独立して実行される
            self.logger.info(f"Executing task (session disabled in --print mode)")
            claude_cmd = f'cd {project_dir} && cat {temp_instruction_file} | claude --dangerously-skip-permissions --print'

            result = subprocess.run(
                ['bash', '-c', claude_cmd],
                capture_output=True,
                text=True,
                timeout=600  # 10分でタイムアウト
            )

            if result.returncode == 0:
                self.logger.info(f"✓ Task executed successfully")

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

    def _execute_task_internal(self, task: Dict[str, Any]):
        """タスクを実行（内部処理）"""
        # Safety check
        if task is None:
            self.logger.error("❌ Task is None in _execute_task_internal!")
            return

        task_id = task['id']
        project_id = task['project_id']

        # descriptionがあればそれを使い、なければtitleを使う
        description = task.get('description') or ''
        description = description.strip() if description else ''
        instruction = description if description else task['title']

        try:
            self.current_task_id = task_id
            self.logger.info(f"=" * 60)
            self.logger.info(f"タスク実行開始: #{task_id}")
            self.logger.info(f"  プロジェクト: {project_id}")
            self.logger.info(f"  タイトル: {task['title']}")
            if description:
                self.logger.info(f"  詳細指示: {description[:100]}..." if len(description) > 100 else f"  詳細指示: {description}")
            self.logger.info(f"=" * 60)

            # orch_runsにレコードを作成
            run_id = self._create_run_record(task_id, project_id, instruction)

            # ステータスをin_progressに更新
            self.update_task_status(task_id, 'in_progress')

            # orch_runsのステータスを'running'に更新
            if run_id:
                self.supabase.table('orch_runs').update({'status': 'running'}).eq('id', run_id).execute()

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
                # 自己評価を実行
                self._perform_self_evaluation(run_id, task_id, instruction, output, success, exit_code)

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

        finally:
            # 実行完了後、並列実行管理から削除
            self.parallel_executor.unregister_task(project_id)

    def execute_task_async(self, task: Dict[str, Any]):
        """タスクを非同期（別スレッド）で実行"""
        project_id = task['project_id']

        # 実行可能かチェック
        if not self.parallel_executor.can_start_task(project_id):
            self.logger.warning(f"Cannot start task for {project_id}: already running or max concurrent reached")
            return False

        # スレッドを作成して実行
        thread = threading.Thread(target=self._execute_task_internal, args=(task,), daemon=True)
        thread.start()

        # run_idはまだ作成されていないので、ダミー値で登録
        # （実際の run_id は _execute_task_internal 内で作成される）
        self.parallel_executor.register_task(project_id, 0, thread)

        self.logger.info(f"Started task for {project_id} in background thread")
        return True

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
                # 実行中のタスク数を表示
                running_count = self.parallel_executor.get_running_count()
                if running_count > 0:
                    running_projects = self.parallel_executor.get_running_projects()
                    self.logger.info(f"実行中: {running_count}件 (プロジェクト: {', '.join(running_projects)})")

                # pendingタスクを取得
                tasks = self.get_pending_tasks()

                if tasks:
                    self.logger.info(f"{len(tasks)}件のpendingタスクを検出")

                    # 並列実行（最大3件まで同時実行）
                    for task in tasks:
                        # 非同期で実行（開始可能なら即座に返る）
                        started = self.execute_task_async(task)
                        if started:
                            self.logger.info(f"タスク#{task['id']}をバックグラウンドで開始")
                        else:
                            self.logger.debug(f"タスク#{task['id']}はスキップ（実行中または最大同時実行数に達している）")
                        # 少し待機してから次のタスクをチェック
                        time.sleep(2)
                else:
                    self.logger.debug("pendingタスクなし")

                # 10秒待機（ポーリング間隔を短縮）
                time.sleep(10)

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
