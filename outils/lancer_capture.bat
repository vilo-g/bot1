@echo off
rem Capture des messages du jeu (ecoute seulement, n envoie rien au jeu).
rem Avant : arreter le bot, lancer_chrome.bat, se connecter au jeu.
cd /d "%~dp0.."
py "%~dp0capture.py"
echo.
pause
