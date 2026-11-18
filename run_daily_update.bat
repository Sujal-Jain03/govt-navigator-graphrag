@echo off
cd /d "G:\My Drive\project marathon\Govt Navigator GraphRAG"
"D:\anaconda_files_install\envs\gen_ai\python.exe" run_daily_update.py >> processed_data\updates\daily_update.log 2>&1
