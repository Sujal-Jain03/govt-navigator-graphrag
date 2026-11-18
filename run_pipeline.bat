@echo off
cd /d "G:\My Drive\project marathon\Govt Navigator GraphRAG"
"D:\anaconda_files_install\envs\gen_ai\python.exe" run_pipeline.py >> processed_data\updates\pipeline.log 2>&1
