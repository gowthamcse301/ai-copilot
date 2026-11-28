@echo off
cd /d "d:\Project AI"
title Happy Toonzz Web Hub & AI Studio
echo Starting Happy Toonzz Server at http://127.0.0.1:8000 ...
start "" "http://127.0.0.1:8000/login"
"C:\Users\innoppl\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
