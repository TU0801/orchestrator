#!/usr/bin/env python3
"""
Test script to insert a task directly into Supabase
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

# Load .env
load_dotenv(Path(__file__).parent / '.env')

# Initialize Supabase
supabase_url = os.environ.get('SUPABASE_URL')
supabase_key = os.environ.get('SUPABASE_KEY')

supabase = create_client(supabase_url, supabase_key)

# Insert test task
task_data = {
    'project_id': 'idiom',
    'title': 'テストタスク: Supabase連携確認',
    'description': 'master.pyからのタスク保存機能をテストするためのタスク',
    'why': 'Supabase連携の動作確認',
    'status': 'pending',
    'priority': 'normal',
    'estimated_hours': None,
    'actual_hours': None,
    'blockers': [],
    'dependencies': []
}

result = supabase.table('orch_tasks').insert(task_data).execute()
print("✅ タスクを挿入しました:")
print(result.data)
