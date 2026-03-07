from flask import Flask
from threading import Thread
import os, sys, io, json, random, hashlib, logging, re, asyncio
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import aiohttp
import discord
from discord.ext import commands, tasks

try:
    from PIL import Image
except Exception:
    Image = None

_flask_app = Flask("")

@_flask_app.route("/")
def _home():
    return "Bot is alive! 🔥"

def _run_flask():
    port = int(os.environ.get("PORT", 10000))
    _flask_app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=_run_flask, daemon=True)
    t.start()

NSFW_MODE = True

TOKEN = os.getenv("TOKEN", "")
WAIFUIM_API_KEY = os.getenv("WAIFUIM_API_KEY", "")
DANBOORU_USER = os.getenv("DANBOORU_USER", "")
DANBOORU_API_KEY = os.getenv("DANBOORU_API_KEY", "")
GELBOORU_API_KEY = os.getenv("GELBOORU_API_KEY", "")
GELBOORU_USER = os.getenv("GELBOORU_USER", "")
E621_USER = os.getenv("E621_USER", "")
E621_API_KEY = os.getenv("E621_API_KEY", "")
WAIFU_IT_API_KEY = os.getenv("WAIFU_IT_API_KEY", "")

DEBUG_FETCH = str(os.getenv("DEBUG_FETCH", "")).strip().lower() in ("1","true","yes","on")
TRUE_RANDOM = str(os.getenv("TRUE_RANDOM", "")).strip().lower() in ("1","true","yes")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "14"))
DISCORD_MAX_UPLOAD = int(os.getenv("DISCORD_MAX_UPLOAD", str(8 * 1024 * 1024)))
HEAD_SIZE_LIMIT = DISCORD_MAX_UPLOAD
DATA_FILE = os.getenv("DATA_FILE", "data_nsfw.json")
AUTOSAVE_INTERVAL = int(os.getenv("AUTOSAVE_INTERVAL", "30"))
FETCH_ATTEMPTS = int(os.getenv("FETCH_ATTEMPTS", "40"))
MAX_USED_GIFS_PER_USER = int(os.getenv("MAX_USED_GIFS_PER_USER", "1000"))

VC_CHANNEL_ID = int(os.getenv("VC_CHANNEL_ID", "0"))
_VC_IDS_RAW = os.getenv("VC_IDS", "")
VC_IDS = [int(x.strip()) for x in _VC_IDS_RAW.split(",") if x.strip().isdigit()] if _VC_IDS_RAW.strip() else []

