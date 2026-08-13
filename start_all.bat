@echo off
set PY=C:\Users\16411\AppData\Local\Programs\Python\Python313\python.exe

echo === Starting Tianshu Services ===

REM Social server
start "Tianshu-Social" cmd /c "cd /d F:\tianshu-social && %PY% -m tianshu_social.server --port 8750"
timeout /t 2 /nobreak >nul

REM Main CLI
echo Starting Tianshu CLI...
cd /d F:\tianshu
%PY% -m tianshu.main

pause
