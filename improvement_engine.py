#!/usr/bin/env python3
"""
Improvement Engine - 自己改善エンジン

評価結果から失敗パターンを検出し、自動的に改善を適用する。
"""

import os
import json
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
import hashlib

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False


class ImprovementEngine:
    """自動改善エンジン"""

    def __init__(self, supabase: Client, logger: Optional[logging.Logger] = None):
        self.supabase = supabase
        self.logger = logger or self._setup_logging()
        self.projects_dir = Path.home() / 'projects'

        # 安全性設定
        self.cooldown_hours = 24  # 同じプロジェクトは24時間に1回まで
        self.max_improvements_per_week = 3  # 同じファイルは週に3回まで

    def _setup_logging(self) -> logging.Logger:
        """ロギング設定"""
        logger = logging.getLogger('ImprovementEngine')
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(handler)
        return logger

    def check_triggers(self, project_id: str) -> Optional[Dict[str, Any]]:
        """
        改善トリガーをチェック

        Returns:
            トリガー情報（検出されなければNone）
            {
                'trigger_type': 'consecutive_failures' or 'low_score',
                'details': {...}
            }
        """
        # トリガー1: 同じカテゴリの失敗が3回連続
        consecutive_failure_trigger = self._check_consecutive_failures(project_id)
        if consecutive_failure_trigger:
            return consecutive_failure_trigger

        # トリガー2: 直近5実行の平均スコアが5.0未満
        low_score_trigger = self._check_low_average_score(project_id)
        if low_score_trigger:
            return low_score_trigger

        return None

    def _check_consecutive_failures(self, project_id: str) -> Optional[Dict[str, Any]]:
        """3回連続の同じカテゴリの失敗を検出"""
        try:
            # 直近10実行を取得
            response = self.supabase.table('orch_runs') \
                .select('id, status, created_at') \
                .eq('project_id', project_id) \
                .order('created_at', desc=True) \
                .limit(10) \
                .execute()

            runs = response.data or []
            if len(runs) < 3:
                return None

            # 直近3つが失敗かチェック
            recent_runs = runs[:3]
            if not all(run['status'] == 'failed' for run in recent_runs):
                return None

            # 評価データから失敗カテゴリを取得
            run_ids = [run['id'] for run in recent_runs]
            eval_response = self.supabase.table('orch_evaluations') \
                .select('run_id, failure_category') \
                .in_('run_id', run_ids) \
                .execute()

            evaluations = eval_response.data or []
            if len(evaluations) < 3:
                return None

            # 同じカテゴリの失敗が3回続いているかチェック
            categories = [e['failure_category'] for e in evaluations if e['failure_category']]
            if len(categories) >= 3 and categories[0] == categories[1] == categories[2]:
                return {
                    'trigger_type': 'consecutive_failures',
                    'details': {
                        'failure_category': categories[0],
                        'run_ids': run_ids,
                        'count': 3
                    }
                }

            return None

        except Exception as e:
            self.logger.error(f"Error checking consecutive failures: {e}")
            return None

    def _check_low_average_score(self, project_id: str) -> Optional[Dict[str, Any]]:
        """直近5実行の平均スコアが5.0未満を検出"""
        try:
            # 直近5実行の評価を取得
            response = self.supabase.table('orch_runs') \
                .select('id') \
                .eq('project_id', project_id) \
                .order('created_at', desc=True) \
                .limit(5) \
                .execute()

            runs = response.data or []
            if len(runs) < 5:
                return None

            run_ids = [run['id'] for run in runs]
            eval_response = self.supabase.table('orch_evaluations') \
                .select('overall_score') \
                .in_('run_id', run_ids) \
                .execute()

            evaluations = eval_response.data or []
            if len(evaluations) < 5:
                return None

            scores = [e['overall_score'] for e in evaluations]
            avg_score = sum(scores) / len(scores)

            if avg_score < 5.0:
                return {
                    'trigger_type': 'low_score',
                    'details': {
                        'average_score': avg_score,
                        'run_ids': run_ids,
                        'scores': scores
                    }
                }

            return None

        except Exception as e:
            self.logger.error(f"Error checking low average score: {e}")
            return None

    def check_cooldown(self, project_id: str) -> bool:
        """
        クールダウン期間をチェック

        Returns:
            True: 改善可能, False: クールダウン期間中
        """
        try:
            # 直近の改善履歴を取得
            cutoff_time = (datetime.now() - timedelta(hours=self.cooldown_hours)).isoformat()

            response = self.supabase.table('orch_improvement_history') \
                .select('applied_at') \
                .eq('project_id', project_id) \
                .gte('applied_at', cutoff_time) \
                .execute()

            if response.data and len(response.data) > 0:
                self.logger.info(f"Project {project_id} is in cooldown period")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error checking cooldown: {e}")
            return False

    def aggregate_improvements(self, run_ids: List[int]) -> List[str]:
        """
        評価から改善提案を集約

        Args:
            run_ids: 対象のrun IDリスト

        Returns:
            改善提案のリスト
        """
        try:
            response = self.supabase.table('orch_evaluations') \
                .select('improvement_suggestions') \
                .in_('run_id', run_ids) \
                .execute()

            evaluations = response.data or []
            all_suggestions = []

            for evaluation in evaluations:
                try:
                    suggestions = json.loads(evaluation['improvement_suggestions'])
                    all_suggestions.extend(suggestions)
                except (json.JSONDecodeError, TypeError):
                    continue

            # 重複を除去
            unique_suggestions = list(set(all_suggestions))
            return unique_suggestions

        except Exception as e:
            self.logger.error(f"Error aggregating improvements: {e}")
            return []

    def apply_improvement(self, project_id: str, trigger: Dict[str, Any], suggestions: List[str]) -> bool:
        """
        改善を適用（別ブランチに）

        Args:
            project_id: プロジェクトID
            trigger: トリガー情報
            suggestions: 改善提案リスト

        Returns:
            成功したらTrue
        """
        try:
            # プロジェクトディレクトリを取得
            project_dir_mapping = {
                'idiom': 'idiom-metaphor-analyzer',
                'orchestrator-dashboard': 'orchestrator-dashboard',
                'docflow': 'docflow',
                'tagless': 'tagless',
                'orchestrator': '../orchestrator'
            }

            dir_name = project_dir_mapping.get(project_id, project_id)
            project_dir = self.projects_dir / dir_name

            if not project_dir.exists():
                self.logger.error(f"Project directory not found: {project_dir}")
                return False

            # ブランチ名を生成
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            branch_name = f"auto-improvement-{timestamp}"

            # 改善内容を生成するプロンプト
            improvement_prompt = f"""## 自動改善タスク

プロジェクト: {project_id}

## トリガー
タイプ: {trigger['trigger_type']}
詳細: {json.dumps(trigger['details'], indent=2)}

## 改善提案
{chr(10).join(f'{i+1}. {s}' for i, s in enumerate(suggestions))}

## 指示

上記の改善提案に基づいて、プロジェクトのコード、CLAUDE.md、またはスキルを改善してください。

重要:
- 変更は慎重に行い、既存の機能を壊さないこと
- CLAUDE.mdに改善内容を記録すること
- 変更理由を明確にすること
- 実装後、変更内容をサマリーとして出力すること

出力形式:
```changes
ファイル1: path/to/file1 - 変更内容の説明
ファイル2: path/to/file2 - 変更内容の説明
```
"""

            self.logger.info(f"Applying improvement to {project_id} on branch {branch_name}")

            # Gitで新しいブランチを作成
            subprocess.run(
                ['git', 'checkout', '-b', branch_name],
                cwd=project_dir,
                check=True,
                capture_output=True
            )

            # 一時ファイルに改善プロンプトを書き出す
            temp_file = Path('/tmp') / f'improvement_{project_id}_{timestamp}.txt'
            temp_file.write_text(improvement_prompt, encoding='utf-8')

            # Claude Codeで改善を実行
            result = subprocess.run(
                ['bash', '-c', f'cd {project_dir} && cat {temp_file} | claude --dangerously-skip-permissions --print'],
                capture_output=True,
                text=True,
                timeout=600
            )

            temp_file.unlink(missing_ok=True)

            if result.returncode != 0:
                self.logger.error(f"Improvement execution failed: {result.stderr}")
                # ブランチを削除して元に戻す
                subprocess.run(['git', 'checkout', '-'], cwd=project_dir, capture_output=True)
                subprocess.run(['git', 'branch', '-D', branch_name], cwd=project_dir, capture_output=True)
                return False

            # 変更をコミット
            subprocess.run(['git', 'add', '.'], cwd=project_dir, check=True)
            commit_message = f"""Auto-improvement: {trigger['trigger_type']}

Trigger details: {json.dumps(trigger['details'])}

Improvements applied:
{chr(10).join(f'- {s}' for s in suggestions[:5])}

🤖 Auto-generated improvement
"""
            subprocess.run(
                ['git', 'commit', '-m', commit_message],
                cwd=project_dir,
                check=True,
                capture_output=True
            )

            # 改善履歴を記録
            self._record_improvement_history(project_id, trigger, branch_name, result.stdout)

            self.logger.info(f"Improvement applied successfully to branch: {branch_name}")
            self.logger.info(f"Review and merge manually: cd {project_dir} && git checkout {branch_name}")

            return True

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Git operation failed: {e}")
            return False
        except subprocess.TimeoutExpired:
            self.logger.error("Improvement execution timed out")
            return False
        except Exception as e:
            self.logger.error(f"Error applying improvement: {e}")
            return False

    def _record_improvement_history(self, project_id: str, trigger: Dict[str, Any], branch_name: str, output: str):
        """改善履歴を記録"""
        try:
            # 変更ファイルを抽出
            import re
            changes_match = re.search(r'```changes\s*\n(.*?)\n```', output, re.DOTALL)
            changes_summary = changes_match.group(1) if changes_match else "No summary provided"

            # target_filesを構築
            target_files = []
            if changes_match:
                for line in changes_match.group(1).split('\n'):
                    if ':' in line:
                        file_path = line.split(':')[0].strip()
                        target_files.append(file_path)

            self.supabase.table('orch_improvement_history').insert({
                'project_id': project_id,
                'trigger_type': trigger['trigger_type'],
                'trigger_details': json.dumps(trigger['details']),
                'target_files': json.dumps(target_files),
                'changes_summary': changes_summary,
                'before_avg_score': trigger['details'].get('average_score', 0.0)
            }).execute()

            self.logger.info(f"Improvement history recorded for {project_id}")

        except Exception as e:
            self.logger.error(f"Error recording improvement history: {e}")

    def run_improvement_check(self, project_id: str):
        """改善チェックを実行"""
        self.logger.info(f"Checking improvement triggers for {project_id}")

        # クールダウンチェック
        if not self.check_cooldown(project_id):
            self.logger.info(f"Skipping {project_id}: in cooldown period")
            return

        # トリガーチェック
        trigger = self.check_triggers(project_id)
        if not trigger:
            self.logger.debug(f"No triggers detected for {project_id}")
            return

        self.logger.info(f"Trigger detected for {project_id}: {trigger['trigger_type']}")

        # 改善提案を集約
        run_ids = trigger['details'].get('run_ids', [])
        suggestions = self.aggregate_improvements(run_ids)

        if not suggestions:
            self.logger.warning(f"No improvement suggestions found for {project_id}")
            return

        self.logger.info(f"Aggregated {len(suggestions)} improvement suggestions")

        # 改善を適用
        success = self.apply_improvement(project_id, trigger, suggestions)

        if success:
            self.logger.info(f"✓ Improvement applied successfully for {project_id}")
        else:
            self.logger.error(f"✗ Improvement failed for {project_id}")


def main():
    """メイン処理"""
    if not SUPABASE_AVAILABLE:
        print("⚠️  Supabase SDKがインストールされていません")
        return

    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_KEY')

    if not supabase_url or not supabase_key:
        print("⚠️  Supabase認証情報が環境変数に設定されていません")
        return

    supabase = create_client(supabase_url, supabase_key)
    engine = ImprovementEngine(supabase)

    # 全プロジェクトをチェック
    projects_response = supabase.table('orch_projects').select('id').execute()
    projects = projects_response.data or []

    for project in projects:
        engine.run_improvement_check(project['id'])


if __name__ == '__main__':
    main()
