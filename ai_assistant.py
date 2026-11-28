"""Happy Toonzz AI Studio Assistant: chat, web search, image creation, video generation, and voice TTS."""

from __future__ import annotations

import asyncio
import base64
import html
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import edge_tts
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import db

BASE_DIR = Path(__file__).resolve().parent
AI_MEDIA_DIR = BASE_DIR / "static" / "ai_media"
UPLOAD_DIR = BASE_DIR / "images" / "uploads"
REF_SHEET_PATH = BASE_DIR / "images" / "reference-sheet.jpg"
SPRITES_DIR = BASE_DIR / "assets" / "sprites"
IMAGES_DIR = BASE_DIR / "images"

# Locate FFmpeg binary if installed via WinGet or PATH
_DEFAULT_FFMPEG = (
    r"C:\Users\innoppl\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.1-full_build\bin\ffmpeg.exe"
)
FFMPEG_BIN = shutil.which("ffmpeg") or (_DEFAULT_FFMPEG if os.path.exists(_DEFAULT_FFMPEG) else "")

CHAT_MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-4-hydrogen"]

MASCOT_VISUAL_DESC = (
    "Cute fluffy 3D Pixar-style pastel kitten mascot 'Happy Toonzz' with soft white fur, "
    "pastel pink tuft on top of head, rosy pink cheeks, big sparkling purple-violet eyes, "
    "tiny pink nose, pink fluffy arms and tail, golden-yellow right leg, sky-blue left leg, "
    "bright joyful smile, vibrant family-friendly 3D cartoon lighting"
)

SYSTEM_PROMPT = """You are the Happy Toonzz Studio Copilot — a warm, positive, enthusiastic, creative AI assistant helping the creator run their Happy Toonzz brand (YouTube @HappyToonzz-g7f, Instagram @happy_toonzz, based in Chennai, India).

Personality & Response Style:
- Always be uplifting, encouraging, clear, and proactive ("positive response").
- Because replies may be read aloud via voice playback, write naturally in clean conversational sentences without cluttering with raw markdown code blocks or giant tables unless specifically needed. Keep replies focused and actionable, usually 1-3 concise sentences.
- If the creator speaks or writes in Tamil or Tanglish, respond naturally in warm Tamil or Tamil + English. Strictly use only pure Tamil script (தமிழ்) and English words — never mix characters from Malayalam, Bengali, Telugu, or other scripts.

Mascot Context:
- Happy Toonzz's main character is a fluffy pastel 3D kitten ("Our Little Happy Friend — Small Character... Big Happiness!"): white fur, pink head tuft/arms/tail, golden-yellow right leg, sky-blue left leg, big purple sparkling eyes.
- Theme song: "உலகம் சிரிக்கட்டும் (Happy Toonzz Kondaattam)".

Tool Usage Rules:
- When asked to search the internet, trends, facts, news, or competitor/channel info -> call web_search.
- When asked to find, open, or play an existing YouTube video -> call search_youtube_video; this embeds the best embeddable match in chat. Do not use generate_video for an existing video.
- When asked to create, draw, or generate an image, celebration poster, thumbnail, or festival greeting -> call create_image (set use_mascot_reference=True and fill headline/subtitle/theme cleanly so our real Happy Toonzz mascot reference is used!).
- When asked to generate/make a video/clip/reel -> call generate_video.
- When asked to update subscriber/follower numbers, social links, bio, or tagline -> call update_channel_stats.
- When asked about current website stats, looks, or fan messages -> call get_channel_overview.
- When asked to add a generated image to the website's Looks gallery -> call publish_image_as_look.
"""

TOOL_DECLARATIONS = [
    {
        "type": "function",
        "name": "search_youtube_video",
        "description": "Find one embeddable YouTube video for a request to watch or play a video, song, or clip. Use this instead of web_search when the user wants video playback.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Video title, topic, song, or descriptive search query"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "web_search",
        "description": "Search the internet for up-to-date information, trends, YouTube ideas, news, or facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "create_image",
        "description": "Create/generate an AI image or branded Happy Toonzz mascot poster using the official reference character.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed visual prompt describing the scene"},
                "headline": {"type": "string", "description": "Short bold poster title text if creating a poster (e.g. '28K FOLLOWERS!', 'DIWALI WISHES', 'HAPPY TOONZZ')"},
                "subtitle": {"type": "string", "description": "Short supporting tagline on poster (e.g. 'Thank You Instagram Family!')"},
                "theme": {
                    "type": "string",
                    "enum": ["celebration", "superhero", "astronaut", "winter", "chef", "meadow"],
                    "description": "Theme style matching the request",
                },
                "use_mascot_reference": {
                    "type": "boolean",
                    "description": "True to use the official Happy Toonzz pastel kitten mascot reference images",
                },
                "aspect": {
                    "type": "string",
                    "enum": ["square", "wide"],
                    "description": "square (1024x1024) for Instagram/posters or wide (1280x720) for YouTube thumbnails",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "type": "function",
        "name": "generate_video",
        "description": "Generate a short animated MP4 video clip for a prompt.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Scene & action description for the video clip"},
                "duration_sec": {"type": "integer", "description": "Clip length in seconds (5 to 10)"},
            },
            "required": ["prompt"],
        },
    },
    {
        "type": "function",
        "name": "update_channel_stats",
        "description": "Update Happy Toonzz website channel info, links, bio, or follower/subscriber counts.",
        "parameters": {
            "type": "object",
            "properties": {
                "youtube_subs": {"type": "string", "description": "YouTube subscriber count e.g. '165'"},
                "youtube_videos": {"type": "string", "description": "YouTube video count"},
                "instagram_followers": {"type": "string", "description": "Instagram follower count"},
                "facebook_followers": {"type": "string", "description": "Facebook follower count"},
                "facebook_url": {"type": "string", "description": "Facebook page URL"},
                "facebook_handle": {"type": "string", "description": "Facebook page name"},
                "bio": {"type": "string", "description": "Channel bio text"},
                "tagline": {"type": "string", "description": "Hero tagline"},
            },
        },
    },
    {
        "type": "function",
        "name": "get_channel_overview",
        "description": "Read current channel profile stats, character looks, and recent fan messages from the website database.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "publish_image_as_look",
        "description": "Publish an already-generated image URL (/static/ai_media/...) onto the live Happy Toonzz Looks gallery.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "The /static/ai_media/... URL returned by create_image"},
                "title": {"type": "string", "description": "Short look title"},
                "subtitle": {"type": "string", "description": "Short 4-6 word subtitle"},
                "category": {"type": "string", "enum": ["adventure", "cozy"]},
                "badge": {"type": "string", "description": "One-word badge e.g. Fantasy, Festive"},
                "description": {"type": "string", "description": "One-sentence description for the look card"},
            },
            "required": ["image_url", "title", "subtitle", "description"],
        },
    },
]


