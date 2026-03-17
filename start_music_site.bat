@echo off
echo Starting Last.fm Updater...
start "Last.fm Updater" cmd /k "cd /d C:\Users\joell\Desktop\musicdb && python lastfm_updater.py"
echo Starting Flask Website...
start "Flask Website" cmd /k "cd /d C:\Users\joell\Desktop\musicdb && python app.py"
echo Both processes started in separate windows.
pause