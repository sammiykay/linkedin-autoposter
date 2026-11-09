@echo off
cd /d %~dp0
if not exist venv ( python -m venv venv )
venv\Scripts\pip install -q -r requirements.txt
if not exist .env ( copy .env.example .env & echo Edit .env first, then re-run. & exit /b )
start "worker" venv\Scripts\python -m app.worker
venv\Scripts\python -m app.dashboard