def _make_client(api_key: str, api_version: str | None = None, timeout_ms: int = 25000) -> genai.Client:
    opts: dict[str, Any] = {
        "timeout": timeout_ms,
        "retry_options": types.HttpRetryOptions(attempts=1),
    }
    if api_version:
        opts["api_version"] = api_version
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(**opts))


def _free_web_search(query: str) -> dict[str, Any]:
    """Fallback live web search via Bing RSS, Google News RSS, and Wikipedia API."""
    results: list[dict[str, str]] = []
    q_enc = urllib.parse.quote(query)

    # 1. Bing Web Search RSS
    try:
        req = urllib.request.Request(
            f"https://www.bing.com/search?q={q_enc}&format=rss",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", errors="ignore")
        root = ET.fromstring(raw)
        for item in root.findall(".//item")[:5]:
            results.append(
                {
                    "title": html.unescape(item.findtext("title") or ""),
                    "url": item.findtext("link") or "",
                    "snippet": html.unescape(re.sub(r"<[^>]+>", "", item.findtext("description") or ""))[:220],
                }
            )
    except Exception:
        pass

    # 2. Google News RSS if few results
    if len(results) < 3:
        try:
            req = urllib.request.Request(
                f"https://news.google.com/rss/search?q={q_enc}&hl=en-IN&gl=IN&ceid=IN:en",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", errors="ignore")
            root = ET.fromstring(raw)
            for item in root.findall(".//item")[:4]:
                results.append(
                    {
                        "title": html.unescape(item.findtext("title") or ""),
                        "url": item.findtext("link") or "",
                        "snippet": "News / recent topic result",
                    }
                )
        except Exception:
            pass

    # 3. Wikipedia Opensearch
    try:
        req = urllib.request.Request(
            f"https://en.wikipedia.org/w/api.php?action=opensearch&search={q_enc}&limit=3&format=json",
            headers={"User-Agent": "HappyToonzzStudio/1.0"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=6).read().decode("utf-8", errors="ignore"))
        for title, desc, link in zip(data[1], data[2], data[3]):
            if title:
                results.append({"title": title, "url": link, "snippet": desc[:220]})
    except Exception:
        pass

    return {"ok": bool(results), "source": "web_rss_fallback", "results": results[:8]}


def tool_web_search(query: str, api_key: str) -> dict[str, Any]:
    """Search the web using Gemini Google Search grounding when available, else live RSS/Wiki."""
    if api_key:
        for model_name in ("gemini-3.8-flash", "gemini-3.5-flash-lite"):
            try:
                client = _make_client(api_key, timeout_ms=15000)
                inter = client.interactions.create(
                    model=model_name,
                    input=query,
                    tools=[{"type": "google_search"}],
                )
                if inter.output_text:
                    return {"ok": True, "source": f"google_search:{model_name}", "summary": inter.output_text}
            except Exception:
                continue
    return _free_web_search(query)


def tool_search_youtube_video(query: str, api_key: str) -> dict[str, Any]:
    """Search YouTube for one embeddable video without exposing the API key to the browser."""
    search_query = query.strip()
    if not api_key:
        return {
            "ok": False,
            "error": "YouTube playback search is not configured. Add a YouTube Data API key under Admin Settings > YouTube Data API key.",
        }
    if not search_query:
        return {"ok": False, "error": "Enter a topic or title to search YouTube."}

    params = urllib.parse.urlencode(
        {
            "part": "snippet",
            "type": "video",
            "videoEmbeddable": "true",
            "safeSearch": "strict",
            "maxResults": "1",
            "q": search_query,
            "key": api_key,
        }
    )
    request = urllib.request.Request(
        f"https://www.googleapis.com/youtube/v3/search?{params}",
        headers={"Accept": "application/json", "User-Agent": "HappyToonzzStudio/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return {
                "ok": False,
                "error": "YouTube search was denied. Check that the key is valid, YouTube Data API v3 is enabled, and quota is available.",
            }
        return {"ok": False, "error": f"YouTube search failed with HTTP {exc.code}."}
    except (urllib.error.URLError, TimeoutError):
        return {"ok": False, "error": "Could not reach YouTube search. Check the server connection and try again."}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "YouTube search returned an invalid response. Please try again."}

    if not isinstance(data, dict):
        return {"ok": False, "error": "YouTube search returned an invalid response. Please try again."}

    items = data.get("items", [])
    if not isinstance(items, list):
        return {"ok": False, "error": "YouTube search returned an invalid response. Please try again."}

    for item in items:
        if not isinstance(item, dict):
            continue
        video = item.get("id", {})
        snippet = item.get("snippet", {})
        if not isinstance(video, dict):
            continue
        video_id = video.get("videoId")
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            continue
        if not isinstance(snippet, dict):
            snippet = {}
        return {
            "ok": True,
            "video": {
                "video_id": video_id,
                "title": str(snippet.get("title", "")),
                "channel_title": str(snippet.get("channelTitle", "")),
            },
        }
    return {"ok": False, "error": "YouTube did not find an embeddable video for that search."}


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        ("C:/Windows/Fonts/Nirmala.ttc", 1 if bold else 0),
        ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ]
    for path, idx in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    pass
    return ImageFont.load_default()


def _clean_mascot_sprite(img: Image.Image, remove_bl_corner: bool = False) -> Image.Image:
    """Shave 1px cutout fringe and optionally mask stray bottom-left prop artifacts."""
    sp = img.convert("RGBA")
    alpha = sp.getchannel("A")
    if remove_bl_corner:
        # Remove small disconnected artifact sitting at extreme bottom-left corner of sprite sheet crop
        ad = ImageDraw.Draw(alpha)
        ad.rectangle([0, int(sp.height * 0.84), int(sp.width * 0.24), sp.height], fill=0)
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.75))
    sp.putalpha(alpha)
    return sp


