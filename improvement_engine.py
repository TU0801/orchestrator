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

    def aggregate_improvements(self, run_ids: List[int]) -> Dict[str, Any]:
        """
        評価から改善提案を集約（スキル・エージェント評価を含む）

        Args:
            run_ids: 対象のrun IDリスト

        Returns:
            改善提案の辞書（suggestions, ineffective_skills, missing_skills, agent_suggestions）
        """
        try:
            response = self.supabase.table('orch_evaluations') \
                .select('improvement_suggestions, tool_usage_analysis') \
                .in_('run_id', run_ids) \
                .execute()

            evaluations = response.data or []
            all_suggestions = []
            ineffective_skills = []
            missing_skills = []
            agent_suggestions = []

            for evaluation in evaluations:
                try:
                    # 一般的な改善提案
                    suggestions = json.loads(evaluation['improvement_suggestions'])
                    all_suggestions.extend(suggestions)

                    # スキル・エージェント評価
                    tool_usage = json.loads(evaluation.get('tool_usage_analysis', '{}'))
                    skill_eff = tool_usage.get('skill_effectiveness', {})
                    agent_eff = tool_usage.get('agent_effectiveness', {})

                    # 効果のないスキル
                    if skill_eff.get('ineffective_skills'):
                        ineffective_skills.extend(skill_eff['ineffective_skills'])

                    # 不足しているスキル
                    if skill_eff.get('missing_skills'):
                        missing_skills.extend(skill_eff['missing_skills'])

                    # エージェント改善提案
                    if agent_eff.get('better_agent_suggestion'):
                        agent_suggestions.append(agent_eff['better_agent_suggestion'])

                except (json.JSONDecodeError, TypeError):
                    continue

            return {
                'suggestions': list(set(all_suggestions)),
                'ineffective_skills': list(set(ineffective_skills)),
                'missing_skills': list(set(missing_skills)),
                'agent_suggestions': list(set(agent_suggestions))
            }

        except Exception as e:
            self.logger.error(f"Error aggregating improvements: {e}")
            return {
                'suggestions': [],
                'ineffective_skills': [],
                'missing_skills': [],
                'agent_suggestions': []
            }

    def apply_improvement(self, project_id: str, trigger: Dict[str, Any], improvements: Dict[str, Any]) -> bool:
        """
        改善を適用（別ブランチに）

        Args:
            project_id: プロジェクトID
            trigger: トリガー情報
            improvements: 改善提案辞書（suggestions, ineffective_skills, missing_skills, agent_suggestions）

        Returns:
            成功したらTrue
        """
        try:
            # プロジェクト設定をDBから取得
            config = self.get_project_config(project_id)
            project_dir = self.projects_dir / config['directory']

            if not project_dir.exists():
                self.logger.error(f"Project directory not found: {project_dir}")
                return False

            # ブランチ名を生成
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            branch_name = f"auto-improvement-{timestamp}"

            # 改善内容を生成するプロンプト
            suggestions = improvements.get('suggestions', [])
            ineffective_skills = improvements.get('ineffective_skills', [])
            missing_skills = improvements.get('missing_skills', [])
            agent_suggestions = improvements.get('agent_suggestions', [])

            improvement_prompt = f"""## 自動改善タスク - スキル/エージェント最適化

プロジェクト: {project_id}

## トリガー
タイプ: {trigger['trigger_type']}
詳細: {json.dumps(trigger['details'], indent=2)}

## 改善提案
{chr(10).join(f'{i+1}. {s}' for i, s in enumerate(suggestions)) if suggestions else '（一般的な改善提案なし）'}

## スキル評価結果
### 効果のないスキル（削除を検討）:
{chr(10).join(f'  - {s}' for s in ineffective_skills) if ineffective_skills else '  （なし）'}

### 不足しているスキル（作成を推奨）:
{chr(10).join(f'  - {s}' for s in missing_skills) if missing_skills else '  （なし）'}

## エージェント改善提案:
{chr(10).join(f'  - {s}' for s in agent_suggestions) if agent_suggestions else '  （なし）'}

## 指示

上記の失敗パターンと改善提案に基づいて、以下を実行してください：

### 1. スキル管理（最優先）
- `.claude/skills/` ディレクトリを確認・作成
- **効果のないスキルを削除**:
{chr(10).join(f'  * {s} を削除または大幅改修' for s in ineffective_skills) if ineffective_skills else '  （削除対象なし）'}
- **不足しているスキルを作成**:
{chr(10).join(f'  * {s} を作成' for s in missing_skills) if missing_skills else '  （作成不要）'}
- スキルファイル命名規則: `{project_id}-[purpose].sh` または `.py`
- スキル内容: 再利用可能なコマンド/パターンを定義、ドキュメント必須

### 2. エージェント設定
- `.claude/agents/` ディレクトリを確認・作成（必要に応じて）
- プロジェクト固有のエージェント設定を作成
  * カスタムプロンプトテンプレート
  * ツール使用ポリシー
  * 失敗を避けるためのガードレール

### 3. サブエージェント構成
- タスクが複雑な場合、サブエージェントの組み立て戦略を `.claude/subagents.md` に記録
- どのタスクをどのエージェントに分割すべきかの判断基準

### 4. 外部リソース活用
- 類似の問題を解決する公開スキル/パターンがあれば参考にする
- 必要に応じて有用なスクリプトやツールを `.claude/tools/` に配置

### 5. CLAUDE.md更新
- 今回の失敗パターンと対策を記録
- スキル/エージェント構成の変更を文書化
- 「失敗から学んだこと」セクションを追加

### 6. コード改善（必要に応じて）
- 根本的なコード問題があれば修正
- ただしスキル/エージェント強化を優先

## 重要な注意事項
- 既存の機能を壊さないこと
- スキルファイルは実行可能で、明確なドキュメントを含むこと
- 変更は段階的に（一度に多くを変えすぎない）
- テスト可能な形で実装すること

## 出力形式

```changes
.claude/skills/[新規スキル].sh - [目的と機能の説明]
.claude/agents/[設定ファイル] - [エージェント設定の説明]
CLAUDE.md - [失敗パターンと対策を追記]
[その他の変更ファイル] - [説明]
```

```skills-created
スキル名: [名前]
目的: [このスキルが解決する問題]
使い方: [実行方法]
---
スキル名: [名前]
...
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
            import re

            # 変更ファイルを抽出
            changes_match = re.search(r'```changes\s*\n(.*?)\n```', output, re.DOTALL)
            changes_summary = changes_match.group(1) if changes_match else "No summary provided"

            # target_filesを構築
            target_files = []
            if changes_match:
                for line in changes_match.group(1).split('\n'):
                    if ':' in line:
                        file_path = line.split(':')[0].strip()
                        target_files.append(file_path)

            # 作成されたスキルを抽出
            skills_match = re.search(r'```skills-created\s*\n(.*?)\n```', output, re.DOTALL)
            skills_created = []
            if skills_match:
                skill_blocks = skills_match.group(1).split('---')
                for block in skill_blocks:
                    if 'スキル名:' in block:
                        skills_created.append(block.strip())

            # orch_improvement_historyに保存
            self.supabase.table('orch_improvement_history').insert({
                'project_id': project_id,
                'trigger_type': trigger['trigger_type'],
                'trigger_details': json.dumps(trigger['details']),
                'target_files': json.dumps(target_files),
                'changes_summary': changes_summary + (f"\n\n## Created Skills:\n{chr(10).join(skills_created)}" if skills_created else ""),
                'before_avg_score': trigger['details'].get('average_score', 0.0)
            }).execute()

            # 作成されたスキルファイルをorch_knowledge_assetsに記録
            self._record_knowledge_assets(project_id, target_files, branch_name)

            self.logger.info(f"Improvement history recorded for {project_id}")
            if skills_created:
                self.logger.info(f"Created {len(skills_created)} new skills")

        except Exception as e:
            self.logger.error(f"Error recording improvement history: {e}")

    def _record_knowledge_assets(self, project_id: str, target_files: List[str], branch_name: str):
        """作成されたスキル/エージェント設定をorch_knowledge_assetsに記録"""
        try:
            # プロジェクト設定をDBから取得
            config = self.get_project_config(project_id)
            project_dir = self.projects_dir / config['directory']

            for file_path in target_files:
                # .claude/配下のファイルのみ記録
                if not file_path.startswith('.claude/'):
                    continue

                # ファイルタイプを判定
                if '/skills/' in file_path:
                    asset_type = 'skill'
                elif '/agents/' in file_path:
                    asset_type = 'agent'
                elif 'subagents.md' in file_path:
                    asset_type = 'subagent_config'
                else:
                    asset_type = 'other'

                # ファイル内容を読み込み
                full_path = project_dir / file_path
                if not full_path.exists():
                    continue

                try:
                    content = full_path.read_text(encoding='utf-8')
                    content_hash = hashlib.sha256(content.encode()).hexdigest()

                    # orch_knowledge_assetsに保存
                    self.supabase.table('orch_knowledge_assets').insert({
                        'project_id': project_id,
                        'asset_type': asset_type,
                        'file_path': file_path,
                        'content': content,
                        'content_hash': content_hash,
                        'version': 1,
                        'auto_generated': True,
                        'created_by': 'improvement_engine'
                    }).execute()

                    self.logger.info(f"Recorded knowledge asset: {file_path} ({asset_type})")

                except Exception as e:
                    self.logger.warning(f"Failed to record knowledge asset {file_path}: {e}")

        except Exception as e:
            self.logger.error(f"Error recording knowledge assets: {e}")

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
        improvements = self.aggregate_improvements(run_ids)

        if not improvements['suggestions'] and not improvements['missing_skills']:
            self.logger.warning(f"No improvement suggestions found for {project_id}")
            return

        self.logger.info(f"Aggregated improvements: {len(improvements['suggestions'])} suggestions, "
                        f"{len(improvements['ineffective_skills'])} ineffective skills, "
                        f"{len(improvements['missing_skills'])} missing skills, "
                        f"{len(improvements['agent_suggestions'])} agent suggestions")

        # 改善を適用
        success = self.apply_improvement(project_id, trigger, improvements)

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
