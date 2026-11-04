@echo off
rem Interface graphique du bot (page dans le navigateur, sur ce PC seulement).
cd /d "%~dp0.."
py src\interface.py %*
pause