logging.basicConfig(level=logging.DEBUG if DEBUG_FETCH else logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("waifu-bot")

if not VC_IDS:
    logger.warning("[VC] VC_IDS env var not set — voice channel features disabled.")
if not VC_CHANNEL_ID:
    logger.warning("[VC] VC_CHANNEL_ID env var not set — text channel messages disabled.")

if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({"sent_history": {}, "vc_state": {}}, f, indent=2)

with open(DATA_FILE, "r") as f:
    data = json.load(f)

data.setdefault("sent_history", {})
data.setdefault("vc_state", {})

def save_data():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Save failed: {e}")

SPICY_TAGS = [
    "ahegao", "creampie", "cum_inside", "gangbang", "double_penetration",
    "deepthroat", "paizuri", "titfuck", "throatfuck", "facesitting",
    "doggy_style", "missionary", "squirting", "bondage", "bdsm",
    "tentacles", "orgasm", "riding", "thighjob", "cumshot", "blowjob",
    "anal", "pussy", "hardcore", "futanari", "public", "group",
    "nude", "naked", "sex", "handjob", "footjob", "femdom",
    "harem", "milf", "big_breasts", "large_breasts", "busty",
    "ass", "buttjob", "spanking", "hypnosis", "mind_break",
    "pov", "solo_female", "multiple_boys", "cum_on_face",
    "spread_legs", "cum_on_body", "breast_grab", "nipples",
]

_seed_gif_tags = [
    "hentai", "sex", "blowjob", "anal", "creampie", "cumshot", "ahegao",
    "paizuri", "gangbang", "deepthroat", "tentacles", "futanari", "orgasm",
    "squirt", "bondage", "milf", "oppai", "pussy", "hardcore", "animated",
    "nude", "naked", "big_breasts", "femdom", "pov", "ass", "busty",
    "nipples", "spread_legs", "riding", "deepthroat",
]

GIF_TAGS = list(dict.fromkeys(_seed_gif_tags))

async def _download_bytes(session, url, size_limit=HEAD_SIZE_LIMIT, timeout=REQUEST_TIMEOUT):
    try:
        to = aiohttp.ClientTimeout(total=timeout)
        async with session.get(url, timeout=to, allow_redirects=True) as resp:
            if resp.status != 200:
                return None, None
            ctype = resp.content_type or ""
            total, chunks = 0, []
            async for chunk in resp.content.iter_chunked(1024):
                chunks.append(chunk)
                total += len(chunk)
                if total > size_limit:
                    return None, ctype
            return b"".join(chunks), ctype
    except Exception:
        return None, None

async def compress_image(image_bytes, target_size=DISCORD_MAX_UPLOAD):
    if not Image: return image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.format == "GIF": return image_bytes
        output = io.BytesIO()
        quality = 95
        while quality > 10:
            output.seek(0); output.truncate()
            img.save(output, format=img.format or "JPEG", quality=quality, optimize=True)
            if output.tell() <= target_size: return output.getvalue()
            quality -= 10
        return output.getvalue()
    except Exception:
        return image_bytes

async def _gelbooru_compat(session, base_url, api_key=None, user_id=None, extra_tags=None):
    try:
        tags = ["rating:explicit"]
        if random.random() < 0.85:
            tags.append(random.choice(SPICY_TAGS))
        if random.random() < 0.60:
            tags.append("animated")
        if extra_tags:
            tags.extend(extra_tags)
        params = {
            "page": "dapi", "s": "post", "q": "index",
            "json": "1", "tags": " ".join(tags), "limit": 20,
        }
        if api_key and user_id:
            params["api_key"] = api_key
            params["user_id"] = user_id
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(base_url, params=params, headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            posts = payload if isinstance(payload, list) else payload.get("post", [])
            if not posts: return None, None, None
            post = random.choice(posts)
            gif_url = post.get("file_url")
            if not gif_url or gif_url.lower().endswith((".webm", ".mp4", ".swf")):
                return None, None, None
            return gif_url, base_url, post
    except Exception:
        return None, None, None

async def fetch_rule34(session, positive=None):
    try:
        tags = ["rating:explicit"]
        if random.random() < 0.90: tags.append(random.choice(SPICY_TAGS))
        if random.random() < 0.70: tags.append("animated")
        params = {
            "page": "dapi", "s": "post", "q": "index",
            "json": "1", "tags": " ".join(tags), "limit": 120,
        }
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://api.rule34.xxx/index.php", params=params, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            posts = await resp.json(content_type=None)
            if not posts: return None, None, None
            post = random.choice(posts)
            gif_url = post.get("file_url")
            if not gif_url or gif_url.lower().endswith((".webm", ".mp4", ".swf")):
                return None, None, None
            return gif_url, "rule34", post
    except Exception:
        return None, None, None

async def fetch_gelbooru(session, positive=None):
    url, _, post = await _gelbooru_compat(session, "https://gelbooru.com/index.php", GELBOORU_API_KEY or None, GELBOORU_USER or None)
    return url, "gelbooru", post

async def fetch_nekosapi(session, positive=None):
    try:
        params = {"rating": "explicit", "limit": 5}
        if random.random() < 0.80:
            params["tags"] = random.choice(SPICY_TAGS)
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://api.nekosapi.com/v4/images/random", params=params, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            images = payload.get("items", [])
            if not images: return None, None, None
            img = random.choice(images)
            return img.get("url"), "nekosapi", img
    except Exception:
        return None, None, None

async def fetch_konachan(session, positive=None):
    try:
        tags = ["rating:explicit"]
        if random.random() < 0.85: tags.append(random.choice(SPICY_TAGS).replace("_", " "))
        if random.random() < 0.60: tags.append("animated")
        params = {"tags": " ".join(tags), "limit": 20}
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://konachan.com/post.json", params=params, headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            posts = await resp.json(content_type=None)
            if not posts: return None, None, None
            image_posts = [p for p in posts if p.get("file_url") and not p.get("file_url", "").lower().endswith((".webm", ".mp4"))]
            if not image_posts: return None, None, None
            post = random.choice(image_posts)
            return post.get("file_url"), "konachan", post
    except Exception:
        return None, None, None

async def fetch_nekobot(session, positive=None):
    try:
        category = random.choice(["hentai", "hentai_anal", "hass", "hboobs", "hthigh", "paizuri", "tentacle", "pgif"])
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(f"https://nekobot.xyz/api/image?type={category}", timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            if not payload.get("success"): return None, None, None
            return payload.get("message"), f"nekobot_{category}", payload
    except Exception:
        return None, None, None

async def fetch_danbooru(session, positive=None):
    try:
        tags = ["rating:explicit"]
        if random.random() < 0.85: tags.append(random.choice(SPICY_TAGS))
        if random.random() < 0.60: tags.append("animated")
        params = {"tags": " ".join(tags), "limit": 20, "random": "true"}
        headers = {}
        if DANBOORU_USER and DANBOORU_API_KEY:
            import base64
            creds = base64.b64encode(f"{DANBOORU_USER}:{DANBOORU_API_KEY}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://danbooru.donmai.us/posts.json", params=params, headers=headers or None, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            posts = await resp.json(content_type=None)
            if not posts: return None, None, None
            post = random.choice(posts)
            gif_url = post.get("file_url") or post.get("large_file_url")
            if not gif_url or gif_url.lower().endswith((".webm", ".mp4", ".swf")):
                return None, None, None
            return gif_url, "danbooru", post
    except Exception:
        return None, None, None

async def fetch_nekos_life(session, positive=None):
    try:
        category = random.choice(["blowjob", "cum", "hentai", "classical", "ero", "spank", "lewd", "feet"])
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(f"https://nekos.life/api/v2/img/{category}", timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            return payload.get("url"), f"nekos_life_{category}", payload
    except Exception:
        return None, None, None

async def fetch_tbib(session, positive=None):
    url, _, post = await _gelbooru_compat(session, "https://tbib.org/index.php")
    return url, "tbib", post

async def fetch_xbooru(session, positive=None):
    url, _, post = await _gelbooru_compat(session, "https://xbooru.com/index.php")
    return url, "xbooru", post

async def fetch_realbooru(session, positive=None):
    url, _, post = await _gelbooru_compat(session, "https://realbooru.com/index.php")
    return url, "realbooru", post

async def fetch_waifu_im(session, positive=None):
    try:
        q = positive or random.choice(GIF_TAGS)
        params = {"included_tags": q, "is_nsfw": "true", "limit": 8}
        headers = {}
        if WAIFUIM_API_KEY:
            headers["Authorization"] = f"Bearer {WAIFUIM_API_KEY}"
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://api.waifu.im/search", params=params, headers=headers or None, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            images = payload.get("images", [])
            if not images: return None, None, None
            img = random.choice(images)
            return img.get("url"), "waifu_im", img
    except Exception:
        return None, None, None

async def fetch_paheal(session, positive=None):
    try:
        tag = random.choice(SPICY_TAGS).replace("_", " ")
        params = {"tags": tag, "limit": 50}
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://rule34.paheal.net/api/danbooru/find_posts/index.xml", params=params, headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            text = await resp.text()
            root = ET.fromstring(text)
            posts = root.findall(".//post")
            if not posts: return None, None, None
            post = random.choice(posts)
            gif_url = post.get("file_url")
            if not gif_url or gif_url.lower().endswith((".webm", ".mp4", ".swf", ".flv")):
                return None, None, None
            return gif_url, "paheal", {"file_url": gif_url}
    except Exception:
        return None, None, None

async def fetch_waifu_it(session, positive=None):
    if not WAIFU_IT_API_KEY: return None, None, None
    try:
        category = random.choice(["creampie", "thighjob", "ero", "paizuri", "oppai", "anal", "blowjob", "hentai"])
        hdrs = {"Authorization": WAIFU_IT_API_KEY}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(f"https://waifu.it/api/v4/{category}", headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            return payload.get("url"), f"waifu_it_{category}", payload
    except Exception:
        return None, None, None

async def fetch_nekos_moe(session, positive=None):
    try:
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://nekos.moe/api/v1/random/image?nsfw=true&count=1", headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            images = payload.get("images", [])
            if not images: return None, None, None
            img = random.choice(images)
            img_id = img.get("id")
            if not img_id: return None, None, None
            return f"https://nekos.moe/image/{img_id}.jpg", "nekos_moe", img
    except Exception:
        return None, None, None

async def fetch_waifu_pics(session, positive=None):
    try:
        category = random.choice(["waifu", "neko", "trap", "blowjob"])
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(f"https://api.waifu.pics/nsfw/{category}", timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            return payload.get("url") or payload.get("image"), f"waifu_pics_{category}", payload
    except Exception:
        return None, None, None

async def fetch_e621(session, positive=None):
    try:
        tags = ["rating:explicit", "order:random"]
        if random.random() < 0.85: tags.append(random.choice(SPICY_TAGS))
        params = {"tags": " ".join(tags), "limit": 20}
        hdrs = {"User-Agent": "WaifuBot/1.0 (by discord_bot_operator on e621)"}
        auth = aiohttp.BasicAuth(E621_USER, E621_API_KEY) if E621_USER and E621_API_KEY else None
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://e621.net/posts.json", params=params, headers=hdrs, auth=auth, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            posts = payload.get("posts", [])
            if not posts: return None, None, None
            image_posts = [p for p in posts if p.get("file", {}).get("ext") in ("jpg","jpeg","png","gif","webp")]
            if not image_posts: return None, None, None
            post = random.choice(image_posts)
            gif_url = post.get("file", {}).get("url")
            if not gif_url: return None, None, None
            return gif_url, "e621", post
    except Exception:
        return None, None, None

async def fetch_yandere(session, positive=None):
    try:
        tags = ["rating:explicit", "order:random"]
        if random.random() < 0.85: tags.append(random.choice(SPICY_TAGS).replace("_", " "))
        params = {"tags": " ".join(tags), "limit": 20}
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://yande.re/post.json", params=params, headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            posts = await resp.json(content_type=None)
            if not posts: return None, None, None
            image_posts = [p for p in posts if p.get("file_url") and not p.get("file_url", "").lower().endswith((".webm", ".mp4"))]
            if not image_posts: return None, None, None
            post = random.choice(image_posts)
            return post.get("file_url"), "yandere", post
    except Exception:
        return None, None, None

async def fetch_hypnohub(session, positive=None):
    url, _, post = await _gelbooru_compat(session, "https://hypnohub.net/index.php")
    return url, "hypnohub", post

_BASE_PROVIDERS = [
    ("rule34", fetch_rule34, 45),
    ("gelbooru", fetch_gelbooru, 18),
    ("nekosapi", fetch_nekosapi, 15),
    ("konachan", fetch_konachan, 12),
    ("nekobot", fetch_nekobot, 10),
    ("danbooru", fetch_danbooru, 8),
    ("nekos_life", fetch_nekos_life, 8),
    ("tbib", fetch_tbib, 7),
    ("xbooru", fetch_xbooru, 7),
    ("realbooru", fetch_realbooru, 6),
    ("waifu_im", fetch_waifu_im, 5),
    ("paheal", fetch_paheal, 5),
    ("waifu_it", fetch_waifu_it, 4),
    ("nekos_moe", fetch_nekos_moe, 3),
    ("waifu_pics", fetch_waifu_pics, 2),
]

_NSFW_EXTRA_PROVIDERS = [
    ("e621", fetch_e621, 18),
    ("yandere", fetch_yandere, 14),
    ("hypnohub", fetch_hypnohub, 6),
]

PROVIDERS = _BASE_PROVIDERS + _NSFW_EXTRA_PROVIDERS

def _hash_url(url):
    return hashlib.md5(url.encode()).hexdigest()

def _choose_provider():
    if TRUE_RANDOM: return random.choice(PROVIDERS)
    weights = [w for _, _, w in PROVIDERS]
    return random.choices(PROVIDERS, weights=weights, k=1)[0]

async def _fetch_one(session, used_hashes=None):
    if used_hashes is None: used_hashes = set()
    name, fetch_func, _ = _choose_provider()
    try:
        url, source, meta = await fetch_func(session)
        if url:
            h = _hash_url(url)
            if h not in used_hashes:
                return url, source, meta, h
    except Exception:
        pass
    return None, None, None, None

async def fetch_gif(session, user_id=None):
    uid = str(user_id) if user_id else "global"
    history = data["sent_history"].setdefault(uid, [])
    used = set(history)
    for _ in range(FETCH_ATTEMPTS):
        url, source, meta, url_hash = await _fetch_one(session, used)
        if url:
            history.append(url_hash)
            if len(history) > MAX_USED_GIFS_PER_USER: history.pop(0)
            data["sent_history"][uid] = history
            return url, source, meta
    return None, None, None

JOIN_GREETINGS = [
    "💋 {display_name} slips in like a slow caress — the room just warmed up.",
    "🔥 {display_name} arrived, tracing heat across the air; someone hold the temperature.",
    "✨ {display_name} joins — all eyes and soft smiles. Dare to stir trouble?",
    "😈 {display_name} steps through the door with a dangerous smile and a hungry look.",
    "👀 {display_name} appeared — sudden quiet, then the world leans in.",
    "🖤 {display_name} joined, breath shallow, pulse audible — tempting, isn't it?",
    "🌙 {display_name} glides in as if they own the moment — claim it or be claimed.",
    "🕯️ {display_name} arrives wrapped in dusk and whispering promises.",
    "🍷 {display_name} joined — like a warm pour, smooth and slow.",
    "🥀 {display_name} walked in with a smile that asked for trouble.",
    "🕶️ {display_name} stepped in cool, but the air around them is anything but.",
    "💎 {display_name} joined — rare, polished, and distractingly beautiful.",
    "👑 {display_name} arrived; treat them like royalty or lose the crown.",
    "🌫️ {display_name} drifted in; the air tastes sweeter already.",
    "🪞 {display_name} joined — catch their reflection if you dare.",
    "⚡ {display_name} joined and the electricity in the room changed lanes.",
    "🧠 {display_name} arrived with a mind on fire — play smart, play dangerous.",
    "💋 {display_name} slipped in with a grin; the night just leaned forward.",
    "🩸 {display_name} joined — bold, a little wicked, entirely noticed.",
    "🐍 {display_name} slithered in, sly and sure. Watch your step.",
    "🌒 {display_name} arrived quietly — but the silence hums with intent.",
    "🧿 {display_name} joined; the air says stay, the body says closer.",
    "🎭 {display_name} entered with a mischievous tilt — masks are optional.",
    "🪶 {display_name} stepped in with featherlight steps and heavy intent.",
    "🩶 {display_name} joined, calm on the outside, simmering on the inside.",
    "👁️ {display_name} arrived; one look and the night got complicated.",
    "🕸️ {display_name} stepped into the web — enjoy getting tangled.",
    "🌘 {display_name} joined — shadow-soft and dangerously inviting.",
    "🧊 {display_name} arrived cool, but their presence melts the room.",
    "⚖️ {display_name} walked in — the balance shifted toward desire.",
    "🪄 {display_name} joined and something magical tightened in the chest.",
    "🌺 {display_name} arrived like a slow bloom — intoxicating.",
    "🫦 {display_name} joined — lips curved, promise implied.",
    "🎶 {display_name} arrived on a private rhythm; follow if you want to sway.",
    "🌪️ {display_name} joined — whirlwinds look calm until they hit.",
    "🖤 {display_name} slipped in, hush and hunger wrapped together.",
    "💼 {display_name} entered composed — look closer, there's mischief under the suit.",
    "💫 {display_name} joined and the room took a breathless pause.",
    "🩸 {display_name} enters — the room tightens like it knows what's coming.",
    "🖤 {display_name} joined. Lock your thoughts, not your doors.",
    "🌑 {display_name} stepped in — eyes linger longer than they should.",
    "😈 {display_name} arrived with intent. Pretend you don't feel it.",
    "🕷️ {display_name} entered — something just wrapped around your focus.",
    "🔥 {display_name} joined. Heat climbs. Control slips.",
    "👁️ {display_name} is here — watched before watching back.",
    "🖤 {display_name} arrived. Breathe slow. This one doesn't rush.",
    "🌒 {display_name} slipped in — confidence sharp enough to cut.",
    "🩶 {display_name} joined quietly. Dangerous people don't announce themselves.",
    "😼 {display_name} joined with a look that asks permission from no one.",
    "🕯️ {display_name} arrived — slow burn, no mercy.",
    "🐍 {display_name} slid in — smooth, patient, inevitable.",
    "🧿 {display_name} joined — attention captured, consent assumed.",
    "🕶️ {display_name} arrived — unreadable, unbothered, unresisted.",
    "🎯 {display_name} joined — precise, unavoidable, magnetic.",
    "🔒 {display_name} arrived — doors close a little tighter.",
    "🗝️ {display_name} unlocked the room; keys aren't always literal.",
    "🧨 {display_name} entered — contained chaos with an inviting grin.",
    "🌌 {display_name} joined — vast, dark, and impossible to ignore.",
    "🖤 {display_name} slips in — the shadows made room for them.",
    "🌑 {display_name} arrived; the air tightened at their name.",
    "🩸 {display_name} walked in with an intent that hums.",
    "🔥 {display_name} entered — eyes sharpen, breaths slow.",
    "😈 {display_name} came — dangerous grace, measured steps.",
    "🕯️ {display_name} arrives, dusk trailing like a promise.",
    "👁️ {display_name} joined — every light found them first.",
    "🐍 {display_name} slipped through; patience wrapped around them.",
    "⚡ {display_name} stepped in and the dark learned to behave.",
    "🗝️ {display_name} unlocked eyes; rooms rearranged themselves.",
    "🖤 {display_name} arrived — presence heavy, attention willing.",
    "🌘 {display_name} joined; the hush leaned forward.",
    "🕶️ {display_name} walked in — unreadable, owning the pause.",
    "🔒 {display_name} arrived; the world closed in tighter.",
    "🧿 {display_name} came — watched and watching back.",
    "🩶 {display_name} stepped through — calm, collected, inevitable.",
    "🌫️ {display_name} appears like smoke — hard to push away.",
    "🐺 {display_name} joined alone; the pack noticed.",
    "🪞 {display_name} arrived — reflection trembles when they move.",
    "🎯 {display_name} entered — precise, unavoidable.",
    "🧨 {display_name} slipped in; contained trouble with a smile.",
    "🪄 {display_name} arrived — small magic, large consequence.",
    "🕷️ {display_name} stepped into the web — enjoy the pull.",
    "🌪️ {display_name} entered — calm before the pleasant storm.",
    "💎 {display_name} joined — cold, beautiful, demanding regard.",
    "🫦 {display_name} arrived; lips quiet, intentions loud.",
    "🎭 {display_name} came with a hidden grin — masks not required.",
    "🍷 {display_name} entered like a slow pour; taste lingers.",
    "🐉 {display_name} arrived — ancient hush follows new steps.",
    "🧠 {display_name} joined; thinking people are deliciously dangerous.",
    "💫 {display_name} arrives and the room holds its breath.",
    "🌺 {display_name} stepped in — beauty that commands.",
    "🕯️ {display_name} came — slow flame, sharp heat.",
    "🪶 {display_name} drifted in; soft steps, heavy intent.",
    "⚖️ {display_name} joined — balance subtly tipped.",
    "🌌 {display_name} arrived — vast, dark, magnetic.",
    "🔮 {display_name} entered; futures leaned toward them.",
    "🧊 {display_name} came cool, but the air betrayed them.",
    "🖤 {display_name} arrived — possession in every glance.",
    "🌒 {display_name} stepped in and the night acknowledged them.",
    "👑 {display_name} joined; crowns fit easily on slow smiles.",
    "🔥 {display_name} slipped in — embers trailed their footsteps.",
    "🕶️ {display_name} arrived — no one dares stare too long.",
    "🩸 {display_name} entered; the room kept a small, sharp memory.",
    "🧿 {display_name} joined — attention, harvested whole.",
    "🗝️ {display_name} stepped through; doors sighed closed behind them.",
    "🐍 {display_name} arrived — elegant, patient, inevitable.",
    "🌘 {display_name} joined; darkness welcomed a familiar shape.",
    "🎶 {display_name} entered on a low, dangerous rhythm.",
    "🪞 {display_name} came — mirrors preferred their reflection tonight.",
    "🥀 {display_name} arrives — wilted petals, potent scent.",
    "🕸️ {display_name} joined; the web tightened delightfully.",
    "💼 {display_name} stepped in — composed, with a hidden edge.",
    "🧨 {display_name} arrived — quiet fuse, loud results.",
    "🫀 {display_name} came — heartbeats answered them.",
    "🪙 {display_name} arrived — coin dropped, choices made.",
    "🐾 {display_name} joined — footsteps marking territory.",
    "🖤 {display_name} entered and the room learned to follow their lead.",
    "⚡ {display_name} arrived; sparks were not accidental.",
    "🌑 {display_name} joined — welcome to the darker side of curiosity.",
]

LEAVE_GREETINGS = [
    "🌙 {display_name} slips away — the afterglow lingers.",
    "🖤 {display_name} left; the room exhales and remembers the warmth.",
    "🌑 {display_name} drifted out, leaving charged silence in their wake.",
    "👀 {display_name} left — eyes still searching the doorway.",
    "🕯️ {display_name} exited; the candle burned a little brighter while they were here.",
    "😈 {display_name} disappeared — mischief properly recorded.",
    "🌫️ {display_name} faded into the night; whispers followed.",
    "🧠 {display_name} walked away smiling — plotting, no doubt.",
    "🕶️ {display_name} slipped out unnoticed — or cleverly unnoticed.",
    "💎 {display_name} left — the room is slightly less dazzling.",
    "🔥 {display_name} exited — the temperature is slow to drop.",
    "🩸 {display_name} is gone; the air still carries a memory.",
    "🐍 {display_name} slithered away — patient until next time.",
    "🪞 {display_name} stepped out; reflections linger.",
    "👑 {display_name} left — brief reign, lasting impression.",
    "🌒 {display_name} faded — the shadow kept the scent.",
    "💋 {display_name} slipped away — lips still warm with goodbye.",
    "🕷️ {display_name} left the web — threads still vibrate.",
    "🧿 {display_name} exited — the room blinked and they were gone.",
    "⚡ {display_name} departed — static still crackles.",
    "🌺 {display_name} left — fragrance lingers like a second presence.",
    "🎶 {display_name} stepped out mid-beat; the rhythm misses them.",
    "🪄 {display_name} vanished — no trick, just absence.",
    "🌘 {display_name} left with the dark; the room forgot to breathe.",
    "⚖️ {display_name} exited — balance quietly unsettled.",
    "🫦 {display_name} left — the promise hangs unfinished.",
    "🌪️ {display_name} is gone; the calm feels suspicious.",
    "🗝️ {display_name} locked up and left — what did they take?",
    "🩶 {display_name} stepped out quietly — most dangerous departures are.",
    "🐺 {display_name} went back to the dark; the pack felt it.",
    "🔒 {display_name} left — something closed with them.",
    "🎯 {display_name} exited precisely — no wasted movement.",
    "🌌 {display_name} faded into the vast — see you on the other side.",
    "🐾 {display_name} left footprints; warmth on the floor.",
    "🧨 {display_name} is gone — the fuse remembers the spark.",
    "🔮 {display_name} exited; the future tilts a little.",
    "🌑 {display_name} stepped into the night — gracefully, inevitably.",
    "😈 {display_name} vanished — the mischief is still here somewhere.",
    "🕯️ {display_name} left; the flame lowered but didn't die.",
    "🖤 {display_name} gone. The hush that replaced them says everything.",
    "💫 {display_name} left; the room adjusts slowly to less light.",
    "🐉 {display_name} departed — the old presence lingers like myth.",
    "🌺 {display_name} slipped out; the air still smells of them.",
    "🩸 {display_name} left. Something small and sharp stayed.",
    "🪙 {display_name} exited — the coin has been spent.",
    "🎭 {display_name} removed their mask on the way out — imagine that.",
    "🖤 {display_name} is gone. Door's open. Heart isn't.",
    "🌒 {display_name} drifted away — half the night went with them.",
    "🧊 {display_name} left; a cool vacancy settled in.",
    "👁️ {display_name} stopped watching — the room feels less seen.",
    "🔥 {display_name} left; the embers hold the shape of their heat.",
    "🗡️ {display_name} departed — the edge lingered.",
    "🌫️ {display_name} dissolved into the air — breathe deep.",
    "⚡ {display_name} stepped out; the sparks miss them already.",
    "🎯 {display_name} gone. The precision of their absence is felt.",
    "🕸️ {display_name} left the web spinning — enjoy the vibration.",
    "🪞 {display_name} exited — the glass is duller without them.",
    "💎 {display_name} gone — the room noticed the drop in brilliance.",
    "🧿 {display_name} stepped away — but the eye still watches.",
    "🐍 {display_name} coiled away into the dark. Until next time.",
]

async def send_greeting_embed(channel, session, greeting_text, image_url, member, send_to_dm=None):
    try:
        image_bytes, content_type = await _download_bytes(session, image_url)
        if image_bytes and len(image_bytes) > DISCORD_MAX_UPLOAD:
            image_bytes = await compress_image(image_bytes)
        if not image_bytes or len(image_bytes) > DISCORD_MAX_UPLOAD:
            await channel.send(greeting_text)
            return

        lurl = image_url.lower()
        ctype = content_type or ""
        ext = ".gif" if "gif" in lurl or "gif" in ctype else ".jpg"
        if "png" in lurl or "png" in ctype: ext = ".png"
        elif "webp" in lurl or "webp" in ctype: ext = ".webp"

        filename = f"waifu{ext}"

        ch_file = discord.File(io.BytesIO(image_bytes), filename=filename)
        ch_embed = discord.Embed(description=greeting_text, color=discord.Color.from_rgb(220, 53, 69))
        ch_embed.set_author(name=member.display_name, icon_url=getattr(member.display_avatar, "url", None))
        ch_embed.set_image(url=f"attachment://{filename}")
        ch_embed.set_footer(text="Your Personal waifu")
        await channel.send(embed=ch_embed, file=ch_file)

        if send_to_dm:
            try:
                dm_file = discord.File(io.BytesIO(image_bytes), filename=filename)
                dm_embed = discord.Embed(description=greeting_text, color=discord.Color.from_rgb(46, 204, 113))
                dm_embed.set_author(name=member.display_name, icon_url=getattr(member.display_avatar, "url", None))
                dm_embed.set_image(url=f"attachment://{filename}")
                dm_embed.set_footer(text="Your Personal waifu")
                await send_to_dm.send(embed=dm_embed, file=dm_file)
            except Exception:
                pass
    except Exception:
        try:
            await channel.send(greeting_text)
        except Exception:
            pass

def get_vcs_with_users(guild):
    out = []
    for vc_id in VC_IDS:
        vc = guild.get_channel(vc_id)
        if vc and isinstance(vc, discord.VoiceChannel):
            users = [m for m in vc.members if not m.bot]
            if users: out.append((vc, users))
    return out

async def update_vc_position(guild, target_channel=None):
    vc_client = guild.voice_client
    if target_channel and target_channel.id in VC_IDS:
        users = [m for m in target_channel.members if not m.bot]
        if users:
            try:
                if vc_client and vc_client.is_connected():
                    if vc_client.channel.id != target_channel.id:
                        await vc_client.move_to(target_channel)
                else:
                    await target_channel.connect()
                return target_channel
            except Exception:
                pass

    if vc_client and vc_client.is_connected():
        current = vc_client.channel
        if current and current.id in VC_IDS:
            users = [m for m in current.members if not m.bot]
            if users:
                return current

    vcs = get_vcs_with_users(guild)
    if vcs:
        order = {vid: i for i, vid in enumerate(VC_IDS)}
        vcs.sort(key=lambda x: order.get(x[0].id, 999))
        target_vc = vcs[0][0]
        try:
            if vc_client and vc_client.is_connected():
                if vc_client.channel.id != target_vc.id:
                    await vc_client.move_to(target_vc)
            else:
                await target_vc.connect()
            return target_vc
        except Exception:
            pass

    if vc_client and vc_client.is_connected():
        return vc_client.channel

    for vc_id in VC_IDS:
        vc = guild.get_channel(vc_id)
        if vc and isinstance(vc, discord.VoiceChannel):
            try:
                await vc.connect()
                return vc
            except Exception:
                continue
    return None

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(seconds=AUTOSAVE_INTERVAL)
async def autosave_task():
    try: save_data()
    except Exception: pass

@tasks.loop(seconds=45)
async def periodic_vc_drop():
    if not VC_CHANNEL_ID: return
    for vc_id in VC_IDS:
        vc = bot.get_channel(vc_id)
        if not vc or not isinstance(vc, discord.VoiceChannel): continue
        users = [m for m in vc.members if not m.bot]
        if len(users) >= 2:
            channel = bot.get_channel(VC_CHANNEL_ID)
            if not channel: continue
            try:
                async with aiohttp.ClientSession() as session:
                    url, _, _ = await fetch_gif(session)
                    if url:
                        image_bytes, content_type = await _download_bytes(session, url)
                        if image_bytes:
                            if len(image_bytes) > DISCORD_MAX_UPLOAD:
                                image_bytes = await compress_image(image_bytes)
                            if image_bytes and len(image_bytes) <= DISCORD_MAX_UPLOAD:
                                ext = ".gif" if "gif" in url.lower() or (content_type and "gif" in content_type) else ".jpg"
                                await channel.send(file=discord.File(io.BytesIO(image_bytes), filename=f"waifu{ext}"))
            except Exception:
                pass
            break

@tasks.loop(seconds=300)
async def vc_reconnect_heartbeat():
    for guild in bot.guilds:
        try:
            vc_client = guild.voice_client
            if vc_client and vc_client.is_connected(): continue
            connected = False
            for vc_id in VC_IDS:
                vc = guild.get_channel(vc_id)
                if vc and isinstance(vc, discord.VoiceChannel):
                    if [m for m in vc.members if not m.bot]:
                        try:
                            await vc.connect()
                            connected = True
                            break
                        except Exception:
                            pass
            if not connected:
                for vc_id in VC_IDS:
                    vc = guild.get_channel(vc_id)
                    if vc and isinstance(vc, discord.VoiceChannel):
                        try:
                            await vc.connect()
                            break
                        except Exception:
                            pass
        except Exception:
            pass

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    for task in (autosave_task, periodic_vc_drop, vc_reconnect_heartbeat):
        if not task.is_running(): task.start()
    for guild in bot.guilds:
        try:
            await update_vc_position(guild)
        except Exception:
            pass

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id: return
    guild = member.guild
    channel = bot.get_channel(VC_CHANNEL_ID) if VC_CHANNEL_ID else None

    was_monitored = before and before.channel and before.channel.id in VC_IDS
    now_monitored = after and after.channel and after.channel.id in VC_IDS

    if was_monitored or now_monitored:
        if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
            await update_vc_position(guild, target_channel=after.channel)
        else:
            await update_vc_position(guild)

    if not channel: return

    async with aiohttp.ClientSession() as session:
        if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
            greeting = random.choice(JOIN_GREETINGS).format(display_name=member.display_name)
            gif_url, _, _ = await fetch_gif(session, member.id)
            if gif_url:
                await send_greeting_embed(channel, session, greeting, gif_url, member, send_to_dm=member)
            else:
                await channel.send(greeting)

        elif was_monitored and not now_monitored:
            leave_msg = random.choice(LEAVE_GREETINGS).format(display_name=member.display_name)
            gif_url, _, _ = await fetch_gif(session, member.id)
            if gif_url:
                await send_greeting_embed(channel, session, leave_msg, gif_url, member, send_to_dm=member)
            else:
                await channel.send(leave_msg)

if not TOKEN:
    logger.error("TOKEN env var is not set.")
    sys.exit(1)

keep_alive()
bot.run(TOKEN)
