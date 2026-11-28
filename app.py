"""Happy Toonzz — FastAPI web application with Master Admin Login & AI Voice Studio."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, UnidentifiedImageError

import ai_assistant
import db
from content_safety import sanitize_homepage_css, sanitize_homepage_html

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "images" / "uploads"
AI_MEDIA_DIR = BASE_DIR / "static" / "ai_media"
LYRICS_PATH = BASE_DIR / "assets" / "exact_audio_cues.json"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_AI_TASK_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="happy-toonzz-ai")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    AI_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    generated_pw = db.init_db()
    if generated_pw:
        admin_user = os.environ.get("ADMIN_USERNAME", "admin")
        print(
            f"\n[Happy Toonzz] Initialized Master Admin -> username: {admin_user} | password: {generated_pw}\n",
            flush=True,
        )
    yield


UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
AI_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Happy Toonzz Official Hub", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/images", StaticFiles(directory=str(BASE_DIR / "images")), name="images")
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _load_lyrics() -> list[dict[str, Any]]:
    if not LYRICS_PATH.exists():
        return []
    try:
        return json.loads(LYRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _current_admin(request: Request) -> dict[str, Any] | None:
    return db.get_user_by_session(request.cookies.get(db.SESSION_COOKIE_NAME))


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "")
    return proto == "https" or request.url.scheme == "https"


def _set_session_cookie(response: RedirectResponse, request: Request, token: str) -> None:
    response.set_cookie(
        key=db.SESSION_COOKIE_NAME,
        value=token,
        max_age=db.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
        path="/",
    )


def _check_same_origin(request: Request) -> None:
    """Block cross-origin browser requests when Origin header is present."""
    origin = request.headers.get("origin")
    if not origin:
        return
    parsed = urlparse(origin)
    host = request.headers.get("host", "")
    if host and parsed.netloc and parsed.netloc != host:
        raise HTTPException(status_code=403, detail="Cross-origin request blocked.")


def _clean_http_url(url: str) -> str:
    """Allow only http(s) URLs or empty string."""
    val = url.strip()
    if not val:
        return ""
    parsed = urlparse(val)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return val
    return ""


def _instagram_profile_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"instagram.com", "www.instagram.com"}:
        return ""
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 1 or path_parts[0].lower() in {"accounts", "explore", "p", "reel", "stories", "tv"}:
        return ""
    return f"https://www.instagram.com/{path_parts[0]}/"


_YOUTUBE_UPLOADS_CACHE: dict[str, tuple[float, str]] = {}
_YOUTUBE_UPLOADS_CACHE_LOCK = threading.Lock()
_YOUTUBE_UPLOADS_CACHE_SECONDS = 6 * 60 * 60


def _youtube_uploads_playlist_id(channel: dict[str, Any], api_key: str) -> str:
    parsed = urlparse(str(channel.get("youtube_url", "")).strip())
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"} and len(path_parts) > 1:
        if path_parts[0] == "channel" and path_parts[1].startswith("UC") and len(path_parts[1]) > 2:
            return f"UU{path_parts[1][2:]}"

    handle = str(channel.get("youtube_handle", "")).strip().lstrip("@")
    if parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"} and path_parts:
        if path_parts[0].startswith("@"):
            handle = path_parts[0][1:]
    if not handle:
        raise HTTPException(status_code=404, detail="A YouTube channel handle is not configured.")

    now = time.monotonic()
    with _YOUTUBE_UPLOADS_CACHE_LOCK:
        cached = _YOUTUBE_UPLOADS_CACHE.get(handle)
        if cached and cached[0] > now:
            return cached[1]

    if not api_key:
        raise HTTPException(status_code=503, detail="YouTube Data API key is not configured.")

    query = urlencode({"part": "contentDetails", "forHandle": handle, "key": api_key})
    request = urllib.request.Request(
        f"https://www.googleapis.com/youtube/v3/channels?{query}",
        headers={"Accept": "application/json", "User-Agent": "HappyToonzzStudio/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise HTTPException(
                status_code=503,
                detail="YouTube could not look up the channel. Check the API key, API access, and quota.",
            ) from exc
        raise HTTPException(status_code=502, detail=f"YouTube channel lookup failed with HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(status_code=502, detail="Could not reach YouTube to load channel videos.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=502, detail="YouTube returned an invalid channel response.") from None

    items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise HTTPException(status_code=404, detail="YouTube did not find the configured channel.")
    content_details = items[0].get("contentDetails", {})
    related_playlists = content_details.get("relatedPlaylists", {}) if isinstance(content_details, dict) else {}
    playlist_id = related_playlists.get("uploads") if isinstance(related_playlists, dict) else None
    if not isinstance(playlist_id, str) or not playlist_id:
        raise HTTPException(status_code=502, detail="YouTube did not return an uploads playlist for this channel.")

    with _YOUTUBE_UPLOADS_CACHE_LOCK:
        _YOUTUBE_UPLOADS_CACHE[handle] = (time.monotonic() + _YOUTUBE_UPLOADS_CACHE_SECONDS, playlist_id)
    return playlist_id


def _safe_next_url(raw_next: str | None, fallback: str) -> str:
    """Allow relative local paths only."""
    if not raw_next:
        return fallback
    candidate = raw_next.strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return fallback


# ---------------------------------------------------------------------------
# Public Website Pages
# ---------------------------------------------------------------------------


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    icon_path = BASE_DIR / "assets" / "sprites" / "happy_toonzz_favicon.ico"
    if icon_path.exists():
        return FileResponse(str(icon_path), media_type="image/vnd.microsoft.icon")
    return Response(status_code=204)


@app.get("/homepage-custom.css")
async def homepage_custom_css():
    content = db.get_homepage_content()
    css = sanitize_homepage_css(content["published_css"]) if content["published_css"] else ""
    return Response(
        content=css,
        media_type="text/css",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, msg_sent: str | None = None):
    admin = _current_admin(request)
    channel = db.get_channel_profile()
    instagram_url = channel.get("instagram_url") or "https://www.instagram.com/happy_toonzz/"
    homepage_content = db.get_homepage_content()
    looks = db.get_looks()
    lyrics = _load_lyrics()
    has_ref_video = (BASE_DIR / "assets" / "reference-video.mp4").exists()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "admin": admin,
            "channel": channel,
            "has_api_key": bool(db.get_gemini_api_key()) if admin else False,
            "ai_history": [],
            "assistant_page_context": "homepage",
            "instagram_embed_url": _instagram_profile_url(instagram_url),
            "homepage_content_html": sanitize_homepage_html(homepage_content["published_html"])
            if homepage_content["published_html"]
            else "",
            "looks": looks,
            "lyrics": lyrics,
            "has_ref_video": has_ref_video,
            "msg_sent": msg_sent == "1",
        },
    )


def _homepage_content_preview_document(content_html: str, content_css: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<base href="/">'
        '<link rel="stylesheet" href="/static/styles.css">'
        f"<style>{content_css}</style>"
        '<link rel="stylesheet" href="/static/homepage_carousel.css?v=2"></head><body>'
        '<main class="container homepage-content-section">'
        f'<div class="homepage-content-card happy-toonzz-content">{content_html}</div>'
        '</main><script src="/static/homepage_carousel.js?v=2" defer></script></body></html>'
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "",
    error: str = "",
):
    admin = _current_admin(request)
    if admin:
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "next_url": next,
            "error": error,
            "channel": db.get_channel_profile(),
        },
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_url: str = Form(""),
):
    _check_same_origin(request)
    admin = db.authenticate_admin(username, password)
    if not admin:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next_url": next_url,
                "error": "Invalid master username or password.",
                "channel": db.get_channel_profile(),
            },
            status_code=401,
        )
    token = db.create_session(admin["id"])
    resp = RedirectResponse(url=_safe_next_url(next_url, "/admin"), status_code=303)
    _set_session_cookie(resp, request, token)
    return resp


@app.post("/logout")
async def logout(request: Request):
    _check_same_origin(request)
    db.delete_session(request.cookies.get(db.SESSION_COOKIE_NAME))
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(db.SESSION_COOKIE_NAME, path="/")
    return resp


# ---------------------------------------------------------------------------
# Master Admin Studio & AI Assistant
# ---------------------------------------------------------------------------


def _require_admin(request: Request, next_url: str = "/admin") -> dict[str, Any] | RedirectResponse:
    admin = _current_admin(request)
    if not admin:
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)
    return admin


def _render_admin_page(
    request: Request,
    template_name: str,
    active_page: str,
    page_title: str,
    page_description: str,
    saved: str = "",
    error: str = "",
    next_url: str = "/admin",
    admin_user: dict[str, Any] | None = None,
    **page_context: Any,
) -> HTMLResponse | RedirectResponse:
    auth = admin_user if admin_user is not None else _require_admin(request, next_url)
    if isinstance(auth, RedirectResponse):
        return auth

    api_key = db.get_gemini_api_key()
    youtube_api_key = db.get_youtube_api_key()
    context = {
        "user": auth,
        "channel": db.get_channel_profile(),
        "has_api_key": bool(api_key),
        "masked_api_key": f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else ("Configured" if api_key else ""),
        "has_youtube_api_key": bool(youtube_api_key),
        "masked_youtube_api_key": (
            f"{youtube_api_key[:4]}...{youtube_api_key[-4:]}"
            if len(youtube_api_key) > 8
            else ("Configured" if youtube_api_key else "")
        ),
        "active_admin_page": active_page,
        "page_title": page_title,
        "page_description": page_description,
        "saved": saved,
        "error": error,
        "ai_history": (
            db.list_ai_messages(auth["id"], limit=30) if active_page == "assistant" else []
        ),
        "assistant_page_context": active_page,
        **page_context,
    }
    return templates.TemplateResponse(request, template_name, context)


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    saved: str = "",
    error: str = "",
):
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth
    return _render_admin_page(
        request,
        "admin.html",
        "overview",
        "Overview",
        "Current channel and workspace statistics.",
        saved=saved,
        error=error,
        admin_user=auth,
        stats=db.get_admin_counts(),
    )


@app.get("/admin/channel", response_class=HTMLResponse)
async def admin_channel_page(request: Request, saved: str = "", error: str = ""):
    return _render_admin_page(
        request,
        "admin_channel.html",
        "channel",
        "Channel profile",
        "Channel identity and social statistics.",
        saved=saved,
        error=error,
        next_url="/admin/channel",
    )


@app.get("/admin/looks", response_class=HTMLResponse)
async def admin_looks_page(request: Request, saved: str = "", error: str = ""):
    auth = _require_admin(request, "/admin/looks")
    if isinstance(auth, RedirectResponse):
        return auth
    return _render_admin_page(
        request,
        "admin_looks.html",
        "looks",
        "Character looks",
        "Manage the looks displayed in your gallery.",
        saved=saved,
        error=error,
        next_url="/admin/looks",
        admin_user=auth,
        looks=db.get_looks(),
    )


@app.get("/admin/inbox", response_class=HTMLResponse)
async def admin_inbox_page(request: Request, saved: str = "", error: str = ""):
    auth = _require_admin(request, "/admin/inbox")
    if isinstance(auth, RedirectResponse):
        return auth
    return _render_admin_page(
        request,
        "admin_inbox.html",
        "inbox",
        "Fan inbox",
        "Messages received from your website.",
        saved=saved,
        error=error,
        next_url="/admin/inbox",
        admin_user=auth,
        messages=db.list_messages(),
    )


@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(request: Request, saved: str = "", error: str = ""):
    return _render_admin_page(
        request,
        "admin_settings.html",
        "settings",
        "Settings",
        "Manage administrator credentials and AI engine access.",
        saved=saved,
        error=error,
        next_url="/admin/settings",
    )


@app.get("/admin/content", response_class=HTMLResponse)
async def admin_homepage_content_page(request: Request, saved: str = "", error: str = ""):
    auth = _require_admin(request, "/admin/content")
    if isinstance(auth, RedirectResponse):
        return auth
    content = db.get_homepage_content()
    draft_html = sanitize_homepage_html(content["draft_html"]) if content["draft_html"] else ""
    draft_css = sanitize_homepage_css(content["draft_css"]) if content["draft_css"] else ""
    published_html = (
        sanitize_homepage_html(content["published_html"]) if content["published_html"] else ""
    )
    published_css = (
        sanitize_homepage_css(content["published_css"]) if content["published_css"] else ""
    )
    return _render_admin_page(
        request,
        "admin_content.html",
        "content",
        "Website content",
        "Review AI-written HTML and CSS before publishing it to the public website.",
        saved=saved,
        error=error,
        next_url="/admin/content",
        admin_user=auth,
        draft_html=draft_html,
        draft_css=draft_css,
        draft_preview_document=_homepage_content_preview_document(draft_html, draft_css),
        draft_updated_at=content["updated_at"],
        published_html=published_html,
        published_preview_document=_homepage_content_preview_document(published_html, published_css),
        versions=db.list_homepage_content_versions(),
    )


@app.post("/admin/content/draft")
async def admin_save_homepage_draft(
    request: Request,
    content_html: str = Form(...),
    content_css: str = Form(""),
):
    _check_same_origin(request)
    auth = _require_admin(request, "/admin/content")
    if isinstance(auth, RedirectResponse):
        return auth
    try:
        sanitized_html = sanitize_homepage_html(content_html)
        sanitized_css = sanitize_homepage_css(content_css)
    except ValueError:
        return RedirectResponse(url="/admin/content?error=invalid_content", status_code=303)
    db.save_homepage_content_draft(
        sanitized_html,
        source="admin",
        summary="Admin edited draft",
        content_css=sanitized_css,
    )
    return RedirectResponse(url="/admin/content?saved=draft", status_code=303)


@app.get("/admin/content/draft-state")
async def admin_homepage_draft_state(request: Request):
    auth = _require_admin(request, "/admin/content")
    if isinstance(auth, RedirectResponse):
        return JSONResponse(
            {"error": "authentication_required"},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
    content = db.get_homepage_content()
    return JSONResponse(
        {"updated_at": content["updated_at"]},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/admin/content/publish")
async def admin_publish_homepage_content(request: Request):
    _check_same_origin(request)
    auth = _require_admin(request, "/admin/content")
    if isinstance(auth, RedirectResponse):
        return auth
    content = db.get_homepage_content()
    try:
        sanitized_html = sanitize_homepage_html(content["draft_html"])
        sanitized_css = sanitize_homepage_css(content["draft_css"])
    except ValueError:
        return RedirectResponse(url="/admin/content?error=invalid_content", status_code=303)
    db.publish_homepage_content(sanitized_html, sanitized_css)
    return RedirectResponse(url="/admin/content?saved=published", status_code=303)


@app.post("/admin/content/versions/{version_id}/restore")
async def admin_restore_homepage_content_version(request: Request, version_id: int):
    _check_same_origin(request)
    auth = _require_admin(request, "/admin/content")
    if isinstance(auth, RedirectResponse):
        return auth
    if not db.restore_homepage_content_version(version_id):
        return RedirectResponse(url="/admin/content?error=version_not_found", status_code=303)
    return RedirectResponse(url="/admin/content?saved=restored", status_code=303)


@app.get("/admin/assistant", response_class=HTMLResponse)
async def admin_assistant_page(
    request: Request,
    saved: str = "",
    error: str = "",
):
    auth = _require_admin(request, "/admin/assistant")
    if isinstance(auth, RedirectResponse):
        return auth

    return _render_admin_page(
        request,
        "assistant.html",
        "assistant",
        "AI Engine",
        "Voice, vision, and command workspace.",
        saved=saved,
        error=error,
        next_url="/admin/assistant",
        admin_user=auth,
    )


def _run_ai_chat_task(
    task_id: str,
    user_id: int,
    prompt: str,
    image_data: bytes | None,
    image_mime_type: str,
    page_context: str,
) -> None:
    db.update_ai_task(task_id, "running")
    try:
        result = ai_assistant.run_assistant_turn(
            user_id,
            prompt,
            image_data=image_data,
            image_mime_type=image_mime_type,
            page_context=page_context,
        )
    except Exception:
        logging.getLogger(__name__).exception("AI task %s failed", task_id)
        error = "The assistant task failed. Please try again."
        db.add_ai_message(user_id, "assistant", error, meta={"task_error": True})
        db.update_ai_task(task_id, "failed", error=error)
        return
    if result.get("ok") is False:
        error = result.get("error") or result.get("reply")
        if not isinstance(error, str) or not error.strip():
            error = "The assistant returned no result. The task failed."
        db.update_ai_task(task_id, "failed", result=result, error=error)
        return
    db.update_ai_task(task_id, "completed", result=result)


def _ai_task_response(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["id"],
        "status": task["status"],
        "prompt": task["prompt"],
        "result": task["result"],
        "error": task["error"],
    }


@app.post("/admin/ai/chat")
def admin_ai_chat(
    request: Request,
    prompt: str = Form(""),
    image_file: UploadFile | None = File(default=None),
    page_context: str = Form("assistant"),
):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    image_data = None
    image_mime_type = ""
    if image_file is not None:
        image_data = image_file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(image_data) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "Images must be 8 MB or smaller."}, status_code=413)
        try:
            with Image.open(BytesIO(image_data)) as image:
                image_format = image.format
                width, height = image.size
                if width * height > 25_000_000:
                    return JSONResponse({"error": "Image dimensions are too large."}, status_code=413)
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            return JSONResponse({"error": "Please attach a valid PNG, JPEG, or WebP image."}, status_code=400)

        image_mime_type = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }.get(image_format or "", "")
        if not image_mime_type:
            return JSONResponse({"error": "Only PNG, JPEG, and WebP images are supported."}, status_code=415)

    if not prompt.strip() and image_data:
        prompt = "Please describe the attached image."
    if not prompt.strip():
        return JSONResponse({"error": "Enter a message or attach an image."}, status_code=400)

    task_id = db.create_ai_task(auth["id"], prompt.strip())
    if task_id is None:
        active_task = db.get_active_ai_task(auth["id"])
        return JSONResponse(
            {
                "error": "An AI task is already running.",
                "task": _ai_task_response(active_task) if active_task else None,
            },
            status_code=409,
        )
    try:
        _AI_TASK_EXECUTOR.submit(
            _run_ai_chat_task,
            task_id,
            auth["id"],
            prompt.strip(),
            image_data,
            image_mime_type,
            page_context,
        )
    except RuntimeError:
        error = "The assistant task queue is unavailable. Please try again."
        db.update_ai_task(task_id, "failed", error=error)
        return JSONResponse({"error": error}, status_code=503)
    return JSONResponse({"task_id": task_id, "status": "queued"}, status_code=202)


@app.get("/admin/ai/history")
async def admin_ai_history(request: Request):
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    return JSONResponse(
        {"messages": db.list_ai_messages(auth["id"], limit=30)},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/admin/ai/tasks/active")
async def admin_ai_active_task(request: Request):
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    task = db.get_active_ai_task(auth["id"])
    return JSONResponse(
        {"task": _ai_task_response(task) if task else None},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/admin/ai/tasks/{task_id}")
async def admin_ai_task_status(request: Request, task_id: str):
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    task = db.get_ai_task(auth["id"], task_id)
    if task is None:
        return JSONResponse({"error": "AI task not found."}, status_code=404)
    return JSONResponse(
        _ai_task_response(task),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/admin/ai/tts")
def admin_ai_tts(
    request: Request,
    text: str = Form(...),
    voice_gender: str = Form("female"),
    voice_style: str = Form("natural"),
):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    if voice_gender not in {"female", "male"} or voice_style not in {"natural", "playful"}:
        return JSONResponse({"error": "Choose a supported voice and style."}, status_code=400)
    audio_bytes = ai_assistant.synthesize_tts(text, voice_gender, voice_style)
    if not audio_bytes:
        return Response(status_code=204)
    mime = "audio/wav" if audio_bytes[:4] == b"RIFF" else "audio/mpeg"
    return Response(content=audio_bytes, media_type=mime)


@app.post("/admin/ai/clear")
async def admin_ai_clear(request: Request):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth
    db.clear_ai_messages(auth["id"])
    return RedirectResponse(url="/admin/assistant?saved=ai_cleared", status_code=303)


@app.post("/admin/ai/publish")
async def admin_ai_publish(
    request: Request,
    image_url: str = Form(...),
    title: str = Form("Happy Toonzz AI Look"),
    subtitle: str = Form("Created in AI Studio"),
):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    res = ai_assistant.tool_publish_look(
        image_url=image_url,
        title=title,
        subtitle=subtitle,
        description=f"{title} — generated inside the Happy Toonzz AI Studio.",
    )
    return JSONResponse(res)


@app.post("/admin/credentials")
async def admin_update_credentials(
    request: Request,
    current_password: str = Form(...),
    new_username: str = Form(...),
    new_password: str = Form(""),
):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth

    verified = db.authenticate_admin(auth["username"], current_password)
    if not verified:
        return RedirectResponse(url="/admin/settings?error=wrong_password", status_code=303)

    err = db.update_admin_credentials(
        auth["id"],
        new_username=new_username,
        new_password=new_password.strip() or None,
    )
    if err:
        return RedirectResponse(url="/admin/settings?error=cred_invalid", status_code=303)
    return RedirectResponse(url="/admin/settings?saved=credentials", status_code=303)


@app.post("/admin/apikey")
async def admin_update_apikey(
    request: Request,
    gemini_api_key: str = Form(...),
):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth
    db.set_setting("gemini_api_key", gemini_api_key.strip())
    return RedirectResponse(url="/admin/settings?saved=apikey", status_code=303)


@app.post("/admin/youtube-apikey")
async def admin_update_youtube_apikey(
    request: Request,
    youtube_api_key: str = Form(...),
):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth
    db.set_setting("youtube_api_key", youtube_api_key.strip())
    return RedirectResponse(url="/admin/settings?saved=youtube_apikey", status_code=303)


@app.post("/admin/channel")
async def admin_update_channel(
    request: Request,
    channel_name: str = Form(...),
    tagline: str = Form(...),
    location: str = Form(...),
    bio: str = Form(...),
    youtube_handle: str = Form(...),
    youtube_url: str = Form(...),
    youtube_subs: str = Form(""),
    youtube_videos: str = Form(""),
    instagram_handle: str = Form(""),
    instagram_url: str = Form(""),
    instagram_followers: str = Form(""),
    facebook_handle: str = Form(""),
    facebook_url: str = Form(""),
    facebook_followers: str = Form(""),
):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth

    db.update_channel_profile(
        {
            "channel_name": channel_name,
            "tagline": tagline,
            "location": location,
            "bio": bio,
            "youtube_handle": youtube_handle,
            "youtube_url": _clean_http_url(youtube_url) or "https://www.youtube.com/@HappyToonzz-g7f",
            "youtube_subs": youtube_subs,
            "youtube_videos": youtube_videos,
            "instagram_handle": instagram_handle,
            "instagram_url": _clean_http_url(instagram_url),
            "instagram_followers": instagram_followers,
            "facebook_handle": facebook_handle,
            "facebook_url": _clean_http_url(facebook_url),
            "facebook_followers": facebook_followers,
        }
    )
    return RedirectResponse(url="/admin/channel?saved=channel", status_code=303)


@app.post("/admin/looks")
async def admin_add_look(
    request: Request,
    title: str = Form(...),
    subtitle: str = Form(...),
    category: str = Form(...),
    badge: str = Form(...),
    description: str = Form(...),
    image_file: UploadFile = File(...),
):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth

    ext = Path(image_file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return RedirectResponse(url="/admin/looks?error=invalid_image", status_code=303)

    payload = await image_file.read()
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        return RedirectResponse(url="/admin/looks?error=image_too_large", status_code=303)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"look_{secrets.token_hex(6)}{ext}"
    (UPLOAD_DIR / fname).write_bytes(payload)

    db.add_look(
        title=title,
        subtitle=subtitle,
        category=category if category in {"adventure", "cozy"} else "adventure",
        badge=badge,
        image_path=f"images/uploads/{fname}",
        description=description,
    )
    return RedirectResponse(url="/admin/looks?saved=look", status_code=303)


@app.post("/admin/looks/{look_id}/delete")
async def admin_delete_look(request: Request, look_id: int):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth

    removed_path = db.delete_look(look_id)
    if removed_path and removed_path.startswith("images/uploads/"):
        file_path = (BASE_DIR / removed_path).resolve()
        if str(file_path).startswith(str(UPLOAD_DIR.resolve())) and file_path.exists():
            file_path.unlink(missing_ok=True)
    return RedirectResponse(url="/admin/looks?saved=look_deleted", status_code=303)


@app.post("/admin/messages/{message_id}/delete")
async def admin_delete_message(request: Request, message_id: int):
    _check_same_origin(request)
    auth = _require_admin(request)
    if isinstance(auth, RedirectResponse):
        return auth

    db.delete_message(message_id)
    return RedirectResponse(url="/admin/inbox?saved=message_deleted", status_code=303)


# ---------------------------------------------------------------------------
# Public API Routes
# ---------------------------------------------------------------------------


@app.post("/api/messages")
async def api_submit_message(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    message: str = Form(...),
):
    _check_same_origin(request)
    if not name.strip() or not message.strip():
        return RedirectResponse(url="/#contact", status_code=303)
    db.add_message(name, email, message)
    return RedirectResponse(url="/?msg_sent=1#contact", status_code=303)


@app.get("/api/channel")
async def api_channel():
    return {
        "channel": db.get_channel_profile(),
        "looks": db.get_looks(),
    }


@app.get("/api/youtube/uploads-playlist")
async def api_youtube_uploads_playlist():
    playlist_id = await asyncio.to_thread(
        _youtube_uploads_playlist_id,
        db.get_channel_profile(),
        db.get_youtube_api_key(),
    )
    return {"playlist_id": playlist_id}


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "service": "happy-toonzz-web"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=False)
