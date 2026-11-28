# Happy Toonzz — Official Web Hub & AI Voice Studio

Python (**FastAPI + Jinja2 + SQLite + Gemini AI + Voice TTS**) web application for the **Happy Toonzz** channel (`@HappyToonzz-g7f` on YouTube, `@happy_toonzz` on Instagram).

## Features
- **Master Admin Only Authentication (`scrypt` + SQLite sessions)**: No public registration clutter — log in with Master Username & Password and change either anytime inside the Admin Panel.
- **AI Voice & Task Assistant Dashboard (`/admin`)**:
  - Speak via microphone (English / Tamil) or type any task.
  - Warm, positive automatic voice replies (`edge-tts` + Gemini 3.8 TTS fallback + Hands-Free Voice Loop mode).
  - Real tool actions: Internet search, AI image creation (+ one-click Publish to Looks), HD video clip generation, channel stats updates, and fan message inspection.
- **Official Channel Hub**: YouTube (`@HappyToonzz-g7f`), Instagram (`@happy_toonzz`), Facebook links, content pillars, Character Looks gallery, Reference Sheet zoom, and synced Tamil/Tanglish/English karaoke lyrics for *"உலகம் சிரிக்கட்டும்"*.

## Run Locally
```powershell
pip install -r requirements.txt
copy .env.example .env   # set ADMIN_USERNAME, ADMIN_PASSWORD, SESSION_SECRET, GEMINI_API_KEY
uvicorn app:app --host 127.0.0.1 --port 8000
```
Open: `http://127.0.0.1:8000` (Admin Studio: `/login` -> `/admin`)

## Launch Online (Render)
1. Push `gowthamcse301/ai-copilot` to GitHub.
2. On [render.com](https://render.com), choose **New + -> Blueprint** (uses `render.yaml`).
3. Set `GEMINI_API_KEY` and read your auto-generated `ADMIN_PASSWORD` in Render **Environment** settings.
