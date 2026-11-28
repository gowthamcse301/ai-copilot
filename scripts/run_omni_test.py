import os
import sys
import time
import httpx
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable is not set.")
client = genai.Client(
    api_key=API_KEY,
    http_options=types.HttpOptions(api_version="v1beta", timeout=300000)
)

img_path = r"d:\Project AI\images\look-superhero.jpg"
out_path = r"d:\Project AI\assets\omni_clips\test_clip_01.mp4"
os.makedirs(os.path.dirname(out_path), exist_ok=True)

# --- Upload reference image ---
print("Uploading reference image...", flush=True)
uploaded = client.files.upload(file=img_path, config={"mime_type": "image/jpeg"})
print(f"Uploaded: {uploaded.uri}  mime={uploaded.mime_type}", flush=True)

# --- Build prompt ---
prompt = (
    "3D Pixar-style CGI animated movie scene featuring the cute chubby fluffy pastel baby kitten from the reference image, "
    "keeping the exact white face fur, pastel pink hair tuft on top of its head, huge sparkling purple eyes, rosy cheeks, "
    "pink arms, yellow right leg, sky-blue left leg, and fluffy pink tail, wearing its coral-red cape and blue star tunic. "
    "Standing in a bright sunlit green flower meadow with golden sunbeams and drifting clouds, the kitten cheerfully waves both paws, "
    "bounces and dances rhythmically to an upbeat happy beat, blinks its sparkling purple eyes, and smiles joyfully. "
    "Smooth lively 3D character animation, rich animated cinema lighting, single unbroken shot, no text, no subtitles, no watermarks."
)

# --- Generate via interactions API ---
print("Sending video generation request to gemini-omni-1.2-flash v1beta...", flush=True)
interaction = client.interactions.create(
    model="gemini-omni-1.2-flash",
    input=[
        {"type": "image", "uri": uploaded.uri, "mime_type": uploaded.mime_type},
        {"type": "text", "text": prompt},
    ],
    response_format={
        "type": "video",
        "delivery": "uri",
        "aspect_ratio": "16:9",
        "duration": "6s",
        "resolution": "720p",
    },
)

print(f"Interaction ID: {interaction.id}  Status: {getattr(interaction, 'status', '?')}", flush=True)

# Poll if not immediately done
max_wait = 300
interval = 10
elapsed = 0
while True:
    status = getattr(interaction, "status", None)
    out_vid = getattr(interaction, "output_video", None)
    if out_vid and getattr(out_vid, "uri", None):
        break
    if status in ("failed", "error"):
        raise RuntimeError(f"Generation failed. Steps: {getattr(interaction, 'steps', None)}")
    if elapsed >= max_wait:
        raise TimeoutError(f"Timed out after {max_wait}s. Last status: {status}")
    print(f"  Polling... status={status} elapsed={elapsed}s", flush=True)
    time.sleep(interval)
    elapsed += interval
    interaction = client.interactions.get(name=interaction.id)

# --- Download result ---
print(f"Video ready! URI={interaction.output_video.uri}", flush=True)
video_uri = interaction.output_video.uri

# Download with httpx
response = httpx.get(video_uri, follow_redirects=True, timeout=120)
response.raise_for_status()
with open(out_path, "wb") as f:
    f.write(response.content)

size = os.path.getsize(out_path)
print(f"SUCCESS: {out_path}  ({size:,} bytes)", flush=True)
