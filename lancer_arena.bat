@echo off
rem Jev Arena : demarre Docker, l'application, puis ouvre la page d'accueil.
cd /d "%~dp0"
echo Demarrage de Docker Desktop...
docker desktop start --timeout 180 >nul 2>&1
echo Demarrage de l'arene (arena + crawl4ai)...
docker compose up -d
echo Attente du serveur...
:wait
timeout /t 2 /nobreak >nul
curl -s -o nul http://127.0.0.1:8800/api/status || goto wait
echo Ouverture de http://127.0.0.1:8800
start "" http://127.0.0.1:8800
echo.
echo Pour tout arreter : docker compose down   (dans ce dossier)
pause
