@echo off
rem Ouvre un Chrome reserve au bot (profil separe, port de debogage 9222).
rem Ferme d'abord tes autres fenetres Chrome.
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo Chrome est introuvable. Modifie la ligne CHROME dans ce fichier.
  pause
  exit /b 1
)
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%~dp0..\profil-chrome" "https://empire.goodgamestudios.com/"
