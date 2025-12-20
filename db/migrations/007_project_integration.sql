-- Phase 1: プロジェクト紐付け統合 + 並列実行基盤
-- orch_projectsにカラム追加

ALTER TABLE orch_projects ADD COLUMN IF NOT EXISTS local_directory TEXT;
ALTER TABLE orch_projects ADD COLUMN IF NOT EXISTS resume_session_name TEXT;

-- 既存データを更新
UPDATE orch_projects SET local_directory = 'idiom-metaphor-analyzer', resume_session_name = 'orch-idiom' WHERE id = 'idiom';
UPDATE orch_projects SET local_directory = 'orchestrator-dashboard', resume_session_name = 'orch-dashboard' WHERE id = 'orchestrator-dashboard';
UPDATE orch_projects SET local_directory = 'docflow', resume_session_name = 'orch-docflow' WHERE id = 'docflow';
UPDATE orch_projects SET local_directory = 'tagless', resume_session_name = 'orch-tagless' WHERE id = 'tagless';
UPDATE orch_projects SET local_directory = '../orchestrator', resume_session_name = 'orch-orchestrator' WHERE id = 'orchestrator';

-- orch_runsに進捗カラム追加
ALTER TABLE orch_runs ADD COLUMN IF NOT EXISTS current_progress JSONB;

-- コメント追加
COMMENT ON COLUMN orch_projects.local_directory IS 'ローカルディレクトリパス (projects/以下の相対パス)';
COMMENT ON COLUMN orch_projects.resume_session_name IS 'Claude Code Resumeセッション名';
COMMENT ON COLUMN orch_runs.current_progress IS 'タスク実行の進捗状況 (タスクリスト形式)';