def _circular_sticker(path: Path, size: int, ring_color: tuple[int, int, int]) -> Image.Image:
    """Create an anti-aliased circular mascot badge sticker with white + pastel rings."""
    hi = size * 2
    border = max(10, int(hi * 0.08))
    inner = hi - border * 2
    with Image.open(path).convert("RGBA") as src:
        fitted = ImageOps.fit(src, (inner, inner), method=Image.LANCZOS, centering=(0.5, 0.42))
    badge = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.ellipse([0, 0, hi - 1, hi - 1], fill=(255, 255, 255, 248), outline=ring_color + (255,), width=max(8, int(hi * 0.045)))
    circ_mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(circ_mask).ellipse([0, 0, inner - 1, inner - 1], fill=255)
    badge.paste(fitted, (border, border), circ_mask)
    return badge.resize((size, size), Image.LANCZOS)


def _fit_text_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_w: int,
    start_size: int,
    min_size: int = 18,
    bold: bool = True,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, tuple[int, int, int, int]]:
    """Shrink font size gracefully until text fits within max_w pixels."""
    size = start_size
    while size >= min_size:
        font = _load_font(size, bold=bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_w or size == min_size:
            return font, bbox
        size -= max(2, (size - min_size) // 8 or 2)
    font = _load_font(min_size, bold=bold)
    return font, draw.textbbox((0, 0), text, font=font)


def _create_mascot_poster(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    headline: str = "",
    subtitle: str = "",
    theme: str = "celebration",
) -> Path:
    """Render a crisp, watermark-free Happy Toonzz poster using the real mascot sprites & looks."""
    AI_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    p_low = prompt.lower()

    theme_aliases = {
        "space": "astronaut",
        "galaxy": "astronaut",
        "hero": "superhero",
        "diwali": "festive",
        "deepavali": "festive",
        "snow": "winter",
        "cooking": "chef",
        "bakery": "chef",
        "garden": "meadow",
    }
    theme = theme_aliases.get((theme or "celebration").lower().strip(), (theme or "celebration").lower().strip())

    # Auto-detect theme if generic/celebration
    if theme == "celebration":
        if any(w in p_low for w in ("superhero", "hero", "fly", "cape")):
            theme = "superhero"
        elif any(w in p_low for w in ("space", "astronaut", "moon", "planet", "rocket")):
            theme = "astronaut"
        elif any(w in p_low for w in ("winter", "snow", "cold", "christmas")):
            theme = "winter"
        elif any(w in p_low for w in ("chef", "cake", "cupcake", "bake", "kitchen")):
            theme = "chef"
        elif any(w in p_low for w in ("diwali", "festival", "deepavali", "lights", "lamp")):
            theme = "festive"
        elif any(w in p_low for w in ("meadow", "garden", "flower", "butterfly", "morning")):
            theme = "meadow"

    # Extract clean milestone/poster text if headline wasn't explicitly supplied
    if not headline:
        m = re.search(r"(\d+\s*[kKmM]\+?\s*(?:followers?|subscribers?|subs|celebration)?)", prompt, re.IGNORECASE)
        if m:
            headline = m.group(1).upper().strip()
        elif theme == "festive":
            headline = "HAPPY FESTIVE WISHES!"
        elif theme == "superhero":
            headline = "SUPER HAPPY TOONZZ!"
        elif theme == "astronaut":
            headline = "SPACE ADVENTURE!"
        elif theme == "winter":
            headline = "COZY WINTER JOY!"
        elif theme == "chef":
            headline = "SWEET TREATS STUDIO!"
        else:
            headline = "HAPPY TOONZZ!"

    if not subtitle:
        if "28k" in headline.lower() or "follower" in headline.lower():
            subtitle = "Thank You So Much Happy Toonzz Family!"
        else:
            subtitle = "Small Character ... Big Happiness!"

    palettes = {
        "celebration": ((255, 214, 236), (174, 224, 255), (255, 118, 178), (155, 115, 242)),
        "festive": ((62, 24, 96), (255, 152, 68), (255, 216, 89), (255, 110, 148)),
        "superhero": ((110, 200, 255), (255, 240, 195), (251, 130, 129), (120, 203, 242)),
        "astronaut": ((26, 20, 70), (112, 74, 188), (245, 142, 192), (120, 203, 242)),
        "winter": ((124, 176, 238), (236, 246, 255), (245, 142, 192), (120, 203, 242)),
        "chef": ((255, 228, 212), (255, 246, 228), (245, 142, 192), (251, 130, 129)),
        "meadow": ((142, 222, 255), (232, 252, 205), (249, 204, 102), (245, 142, 192)),
    }
    top_c, bot_c, acc1, acc2 = palettes.get(theme, palettes["celebration"])

    canvas = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(top_c[0] * (1 - t) + bot_c[0] * t)
        g = int(top_c[1] * (1 - t) + bot_c[1] * t)
        b = int(top_c[2] * (1 - t) + bot_c[2] * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # Radiant burst & bokeh lights
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cx, cy = width // 2, int(height * 0.44)
    for i in range(16):
        a1 = math.radians(i * (360 / 16))
        a2 = math.radians(i * (360 / 16) + 10)
        rad = max(width, height)
        od.polygon(
            [(cx, cy), (cx + int(math.cos(a1) * rad), cy + int(math.sin(a1) * rad)), (cx + int(math.cos(a2) * rad), cy + int(math.sin(a2) * rad))],
            fill=(255, 255, 255, 26),
        )
    rng = secrets.SystemRandom()
    spark_colors = [(255, 255, 255, 185), acc1 + (180,), acc2 + (180,), (249, 204, 102, 195)]
    for _ in range(36):
        bx = rng.randint(30, width - 30)
        by = rng.randint(30, int(height * 0.72))
        br = rng.randint(7, 30)
        od.ellipse([bx - br, by - br, bx + br, by + br], fill=rng.choice(spark_colors))
    for _ in range(34):
        px = rng.randint(24, width - 24)
        py = rng.randint(24, int(height * 0.74))
        ps = rng.randint(6, 15)
        od.regular_polygon((px, py, ps), 5, rotation=rng.randint(0, 72), fill=rng.choice(spark_colors))
    canvas = Image.alpha_composite(canvas, overlay)

    # Top bar: crisp Happy Toonzz logo on left + pastel channel pill on right
    logo_path = SPRITES_DIR / "logo_happy_toonzz.png"
    if logo_path.exists():
        with Image.open(logo_path).convert("RGBA") as lg:
            lw = int(width * 0.24)
            lh = int(lg.height * (lw / lg.width))
            lg_r = lg.resize((lw, lh), Image.LANCZOS)
            canvas.alpha_composite(lg_r, (34, 24))

    # Reserve bottom celebration card area first so mascot centers cleanly above it
    card_w = int(width * 0.90)
    card_h = int(height * 0.245)
    card_x = (width - card_w) // 2
    card_y = height - card_h - 30

    # Choose main subject: themed 3D studio look OR full waving/butterfly mascot over glowing medallion
    look_map = {
        "superhero": IMAGES_DIR / "look-superhero.jpg",
        "astronaut": IMAGES_DIR / "look-astronaut.jpg",
        "winter": IMAGES_DIR / "look-winter.jpg",
        "chef": IMAGES_DIR / "look-chef.jpg",
    }

    if theme in look_map and look_map[theme].exists():
        with Image.open(look_map[theme]).convert("RGBA") as lk:
            avail_h = card_y - int(height * 0.14) - 24
            side = min(int(width * 0.56), avail_h)
            lk_r = lk.resize((side, side), Image.LANCZOS)
            mask = Image.new("L", (side, side), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, side, side], radius=44, fill=255)
            px = (width - side) // 2
            py = int(height * 0.14) + max(0, (avail_h - side) // 2)
            frame_bg = Image.new("RGBA", (side + 22, side + 22), (0, 0, 0, 0))
            ImageDraw.Draw(frame_bg).rounded_rectangle([0, 0, side + 22, side + 22], radius=52, fill=(255, 255, 255, 245))
            canvas.alpha_composite(frame_bg, (px - 11, py - 11))
            canvas.paste(lk_r, (px, py), mask)
    else:
        sprite_choice = SPRITES_DIR / ("pose_butterfly.png" if theme == "meadow" else "pose_waving.png")
        if not sprite_choice.exists():
            sprite_choice = SPRITES_DIR / "pose_jumping.png"
        if sprite_choice.exists():
            # Soft studio halo behind the mascot so fur glows harmoniously
            halo_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            hd = ImageDraw.Draw(halo_layer)
            halo_r = int(min(width, card_y) * 0.36)
            hcy = int((card_y + height * 0.10) * 0.50)
            hd.ellipse([width // 2 - halo_r, hcy - halo_r, width // 2 + halo_r, hcy + halo_r], fill=(255, 255, 255, 195))
            halo_layer = halo_layer.filter(ImageFilter.GaussianBlur(38))
            canvas = Image.alpha_composite(canvas, halo_layer)

            with Image.open(sprite_choice) as raw_sp:
                sp = _clean_mascot_sprite(raw_sp, remove_bl_corner=("pose_waving" in sprite_choice.name))
                target_h = int((card_y - int(height * 0.11)) * 0.94)
                target_w = int(sp.width * (target_h / sp.height))
                sp_r = sp.resize((target_w, target_h), Image.LANCZOS)
                sx = (width - target_w) // 2
                sy = card_y - target_h - int(height * 0.025)
                sh = Image.new("RGBA", sp_r.size, (52, 26, 58, 0))
                sh.putalpha(sp_r.getchannel("A").point(lambda a: int(a * 0.25)))
                sh = sh.filter(ImageFilter.GaussianBlur(12))
                canvas.alpha_composite(sh, (sx + 6, sy + 10))
                canvas.alpha_composite(sp_r, (sx, sy))

        # Cute circular medallion stickers (no rectangular edges)
        sticker_size = int(min(width, height) * 0.145)
        for exp_name, pos_x, ring_c in [
            ("expr_excited.png", int(width * 0.065), acc1),
            ("expr_winking.png", width - int(width * 0.065) - sticker_size, acc2),
        ]:
            ep = SPRITES_DIR / exp_name
            if ep.exists():
                badge = _circular_sticker(ep, sticker_size, ring_c)
                canvas.alpha_composite(badge, (pos_x, int(card_y * 0.52) - sticker_size // 2))

    # Bottom crisp celebration card with auto-fitted non-overlapping typography
    card_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card_layer)
    cd.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=34,
        fill=(255, 255, 255, 242),
        outline=acc1 + (255,),
        width=5,
    )
    canvas = Image.alpha_composite(canvas, card_layer)

    td = ImageDraw.Draw(canvas)
    max_txt_w = card_w - 64
    f_head, hbox = _fit_text_font(td, headline, max_txt_w, start_size=int(height * 0.060), min_size=24, bold=True)
    f_sub, sbox = _fit_text_font(td, subtitle, max_txt_w, start_size=int(height * 0.030), min_size=16, bold=True)

    ch = db.get_channel_profile()
    foot_txt = f"YouTube: {ch.get('youtube_handle', '@HappyToonzz-g7f')}   |   Instagram: {ch.get('instagram_handle', '@happy_toonzz')}"
    f_foot, fbox = _fit_text_font(td, foot_txt, max_txt_w, start_size=int(height * 0.021), min_size=14, bold=False)

    head_w, head_h = hbox[2] - hbox[0], hbox[3] - hbox[1]
    sub_w, sub_h = sbox[2] - sbox[0], sbox[3] - sbox[1]
    foot_w, foot_h = fbox[2] - fbox[0], fbox[3] - fbox[1]

    gap1 = max(16, int(card_h * 0.085))
    gap2 = max(14, int(card_h * 0.075))
    total_block_h = head_h + gap1 + sub_h + gap2 + foot_h
    block_top = card_y + max(18, (card_h - total_block_h) // 2)

    hx = (width - head_w) // 2 - hbox[0]
    hy = block_top - hbox[1]
    td.text((hx + 2, hy + 2), headline, font=f_head, fill=(238, 206, 228, 255))
    td.text((hx, hy), headline, font=f_head, fill=(52, 32, 48, 255))

    sub_top = block_top + head_h + gap1
    sx2 = (width - sub_w) // 2 - sbox[0]
    sy2 = sub_top - sbox[1]
    td.text((sx2, sy2), subtitle, font=f_sub, fill=(182, 54, 128, 255))

    foot_top = sub_top + sub_h + gap2
    fx = (width - foot_w) // 2 - fbox[0]
    fy = foot_top - fbox[1]
    td.text((fx, fy), foot_txt, font=f_foot, fill=(102, 78, 96, 255))

    fname = f"poster_{secrets.token_hex(6)}.jpg"
    out_path = AI_MEDIA_DIR / fname
    canvas.convert("RGB").save(out_path, quality=94)
    return out_path


def tool_create_image(
    prompt: str,
    api_key: str,
    use_mascot_reference: bool = True,
    aspect: str = "square",
    headline: str = "",
    subtitle: str = "",
    theme: str = "celebration",
) -> dict[str, Any]:
    """Generate an image via Gemini Nano Banana 2.1 (Paid Tier), real-mascot Studio Poster, or Pollinations."""
    AI_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    full_prompt = f"{MASCOT_VISUAL_DESC}. Scene: {prompt}" if use_mascot_reference else prompt
    width, height = (1280, 720) if aspect == "wide" else ((1024, 1280) if aspect == "portrait" else (1024, 1024))

    # 1. Generate with Gemini Nano Banana 2.1, using the mascot reference when available.
    if api_key:
        try:
            client = _make_client(api_key, timeout_ms=25000)
            inp: Any = full_prompt
            if use_mascot_reference and REF_SHEET_PATH.exists():
                b64_ref = base64.b64encode(REF_SHEET_PATH.read_bytes()).decode("ascii")
                inp = [
                    {"type": "image", "data": b64_ref, "mime_type": "image/jpeg"},
                    {
                        "type": "text",
                        "text": f"Keep exact appearance of the pastel kitten mascot from the reference sheet. Create a vibrant poster/scene: {prompt}",
                    },
                ]
            inter = client.interactions.create(model="gemini-nano-banana-2.1", input=inp)
            if inter.output_image and inter.output_image.data:
                fname = f"img_{secrets.token_hex(6)}.png"
                (AI_MEDIA_DIR / fname).write_bytes(base64.b64decode(inter.output_image.data))
                return {
                    "ok": True,
                    "engine": "gemini-nano-banana-2.1",
                    "media_type": "image",
                    "media_url": f"/static/ai_media/{fname}",
                }
            return {
                "ok": False,
                "error": "Gemini returned no image. Check that the API key has image-generation access.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Gemini image generation failed ({type(exc).__name__}). Check the Render API key, model access, and billing.",
            }

    # 2. When using mascot reference (or poster requested), render locally using REAL Happy Toonzz sprites & looks!
    if use_mascot_reference:
        poster_path = _create_mascot_poster(
            prompt=prompt,
            width=width,
            height=height,
            headline=headline,
            subtitle=subtitle,
            theme=theme,
        )
        return {
            "ok": True,
            "engine": "happy-toonzz-studio-compositor (uses official reference mascot & sprites)",
            "media_type": "image",
            "media_url": f"/static/ai_media/{poster_path.name}",
        }

    # 3. Non-mascot general image: try Pollinations free AI, else fall back to studio card
    try:
        seed = secrets.randbelow(999999)
        encoded = urllib.parse.quote(full_prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width={width}&height={height}&seed={seed}&nologo=true"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        img_bytes = urllib.request.urlopen(req, timeout=25).read()
        if len(img_bytes) > 5000:
            fname = f"img_{secrets.token_hex(6)}.jpg"
            (AI_MEDIA_DIR / fname).write_bytes(img_bytes)
            return {
                "ok": True,
                "engine": "pollinations-free",
                "media_type": "image",
                "media_url": f"/static/ai_media/{fname}",
            }
    except Exception:
        pass

    poster_path = _create_mascot_poster(prompt=prompt, width=width, height=height, headline=headline, subtitle=subtitle, theme=theme)
    return {
        "ok": True,
        "engine": "happy-toonzz-studio-compositor",
        "media_type": "image",
        "media_url": f"/static/ai_media/{poster_path.name}",
    }


def _render_motion_video_from_image(img_path: Path, out_mp4: Path, duration_sec: int, voice_text: str) -> bool:
    """Create a real 1280x720 animated Ken Burns MP4 with optional positive voiceover using FFmpeg."""
    if not FFMPEG_BIN or not img_path.exists():
        return False

    duration_sec = max(4, min(10, int(duration_sec)))
    fps = 30
    frames = duration_sec * fps
    tts_mp3 = out_mp4.with_suffix(".mp3")

    has_audio = False
    try:
        audio_bytes = synthesize_tts(voice_text)
        if audio_bytes:
            tts_mp3.write_bytes(audio_bytes)
            has_audio = True
    except Exception:
        has_audio = False

    # Build clean 1280x720 source without awkward cropping (scale-pad fit)
    prep_jpg = out_mp4.with_suffix(".prep.jpg")
    with Image.open(img_path).convert("RGB") as im:
        iw, ih = im.size
        scale = max(1280 / iw, 720 / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        resized = im.resize((nw, nh), Image.LANCZOS)
        x0, y0 = (nw - 1280) // 2, (nh - 720) // 2
        resized.crop((x0, y0, x0 + 1280, y0 + 720)).save(prep_jpg, quality=94)

    vf = (
        f"scale=1920:1080,"
        f"zoompan=z='min(zoom+0.0012,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1280x720:fps={fps},"
        f"format=yuv420p"
    )
    cmd = [FFMPEG_BIN, "-y", "-loop", "1", "-i", str(prep_jpg)]
    if has_audio:
        cmd.extend(["-i", str(tts_mp3)])
    cmd.extend(["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-t", str(duration_sec)])
    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    cmd.extend(["-movflags", "+faststart", str(out_mp4)])

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    prep_jpg.unlink(missing_ok=True)
    tts_mp3.unlink(missing_ok=True)
    return res.returncode == 0 and out_mp4.exists() and out_mp4.stat().st_size > 10000


def tool_generate_video(prompt: str, api_key: str, duration_sec: int = 6) -> dict[str, Any]:
    """Generate MP4 video via Gemini Omni 1.2 Flash (Paid Tier) or animated HD motion clip fallback."""
    AI_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"vid_{secrets.token_hex(6)}.mp4"
    out_mp4 = AI_MEDIA_DIR / fname

    # 1. Try Gemini Omni 1.2 Flash on v1beta (works on Paid Tier keys)
    if api_key:
        try:
            client = _make_client(api_key, api_version="v1beta", timeout_ms=55000)
            inp: Any = f"{MASCOT_VISUAL_DESC}. Animated 3D video scene: {prompt}"
            if REF_SHEET_PATH.exists():
                b64_ref = base64.b64encode(REF_SHEET_PATH.read_bytes()).decode("ascii")
                inp = [
                    {"type": "image", "data": b64_ref, "mime_type": "image/jpeg"},
                    {"type": "text", "text": f"Animate the pastel kitten mascot smoothly: {prompt}"},
                ]
            inter = client.interactions.create(model="gemini-omni-1.2-flash", input=inp)
            if getattr(inter, "output_video", None) and inter.output_video.data:
                out_mp4.write_bytes(base64.b64decode(inter.output_video.data))
                return {
                    "ok": True,
                    "engine": "gemini-omni-1.2-flash",
                    "media_type": "video",
                    "media_url": f"/static/ai_media/{fname}",
                }
        except Exception:
            pass

    # 2. Smart Free Fallback: generate wide 16:9 mascot scene image + smooth FFmpeg motion MP4
    img_res = tool_create_image(prompt, api_key, use_mascot_reference=True, aspect="wide")
    src_img = BASE_DIR / img_res["media_url"].lstrip("/") if img_res.get("ok") else BASE_DIR / "images" / "look-superhero.jpg"
    ok = _render_motion_video_from_image(
        src_img,
        out_mp4,
        duration_sec=duration_sec,
        voice_text=f"Happy Toonzz! {prompt[:140]}",
    )
    if ok:
        return {
            "ok": True,
            "engine": "studio-motion-ffmpeg (Free Tier fallback; add Paid Gemini key for Omni 1.2 Flash)",
            "media_type": "video",
            "media_url": f"/static/ai_media/{fname}",
        }
    return {"ok": False, "error": "Video rendering failed (FFmpeg unavailable or source error)."}


def tool_publish_look(
    image_url: str,
    title: str,
    subtitle: str,
    description: str,
    category: str = "adventure",
    badge: str = "Studio AI",
) -> dict[str, Any]:
    """Copy a generated AI image into images/uploads and publish it to the Looks gallery."""
    rel = image_url.lstrip("/")
    src = (BASE_DIR / rel).resolve()
    if not str(src).startswith(str(AI_MEDIA_DIR.resolve())) or not src.exists():
        return {"ok": False, "error": "Generated image not found."}

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower() or ".jpg"
    dest_name = f"look_{secrets.token_hex(6)}{ext}"
    shutil.copy2(src, UPLOAD_DIR / dest_name)

    look_id = db.add_look(
        title=title,
        subtitle=subtitle,
        category=category if category in {"adventure", "cozy"} else "adventure",
        badge=badge,
        image_path=f"images/uploads/{dest_name}",
        description=description,
    )
    return {"ok": True, "look_id": look_id, "image_path": f"images/uploads/{dest_name}"}


def _execute_tool(name: str, args: dict[str, Any], api_key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run an assistant tool call and return (result_dict, media_attachments)."""
    media_items: list[dict[str, Any]] = []

    if name == "web_search":
        res = tool_web_search(str(args.get("query", "")), api_key)
        return res, media_items

    if name == "search_youtube_video":
        res = tool_search_youtube_video(str(args.get("query", "")), db.get_youtube_api_key())
        if res.get("ok"):
            video = res["video"]
            media_items.append(
                {
                    "media_type": "youtube",
                    "media_url": video["video_id"],
                    "title": video["title"],
                }
            )
        return res, media_items

    if name == "create_image":
        res = tool_create_image(
            prompt=str(args.get("prompt", "")),
            api_key=api_key,
            use_mascot_reference=bool(args.get("use_mascot_reference", True)),
            aspect=str(args.get("aspect", "square")),
            headline=str(args.get("headline", "")),
            subtitle=str(args.get("subtitle", "")),
            theme=str(args.get("theme", "celebration")),
        )
        if res.get("ok"):
            media_items.append({"media_type": "image", "media_url": res["media_url"], "engine": res["engine"]})
        return res, media_items

    if name == "generate_video":
        res = tool_generate_video(
            prompt=str(args.get("prompt", "")),
            api_key=api_key,
            duration_sec=int(args.get("duration_sec", 6)),
        )
        if res.get("ok"):
            media_items.append({"media_type": "video", "media_url": res["media_url"], "engine": res["engine"]})
        return res, media_items

    if name == "update_channel_stats":
        allowed_updates: dict[str, str] = {}
        for k in (
            "youtube_subs",
            "youtube_videos",
            "instagram_followers",
            "facebook_followers",
            "facebook_url",
            "facebook_handle",
            "bio",
            "tagline",
        ):
            if k in args and args[k] is not None:
                val = str(args[k]).strip()
                if k == "facebook_url" and val and not val.startswith(("http://", "https://")):
                    continue
                allowed_updates[k] = val
        db.update_channel_profile(allowed_updates)
        return {"ok": True, "updated": allowed_updates}, media_items

    if name == "get_channel_overview":
        return {
            "channel": db.get_channel_profile(),
            "looks_count": len(db.get_looks()),
            "recent_messages": db.list_messages()[:5],
        }, media_items

    if name == "publish_image_as_look":
        res = tool_publish_look(
            image_url=str(args.get("image_url", "")),
            title=str(args.get("title", "AI Look")),
            subtitle=str(args.get("subtitle", "Custom Happy Toonzz look")),
            description=str(args.get("description", "Created inside Happy Toonzz AI Studio.")),
            category=str(args.get("category", "adventure")),
            badge=str(args.get("badge", "AI Art")),
        )
        return res, media_items

    return {"ok": False, "error": f"Unknown tool: {name}"}, media_items


def run_assistant_turn(
    user_id: int,
    prompt: str,
    image_data: bytes | None = None,
    image_mime_type: str = "",
) -> dict[str, Any]:
    """Run one AI Studio turn with automatic function calling and save messages to SQLite."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        return {"ok": False, "reply": "Please type or speak a message so I can help!"}

    db.add_ai_message(user_id, "user", clean_prompt)
    api_key = db.get_gemini_api_key()

    if not api_key:
        reply = (
            "Happy to help! Please paste your Gemini API key in the Master Settings card on this page "
            "so I can chat, search the web, create Happy Toonzz images, generate videos, and reply in voice."
        )
        db.add_ai_message(user_id, "assistant", reply)
        return {"ok": True, "reply": reply, "media": [], "sources": []}

    # Build recent conversational context
    past = db.list_ai_messages(user_id, limit=10)
    history: list[dict[str, Any]] = []
    for item in past[:-1]:
        step_type = "user_input" if item["role"] == "user" else "model_output"
        history.append({"type": step_type, "content": [{"type": "text", "text": item["text"]}]})
    current_content: list[dict[str, Any]] = [{"type": "text", "text": clean_prompt}]
    if image_data:
        current_content.append(
            {
                "type": "image",
                "data": base64.b64encode(image_data).decode("ascii"),
                "mime_type": image_mime_type,
            }
        )
    history.append({"type": "user_input", "content": current_content})

    client = _make_client(api_key, timeout_ms=25000)
    collected_media: list[dict[str, Any]] = []
    collected_tools: list[str] = []

    selected_model = CHAT_MODELS[0]
    interaction = None

    # Model call with automatic fallback on 503/429
    def _call_chat(hist: list[dict[str, Any]]):
        nonlocal selected_model
        last_err: Exception | None = None
        for m in [selected_model] + [x for x in CHAT_MODELS if x != selected_model]:
            try:
                res = client.interactions.create(
                    model=m,
                    store=False,
                    system_instruction=SYSTEM_PROMPT,
                    input=hist,
                    tools=TOOL_DECLARATIONS,
                )
                selected_model = m
                return res
            except Exception as err:
                last_err = err
                continue
        raise last_err or RuntimeError("All Gemini models failed.")

    try:
        interaction = _call_chat(history)
        for _round in range(4):
            steps = getattr(interaction, "steps", None) or []
            fc_steps = [s for s in steps if getattr(s, "type", "") == "function_call"]
            if not fc_steps:
                break

            for s in steps:
                history.append(s.model_dump())

            for fc in fc_steps:
                collected_tools.append(fc.name)
                res, media_out = _execute_tool(fc.name, dict(fc.arguments or {}), api_key)
                collected_media.extend(media_out)
                history.append(
                    {
                        "type": "function_result",
                        "name": fc.name,
                        "call_id": fc.id,
                        "result": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}],
                    }
                )
            interaction = _call_chat(history)

        reply_text = (interaction.output_text or "").strip() if interaction else ""
        if not reply_text:
            reply_text = "All done! I've completed your request inside the studio."

    except Exception as exc:
        reply_text = (
            f"I ran into a temporary API limit or connection issue ({type(exc).__name__}). "
            f"Please try again in a moment or verify your Gemini API key in Settings!"
        )

    last_media = collected_media[-1] if collected_media else {"media_type": "", "media_url": ""}
    db.add_ai_message(
        user_id,
        "assistant",
        reply_text,
        media_type=last_media["media_type"],
        media_url=last_media["media_url"],
        meta={"tools": collected_tools, "media": collected_media, "model": selected_model},
    )
    return {
        "ok": True,
        "reply": reply_text,
        "media": collected_media,
        "tools": collected_tools,
        "model": selected_model,
        "channel": db.get_channel_profile() if "update_channel_stats" in collected_tools else None,
    }


def _clean_text_for_speech(text: str) -> str:
    """Strip markdown links/formatting, URLs, and stray non-spoken symbols for natural TTS."""
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"[*_`#>]+", " ", cleaned)
    cleaned = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:900]


def synthesize_tts(text: str) -> bytes:
    """Synthesize warm, positive speech audio (MP3 via edge-tts, auto-switching English/Tamil)."""
    spoken = _clean_text_for_speech(text)
    if not spoken:
        return b""

    has_tamil = any("\u0b80" <= ch <= "\u0bff" for ch in spoken)
    voice = "ta-IN-PallaviNeural" if has_tamil else "en-IN-NeerjaExpressiveNeural"

    async def _run_edge() -> bytes:
        comm = edge_tts.Communicate(spoken, voice, rate="+5%")
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    try:
        # Handle running inside an existing asyncio loop (FastAPI) via thread or direct call
        try:
            asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_run_edge())).result(timeout=15)
        except RuntimeError:
            return asyncio.run(_run_edge())
    except Exception:
        pass

    # Fallback: Gemini 3.8 Flash TTS WAV if edge-tts unreachable
    api_key = db.get_gemini_api_key()
    if api_key:
        try:
            client = _make_client(api_key, api_version="v1beta", timeout_ms=15000)
            inter = client.interactions.create(
                model="gemini-3.8-flash-tts",
                input=[
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": spoken[:400],
                                "annotations": [{"type": "speech_metadata", "style": "warm, positive, and cheerful"}],
                            }
                        ],
                    }
                ],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": "Kore"}]},
            )
            if inter.output_audio and inter.output_audio.data:
                return base64.b64decode(inter.output_audio.data)
        except Exception:
            pass
    return b""
