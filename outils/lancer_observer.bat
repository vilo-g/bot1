@echo off
rem Lance l'observateur (il enregistre dans le dossier captures, il n'envoie rien au jeu).
rem Le Chrome du bot doit deja etre ouvert (lancer_chrome.bat).
cd /d "%~dp0..\captures"
py "%~dp0observer.py"
echo.
echo Fichiers enregistres dans : %cd%
pause
