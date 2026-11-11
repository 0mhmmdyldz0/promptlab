@echo off
cd /d "%~dp0"
echo promptlab aciliyor... (kapatmak icin bu pencereyi kapat)
python -m promptlab serve --dir projem
pause
