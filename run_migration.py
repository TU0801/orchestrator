#!/usr/bin/env python3
"""
Run database migration by executing SQL directly via Supabase
"""
import os
import sys
from supabase import create_client

# Read .env file
env_path = os.path.join(os.path.dirname(__file__), '.env')
with open(env_path, 'r') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ[key] = value

supabase_url = os.environ.get('SUPABASE_URL')
supabase_key = os.environ.get('SUPABASE_KEY')

if not supabase_url or not supabase_key:
    print('Error: SUPABASE_URL or SUPABASE_KEY not set in .env')
    sys.exit(1)

supabase = create_client(supabase_url, supabase_key)

print('Running migration: 007_project_integration.sql')
print('=' * 60)

# Read migration SQL
migration_path = 'db/migrations/007_project_integration.sql'
with open(migration_path, 'r') as f:
    sql_content = f.read()

# Split into individual statements
statements = []
current_statement = []
for line in sql_content.split('\n'):
    stripped = line.strip()

    # Skip empty lines and comment-only lines
    if not stripped or stripped.startswith('--'):
        continue

    current_statement.append(line)

    # If line ends with semicolon, it's the end of a statement
    if stripped.endswith(';'):
        stmt = '\n'.join(current_statement).strip()
        if stmt and not stmt.startswith('COMMENT'):
            statements.append(stmt)
        current_statement = []

print(f'Found {len(statements)} SQL statements to execute\n')

# Execute each statement via psycopg2 if available, otherwise inform user
try:
    import psycopg2
    from urllib.parse import urlparse

    # Parse connection URL from supabase_url
    # Supabase URL format: https://PROJECT_REF.supabase.co
    # Database connection: postgresql://postgres:[PASSWORD]@db.PROJECT_REF.supabase.co:5432/postgres

    project_ref = supabase_url.replace('https://', '').replace('.supabase.co', '')

    # For Supabase, we need the database password which is different from the API key
    # We'll need to inform the user to run this manually or provide DB password
    print('⚠️  Direct database connection requires PostgreSQL password')
    print('    (This is different from the Supabase API key)')
    print()
    print('Please run the migration manually in Supabase Dashboard:')
    print('1. Go to https://supabase.com/dashboard/project/' + project_ref)
    print('2. Navigate to SQL Editor')
    print('3. Paste and run the contents of:', migration_path)
    print()
    print('Or provide database password in .env as:')
    print('  SUPABASE_DB_PASSWORD=your_postgres_password')

    db_password = os.environ.get('SUPABASE_DB_PASSWORD')
    if db_password:
        db_host = f'db.{project_ref}.supabase.co'
        # Use connection pooler port 6543 with IPv4
        conn_string = f'postgresql://postgres.{project_ref}:{db_password}@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres'

        print(f'\nConnecting to database via pooler...')
        try:
            conn = psycopg2.connect(conn_string)
        except Exception as e:
            print(f'Pooler connection failed: {e}')
            print('Trying direct connection with session mode...')
            conn_string = f'postgresql://postgres.{project_ref}:{db_password}@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres'
            conn = psycopg2.connect(conn_string)
        cur = conn.cursor()

        for i, stmt in enumerate(statements, 1):
            print(f'{i}. Executing: {stmt[:60]}...')
            try:
                cur.execute(stmt)
                conn.commit()
                print(f'   ✓ Success')
            except Exception as e:
                print(f'   ✗ Error: {e}')
                conn.rollback()

        cur.close()
        conn.close()
        print('\n✓ Migration completed!')

except ImportError:
    print('⚠️  psycopg2 not installed. Cannot execute SQL directly.')
    print()
    print('Please run the migration manually in Supabase Dashboard:')
    print('1. Go to Supabase Dashboard > SQL Editor')
    print('2. Open:', migration_path)
    print('3. Copy and paste the SQL content')
    print('4. Click "Run"')
    print()
    print('Or install psycopg2 and provide DB password:')
    print('  pip install psycopg2-binary')
    print('  Add SUPABASE_DB_PASSWORD to .env')
