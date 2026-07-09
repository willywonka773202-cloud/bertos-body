@echo off
cd /d "%USERPROFILE%\odysseus"
"%USERPROFILE%\odysseus\.venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 7860
