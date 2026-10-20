@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -X utf8 -c "import sys; sys.argv=[0]; import monitor, playwright.sync_api, PIL" 2>nul || (echo Ставлю библиотеки один раз... & py -m pip install --user playwright pillow)
py -X utf8 monitor.py --serve
pause
