import os, sys, io, json, random, hashlib, logging, re, asyncio, base64, datetime
import xml.etree.ElementTree as ET
import difflib
from collections import deque
from urllib.parse import quote_plus

import aiohttp
import discord
from discord.ext import commands, tasks

try:
    from PIL import Image
except Exception:
    Image = None

# ── Keep-alive server (lightweight asyncio, no Flask) ─────────────────────────

async def _keep_alive_server():
    async def _handle(reader, writer):
        try:
            await reader.read(2048)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 13\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"Bot is alive!"
            )
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
    port = int(os.environ.get("PORT", 10000))
    server = await asyncio.start_server(_handle, "0.0.0.0", port)
    logger.info(f"Keep-alive server listening on port {port}")
    async with server:
        await server.serve_forever()

# ── Environment variables ──────────────────────────────────────────────────────

NSFW_MODE = True

TOKEN              = os.getenv("TOKEN", "")
WAIFUIM_API_KEY    = os.getenv("WAIFUIM_API_KEY", "")
DANBOORU_USER      = os.getenv("DANBOORU_USER", "")
DANBOORU_API_KEY   = os.getenv("DANBOORU_API_KEY", "")
GELBOORU_API_KEY   = os.getenv("GELBOORU_API_KEY", "")
GELBOORU_USER      = os.getenv("GELBOORU_USER", "")
E621_USER          = os.getenv("E621_USER", "")
E621_API_KEY       = os.getenv("E621_API_KEY", "")
WAIFU_IT_API_KEY   = os.getenv("WAIFU_IT_API_KEY", "")
BOT_PERSONA        = os.getenv("BOT_PERSONA_NAME", "Yuki")

DEBUG_FETCH            = str(os.getenv("DEBUG_FETCH", "")).strip().lower() in ("1","true","yes","on")
TRUE_RANDOM            = str(os.getenv("TRUE_RANDOM", "")).strip().lower() in ("1","true","yes")
REQUEST_TIMEOUT        = int(os.getenv("REQUEST_TIMEOUT", "14"))
DISCORD_MAX_UPLOAD     = int(os.getenv("DISCORD_MAX_UPLOAD", str(8 * 1024 * 1024)))
HEAD_SIZE_LIMIT        = DISCORD_MAX_UPLOAD
DATA_FILE              = os.getenv("DATA_FILE", "data_nsfw.json")
AUTOSAVE_INTERVAL      = int(os.getenv("AUTOSAVE_INTERVAL", "120"))
FETCH_ATTEMPTS         = int(os.getenv("FETCH_ATTEMPTS", "40"))
MAX_USED_GIFS_PER_USER = int(os.getenv("MAX_USED_GIFS_PER_USER", "1000"))

VC_CHANNEL_ID = int(os.getenv("VC_CHANNEL_ID", "0"))
_VC_IDS_RAW   = os.getenv("VC_IDS", "")
VC_IDS        = [int(x.strip()) for x in _VC_IDS_RAW.split(",") if x.strip().isdigit()] if _VC_IDS_RAW.strip() else []

COMMAND_CHANNEL_ID = int(os.getenv("COMMAND_CHANNEL_ID", "0"))

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if DEBUG_FETCH else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("waifu-bot")

if not VC_IDS:
    logger.warning("[VC] VC_IDS env var not set — voice channel features disabled.")
if not VC_CHANNEL_ID:
    logger.warning("[VC] VC_CHANNEL_ID env var not set — text channel messages disabled.")
if not COMMAND_CHANNEL_ID:
    logger.warning("[CMD] COMMAND_CHANNEL_ID env var not set — neko command filter disabled.")

# ── Data persistence ───────────────────────────────────────────────────────────

if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({
            "sent_history": {}, "vc_state": {},
            "greeted_users": [], "visit_count": {},
            "last_daily_open": "", "user_tiers": {},
        }, f, indent=2)

with open(DATA_FILE, "r") as f:
    _raw = json.load(f)

data = {
    "sent_history": {
        uid: deque(h, maxlen=MAX_USED_GIFS_PER_USER)
        for uid, h in _raw.get("sent_history", {}).items()
    },
    "vc_state":        _raw.get("vc_state", {}),
    "greeted_users":   _raw.get("greeted_users", []),
    "visit_count":     _raw.get("visit_count", {}),
    "last_daily_open": _raw.get("last_daily_open", ""),
    "user_tiers":      _raw.get("user_tiers", {}),
}

def _write_json(payload):
    with open(DATA_FILE, "w") as f:
        json.dump(payload, f, indent=2)

async def save_data():
    try:
        serializable = {
            "sent_history":    {uid: list(h) for uid, h in data["sent_history"].items()},
            "vc_state":        data["vc_state"],
            "greeted_users":   data["greeted_users"],
            "visit_count":     data["visit_count"],
            "last_daily_open": data["last_daily_open"],
            "user_tiers":      data["user_tiers"],
        }
        await asyncio.to_thread(_write_json, serializable)
    except Exception as e:
        logger.warning(f"Save failed: {e}")

# ── Image utilities ────────────────────────────────────────────────────────────

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

def _compress_image_sync(image_bytes, target_size):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.format == "GIF":
            return image_bytes
        output = io.BytesIO()
        quality = 95
        while quality > 10:
            output.seek(0)
            output.truncate()
            img.save(output, format=img.format or "JPEG", quality=quality, optimize=True)
            if output.tell() <= target_size:
                return output.getvalue()
            quality -= 10
        return output.getvalue()
    except Exception:
        return image_bytes

async def compress_image(image_bytes, target_size=DISCORD_MAX_UPLOAD):
    if not Image:
        return image_bytes
    return await asyncio.to_thread(_compress_image_sync, image_bytes, target_size)

# ── API provider tag lists ─────────────────────────────────────────────────────

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

# ── API provider functions ─────────────────────────────────────────────────────
# UNRESTRICTED: all random tag filters, animated filters, and category
# limitations have been removed. Each provider now requests the full explicit
# pool rather than a pre-filtered subset.

async def _gelbooru_compat(session, base_url, api_key=None, user_id=None, extra_tags=None):
    """Gelbooru-compatible endpoint — unrestricted explicit fetch."""
    try:
        # Only rating:explicit — no additional tag narrowing
        tags = ["rating:explicit"]
        if extra_tags:
            tags.extend(extra_tags)
        params = {
            "page": "dapi", "s": "post", "q": "index",
            "json": "1", "tags": " ".join(tags), "limit": 100,
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
    """Rule34 — unrestricted explicit fetch, no tag narrowing."""
    try:
        # rating:explicit only — removed SPICY_TAG and animated filters
        tags = ["rating:explicit"]
        params = {
            "page": "dapi", "s": "post", "q": "index",
            "json": "1", "tags": " ".join(tags), "limit": 200,
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
    """Gelbooru — unrestricted explicit fetch."""
    url, _, post = await _gelbooru_compat(
        session, "https://gelbooru.com/index.php",
        GELBOORU_API_KEY or None, GELBOORU_USER or None
    )
    return url, "gelbooru", post

async def fetch_nekosapi(session, positive=None):
    """NekosAPI v4 — unrestricted explicit fetch, no tag filter."""
    try:
        # Removed the 80% random tag restriction — fetch any explicit image
        params = {"rating": "explicit", "limit": 20}
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
    """Konachan — unrestricted explicit fetch, no tag/animated filter."""
    try:
        # Only rating:explicit — removed SPICY_TAG and animated filters
        params = {"tags": "rating:explicit", "limit": 100}
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://konachan.com/post.json", params=params, headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            posts = await resp.json(content_type=None)
            if not posts: return None, None, None
            image_posts = [
                p for p in posts
                if p.get("file_url") and not p.get("file_url", "").lower().endswith((".webm", ".mp4"))
            ]
            if not image_posts: return None, None, None
            post = random.choice(image_posts)
            return post.get("file_url"), "konachan", post
    except Exception:
        return None, None, None

async def fetch_nekobot(session, positive=None):
    """Nekobot — all available NSFW categories (expanded from 8 to full list)."""
    try:
        # EXPANDED: was 8 categories, now includes all known NSFW nekobot types
        category = random.choice([
            "hentai", "hentai_anal", "hass", "hboobs", "hthigh",
            "paizuri", "tentacle", "pgif", "pussy", "hkuni",
            "hanal", "hfeet", "hbdsm", "hfutanari", "hmilf",
            "hass", "hnude", "hpov",
        ])
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(f"https://nekobot.xyz/api/image?type={category}", timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            if not payload.get("success"): return None, None, None
            return payload.get("message"), f"nekobot_{category}", payload
    except Exception:
        return None, None, None

async def fetch_danbooru(session, positive=None):
    """Danbooru — unrestricted explicit fetch, no tag/animated filter."""
    try:
        # Only rating:explicit — removed SPICY_TAG and animated filters
        params = {"tags": "rating:explicit", "limit": 100, "random": "true"}
        auth = aiohttp.BasicAuth(DANBOORU_USER, DANBOORU_API_KEY) if (DANBOORU_USER and DANBOORU_API_KEY) else None
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://danbooru.donmai.us/posts.json", params=params, auth=auth, timeout=to) as resp:
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
    """Nekos.life — all available NSFW endpoints (expanded full list)."""
    try:
        # EXPANDED: was 8 categories, now all known NSFW nekos.life endpoints
        category = random.choice([
            "blowjob", "cum", "hentai", "classical", "ero", "spank",
            "lewd", "feet", "solo", "yuri", "trap", "futanari",
            "hololewd", "lewdk", "nekolewd", "pwankg", "feetg",
            "bj", "holoero", "pussy", "tits", "anal", "bdsm",
            "creampie", "gangbang",
        ])
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(f"https://nekos.life/api/v2/img/{category}", timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            return payload.get("url"), f"nekos_life_{category}", payload
    except Exception:
        return None, None, None

async def fetch_tbib(session, positive=None):
    """TBIB — unrestricted explicit fetch."""
    url, _, post = await _gelbooru_compat(session, "https://tbib.org/index.php")
    return url, "tbib", post

async def fetch_xbooru(session, positive=None):
    """Xbooru — unrestricted explicit fetch."""
    url, _, post = await _gelbooru_compat(session, "https://xbooru.com/index.php")
    return url, "xbooru", post

async def fetch_realbooru(session, positive=None):
    """Realbooru — unrestricted explicit fetch."""
    url, _, post = await _gelbooru_compat(session, "https://realbooru.com/index.php")
    return url, "realbooru", post

async def fetch_waifu_im(session, positive=None):
    """Waifu.im — unrestricted NSFW fetch, no tag filter, increased limit."""
    try:
        # Removed specific tag restriction — fetch any NSFW image
        params = {"is_nsfw": "true", "limit": 30}
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
    """Paheal — unrestricted explicit fetch, no tag filter."""
    try:
        # Removed SPICY_TAG restriction — fetch with only rating:explicit
        params = {"tags": "rating:explicit", "limit": 100}
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(
            "https://rule34.paheal.net/api/danbooru/find_posts/index.xml",
            params=params, headers=hdrs, timeout=to
        ) as resp:
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
    """Waifu.it — all available NSFW categories (expanded full list)."""
    if not WAIFU_IT_API_KEY: return None, None, None
    try:
        # EXPANDED: was 8 categories, now all known NSFW waifu.it endpoints
        category = random.choice([
            "creampie", "thighjob", "ero", "paizuri", "oppai", "anal",
            "blowjob", "hentai", "ass", "bdsm", "cum", "feet",
            "femdom", "futanari", "gangbang", "group", "handjob",
            "hardcore", "lewd", "milf", "naked", "nude", "pussy",
            "rape", "riding", "sex", "solo", "tentacle", "uniform",
            "yaoi", "yuri",
        ])
        hdrs = {"Authorization": WAIFU_IT_API_KEY}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(f"https://waifu.it/api/v4/{category}", headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            return payload.get("url"), f"waifu_it_{category}", payload
    except Exception:
        return None, None, None

async def fetch_nekos_moe(session, positive=None):
    """Nekos.moe — unrestricted NSFW fetch."""
    try:
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(
            "https://nekos.moe/api/v1/random/image?nsfw=true&count=1",
            headers=hdrs, timeout=to
        ) as resp:
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
    """Waifu.pics — all available NSFW categories."""
    try:
        # All available NSFW categories from waifu.pics API
        category = random.choice(["waifu", "neko", "trap", "blowjob"])
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(f"https://api.waifu.pics/nsfw/{category}", timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            return payload.get("url") or payload.get("image"), f"waifu_pics_{category}", payload
    except Exception:
        return None, None, None

async def fetch_e621(session, positive=None):
    """e621 — unrestricted explicit fetch, no tag filter, increased limit."""
    try:
        # Only rating:explicit + order:random — removed SPICY_TAG filter
        params = {"tags": "rating:explicit order:random", "limit": 100}
        hdrs = {"User-Agent": "WaifuBot/1.0 (by discord_bot_operator on e621)"}
        auth = aiohttp.BasicAuth(E621_USER, E621_API_KEY) if E621_USER and E621_API_KEY else None
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://e621.net/posts.json", params=params, headers=hdrs, auth=auth, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            payload = await resp.json(content_type=None)
            posts = payload.get("posts", [])
            if not posts: return None, None, None
            image_posts = [
                p for p in posts
                if p.get("file", {}).get("ext") in ("jpg", "jpeg", "png", "gif", "webp")
            ]
            if not image_posts: return None, None, None
            post = random.choice(image_posts)
            gif_url = post.get("file", {}).get("url")
            if not gif_url: return None, None, None
            return gif_url, "e621", post
    except Exception:
        return None, None, None

async def fetch_yandere(session, positive=None):
    """Yande.re — unrestricted explicit fetch, no tag filter, increased limit."""
    try:
        # Only rating:explicit + order:random — removed SPICY_TAG filter
        params = {"tags": "rating:explicit order:random", "limit": 100}
        hdrs = {"User-Agent": "WaifuBot/1.0"}
        to = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get("https://yande.re/post.json", params=params, headers=hdrs, timeout=to) as resp:
            if resp.status != 200: return None, None, None
            posts = await resp.json(content_type=None)
            if not posts: return None, None, None
            image_posts = [
                p for p in posts
                if p.get("file_url") and not p.get("file_url", "").lower().endswith((".webm", ".mp4"))
            ]
            if not image_posts: return None, None, None
            post = random.choice(image_posts)
            return post.get("file_url"), "yandere", post
    except Exception:
        return None, None, None

async def fetch_hypnohub(session, positive=None):
    """Hypnohub — unrestricted explicit fetch."""
    url, _, post = await _gelbooru_compat(session, "https://hypnohub.net/index.php")
    return url, "hypnohub", post

# ── Provider list & health tracking ───────────────────────────────────────────

_BASE_PROVIDERS = [
    ("rule34",     fetch_rule34,     45),
    ("gelbooru",   fetch_gelbooru,   18),
    ("nekosapi",   fetch_nekosapi,   15),
    ("konachan",   fetch_konachan,   12),
    ("nekobot",    fetch_nekobot,    10),
    ("danbooru",   fetch_danbooru,    8),
    ("nekos_life", fetch_nekos_life,  8),
    ("tbib",       fetch_tbib,        7),
    ("xbooru",     fetch_xbooru,      7),
    ("realbooru",  fetch_realbooru,   6),
    ("waifu_im",   fetch_waifu_im,    5),
    ("paheal",     fetch_paheal,      5),
    ("waifu_it",   fetch_waifu_it,    4),
    ("nekos_moe",  fetch_nekos_moe,   3),
    ("waifu_pics", fetch_waifu_pics,  2),
]

_NSFW_EXTRA_PROVIDERS = [
    ("e621",     fetch_e621,     18),
    ("yandere",  fetch_yandere,  14),
    ("hypnohub", fetch_hypnohub,  6),
]

PROVIDERS = _BASE_PROVIDERS + _NSFW_EXTRA_PROVIDERS

_provider_failures  = {}
_provider_last_used = {}
_PROVIDER_RATE_GAPS  = {"danbooru": 1.0, "e621": 1.0, "gelbooru": 0.5}
# RAISED from 5 → 10 so providers are not silenced too aggressively
_PROVIDER_FAIL_LIMIT = 10

def _hash_url(url):
    return hashlib.md5(url.encode()).hexdigest()

def _choose_provider():
    if TRUE_RANDOM:
        return random.choice(PROVIDERS)
    eligible = [
        (n, f, w) for n, f, w in PROVIDERS
        if _provider_failures.get(n, 0) < _PROVIDER_FAIL_LIMIT
    ]
    if not eligible:
        _provider_failures.clear()
        eligible = PROVIDERS
    weights = [w for _, _, w in eligible]
    return random.choices(eligible, weights=weights, k=1)[0]

async def _fetch_one(session, used_hashes=None):
    if used_hashes is None:
        used_hashes = set()
    name, fetch_func, _ = _choose_provider()
    gap = _PROVIDER_RATE_GAPS.get(name, 0)
    if gap:
        last = _provider_last_used.get(name, 0)
        now  = asyncio.get_event_loop().time()
        wait = gap - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
    _provider_last_used[name] = asyncio.get_event_loop().time()
    try:
        url, source, meta = await fetch_func(session)
        if url:
            h = _hash_url(url)
            if h not in used_hashes:
                _provider_failures[name] = 0
                return url, source, meta, h
        _provider_failures[name] = _provider_failures.get(name, 0) + 1
    except Exception:
        _provider_failures[name] = _provider_failures.get(name, 0) + 1
    return None, None, None, None

async def fetch_gif(session, user_id=None):
    uid     = str(user_id) if user_id else "global"
    history = data["sent_history"].setdefault(uid, deque(maxlen=MAX_USED_GIFS_PER_USER))
    used    = set(history)
    rounds  = max(1, FETCH_ATTEMPTS // 3)
    for _ in range(rounds):
        tasks_list = [_fetch_one(session, used) for _ in range(3)]
        results    = await asyncio.gather(*tasks_list, return_exceptions=True)
        for res in results:
            if isinstance(res, tuple) and res[0]:
                url, source, meta, url_hash = res
                history.append(url_hash)
                data["sent_history"][uid] = history
                return url, source, meta
    return None, None, None

# ── Greeting text pools ────────────────────────────────────────────────────────

FIRST_TIME_GREETINGS = [
    "🎌 **{display_name}** steps into the spotlight for the very first time. We've been waiting.",
    "✨ A new soul enters — **{display_name}**, the prologue just became a story.",
    "👁️ **{display_name}** arrives for the first time. The room won't forget this.",
    "🌸 **{display_name}** joins us for the first time — welcome to your new obsession.",
    "🔮 **{display_name}** appears. First entries are always the ones that matter most.",
    "🎴 **{display_name}** — a new card dealt. The game just changed.",
    "🌺 A new presence blooms: **{display_name}**. First impressions are the deepest.",
    "⛩️ The gates open for **{display_name}** for the very first time. Step through.",
]

TIERS = ["shadow", "flame", "frost", "bloom", "storm"]

TIER_LABELS = {
    "shadow": "🌑 Shadow",
    "flame":  "🔥 Flame",
    "frost":  "🧊 Frost",
    "bloom":  "🌸 Bloom",
    "storm":  "⚡ Storm",
}

TIER_FIRST_GREETINGS = {
    "shadow": "🌑 **{display_name}** — the shadow faction claims its own. Welcome to the dark.",
    "flame":  "🔥 **{display_name}** — the flame court rises. Burn bright, burn long.",
    "frost":  "🧊 **{display_name}** — cold, precise, and inevitable. The frost welcomes you.",
    "bloom":  "🌸 **{display_name}** — rare beauty, quiet danger. The bloom recognizes you.",
    "storm":  "⚡ **{display_name}** — the weather just became a warning. Storm faction, rise.",
}

MILESTONE_GREETINGS = [
    "🔥 **{display_name}** is back for visit #{count}. A true regular. The room noticed.",
    "👑 **{display_name}** returns again — #{count}. The throne is yours. Sit.",
    "🖤 **{display_name}** visit #{count}. Some presences become permanent. You're one of them.",
    "⚡ **{display_name}** — #{count} times through these doors. Electric as always.",
    "🌙 **{display_name}** #{count}. The night keeps track even when you don't.",
]

THEMED_JOINS = {
    "midnight": [
        "🌑 {display_name} crept in past midnight — the darkest hours are the most honest.",
        "🕯️ {display_name} arrived while everyone sleeps. The night belongs to you both.",
        "🌙 {display_name} showed up at midnight. Some invitations aren't spoken aloud.",
        "🖤 {display_name} joins the midnight crowd — awake when the world forgets to watch.",
        "🌌 {display_name} drifted in under starless dark. Something about them fits.",
    ],
    "morning": [
        "☀️ {display_name} is here early — the ambitious ones always are.",
        "🍵 {display_name} arrived with the morning light. First one in, boldest one here.",
        "🌅 {display_name} stepped in at dawn. The day just got more interesting.",
        "☕ {display_name} arrived before the world woke up. Respect.",
        "🌤️ {display_name} joins with the morning — fresh and already dangerous.",
    ],
    "afternoon": [
        "🌤️ {display_name} arrived — the afternoon shift just got dangerous.",
        "☕ {display_name} showed up midday. Energy high, patience short.",
        "🌞 {display_name} joins at peak hours — the sharpest one in the room.",
        "🍃 {display_name} steps in with the afternoon breeze. Casual but noticed.",
    ],
    "evening": [
        "🌆 {display_name} arrived as the sun drops. Evening energy hits different.",
        "🍷 {display_name} joined at dusk — every good story starts now.",
        "🌇 {display_name} stepped in with golden hour. The best part of the day just started.",
        "🕯️ {display_name} arrives as the lights go warm. Perfect timing.",
        "🌃 {display_name} joined with the evening crowd. The room shifts gear.",
    ],
}

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

MOOD_MESSAGES = [
    "🌙 *The room hums with something unspoken tonight.*",
    "👁️ *Someone is definitely watching. That's not necessarily bad.*",
    "🕯️ *The candles burn a little lower when everyone's here.*",
    "🌑 *Something shifted. The air knows.*",
    "🎶 *A song no one remembers the name of starts playing.*",
    "🖤 *The dark is comfortable here. Stay a while.*",
    "🌌 *The void acknowledged you. You may not have noticed.*",
    "🐍 *Patience is a form of power. Just saying.*",
    "⚡ *Static in the air. Nobody made it — it just formed.*",
    "🌺 *Something beautiful and slightly dangerous is happening right now.*",
    "🔮 *The future is reading the room. What it sees is interesting.*",
    "🥀 *Even wilted things carry a scent. Remember that.*",
]

DAILY_OPENERS = [
    "🌅 *A new day begins. The room opens its eyes.*",
    "🎌 *The gates are open. Who arrives first sets the tone.*",
    "🌒 *Another cycle, another cast of characters. Welcome back.*",
    "⛩️ *The channel wakes. Something is already stirring.*",
    "🌸 *Day begins. The air is still. Not for long.*",
]

# ── Embed configuration ────────────────────────────────────────────────────────

JOIN_EMBED_COLORS = [
    discord.Color.from_rgb(220, 53, 69),
    discord.Color.from_rgb(123, 44, 191),
    discord.Color.from_rgb(255, 159, 28),
    discord.Color.from_rgb(220, 53, 128),
    discord.Color.from_rgb(180, 30, 60),
]

LEAVE_EMBED_COLORS = [
    discord.Color.from_rgb(60, 60, 90),
    discord.Color.from_rgb(40, 40, 70),
    discord.Color.from_rgb(80, 50, 100),
    discord.Color.from_rgb(30, 50, 80),
]

JOIN_TITLES = [
    "⛩️ Voice Channel Arrival",
    "🎌 New Presence Detected",
    "🌙 The Room Shifts",
    "👁️ Arrival Noted",
    "🔮 A Soul Enters",
    "🌑 The Dark Acknowledges",
    "🕯️ The Flame Rises",
    "✨ Presence: Confirmed",
]

LEAVE_TITLES = [
    "🌙 They Slipped Away",
    "🖤 Departure Noted",
    "🌑 The Room Exhales",
    "🕯️ The Flame Lowers",
    "🌫️ Gone Like Smoke",
    "👁️ One Less Watcher",
]

FOOTER_LINES = [
    f"{BOT_PERSONA} noticed you  🖤",
    "presence detected — system unstable",
    "another soul joins the chaos",
    "logged, archived, adored  ❤️",
    "the void welcomed you",
    f"𝑤𝑎𝑡𝑐ℎ𝑒𝑑 𝑏𝑦 {BOT_PERSONA}",
    "𝑝𝑟𝑒𝑠𝑒𝑛𝑐𝑒 𝑖𝑠 𝑒𝑣𝑒𝑟𝑦𝑡ℎ𝑖𝑛𝑔",
    "the room remembers  🌙",
]

LEAVE_FOOTER_LINES = [
    f"{BOT_PERSONA} watched them leave  🖤",
    "𝑡ℎ𝑒𝑦'𝑙𝑙 𝑏𝑒 𝑏𝑎𝑐𝑘",
    "departure archived  🌙",
    "absence noted — warmth remains",
    "𝑡ℎ𝑒 𝑟𝑜𝑜𝑚 𝑟𝑒𝑚𝑒𝑚𝑏𝑒𝑟𝑠",
]

JOIN_REACTIONS  = ["🖤", "👁️", "🔥", "✨", "💫", "🌙", "😈", "👑", "🥀", "⚡", "🎌", "🌸"]
LEAVE_REACTIONS = ["🌙", "🖤", "💫", "🌑", "🕯️", "🌺", "🎶", "🌌"]

# ── Greeting helpers ───────────────────────────────────────────────────────────

def _get_time_theme():
    hour = datetime.datetime.utcnow().hour
    if   0 <= hour < 6:   return "midnight"
    elif 6 <= hour < 12:  return "morning"
    elif 12 <= hour < 18: return "afternoon"
    else:                  return "evening"

def get_join_greeting(member):
    uid   = str(member.id)
    name  = member.display_name
    count = data["visit_count"].get(uid, 0) + 1
    data["visit_count"][uid] = count

    is_first = uid not in data["greeted_users"]
    if is_first:
        data["greeted_users"].append(uid)
        tier = random.choice(TIERS)
        data["user_tiers"][uid] = tier
        return TIER_FIRST_GREETINGS[tier].format(display_name=name)

    if count % 10 == 0 or count % 5 == 0:
        return random.choice(MILESTONE_GREETINGS).format(display_name=name, count=count)

    theme = _get_time_theme()
    pool  = THEMED_JOINS.get(theme, []) + JOIN_GREETINGS
    return random.choice(pool).format(display_name=name)

def get_leave_greeting(display_name):
    return random.choice(LEAVE_GREETINGS).format(display_name=display_name)

async def maybe_send_daily_open(channel):
    today = datetime.date.today().isoformat()
    if data["last_daily_open"] == today:
        return
    data["last_daily_open"] = today
    try:
        await channel.send(random.choice(DAILY_OPENERS))
    except Exception:
        pass

def _is_late_night():
    return 0 <= datetime.datetime.utcnow().hour < 5

# ── Safe send with retry ───────────────────────────────────────────────────────

async def _safe_send(channel, **kwargs):
    for attempt in range(2):
        try:
            return await channel.send(**kwargs)
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, "retry_after", 2)
                await asyncio.sleep(retry_after + 0.5)
            elif attempt == 0:
                await asyncio.sleep(1)
            else:
                raise
    return None

# ── Embed sender ───────────────────────────────────────────────────────────────

async def send_greeting_embed(channel, session, greeting_text, image_url, member,
                               send_to_dm=None, event_type="join"):
    try:
        image_bytes, content_type = None, None
        if _image_pool is not None and not _image_pool.empty():
            try:
                pool_url, image_bytes, content_type = _image_pool.get_nowait()
                image_url = pool_url
            except asyncio.QueueEmpty:
                pass

        if image_bytes is None:
            image_bytes, content_type = await _download_bytes(session, image_url)

        if image_bytes and len(image_bytes) > DISCORD_MAX_UPLOAD:
            image_bytes = await compress_image(image_bytes)
        if not image_bytes or len(image_bytes) > DISCORD_MAX_UPLOAD:
            await _safe_send(channel, content=greeting_text)
            return

        lurl  = image_url.lower()
        ctype = content_type or ""
        ext   = ".gif" if ("gif" in lurl or "gif" in ctype) else ".jpg"
        if "png"  in lurl or "png"  in ctype: ext = ".png"
        elif "webp" in lurl or "webp" in ctype: ext = ".webp"
        filename = f"waifu{ext}"

        if event_type == "join":
            color    = random.choice(JOIN_EMBED_COLORS)
            title    = random.choice(JOIN_TITLES)
            footer   = random.choice(FOOTER_LINES)
            reaction = random.choice(JOIN_REACTIONS)
        else:
            color    = random.choice(LEAVE_EMBED_COLORS)
            title    = random.choice(LEAVE_TITLES)
            footer   = random.choice(LEAVE_FOOTER_LINES)
            reaction = random.choice(LEAVE_REACTIONS)

        if _is_late_night():
            color  = discord.Color.from_rgb(10, 10, 20)
            footer = f"𝑙𝑎𝑡𝑒 𝑛𝑖𝑔ℎ𝑡. {BOT_PERSONA} 𝑖𝑠 𝑠𝑡𝑖𝑙𝑙 𝑤𝑎𝑡𝑐ℎ𝑖𝑛𝑔  🌑"

        uid  = str(member.id)
        tier = data["user_tiers"].get(uid)
        if tier:
            footer = f"{footer}  ·  {TIER_LABELS[tier]}"

        embed = discord.Embed(
            title=title,
            description=greeting_text,
            color=color,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.set_author(
            name=member.display_name,
            icon_url=getattr(member.display_avatar, "url", None),
        )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(
            text=footer,
            icon_url=getattr(member.display_avatar, "url", None),
        )

        ch_file = discord.File(io.BytesIO(image_bytes), filename=filename)
        msg = await _safe_send(channel, embed=embed, file=ch_file)
        if msg:
            try:
                await msg.add_reaction(reaction)
            except Exception:
                pass

        if send_to_dm:
            try:
                dm_file  = discord.File(io.BytesIO(image_bytes), filename=filename)
                dm_embed = discord.Embed(
                    title=title,
                    description=greeting_text,
                    color=color,
                    timestamp=datetime.datetime.utcnow(),
                )
                dm_embed.set_author(
                    name=member.display_name,
                    icon_url=getattr(member.display_avatar, "url", None),
                )
                dm_embed.set_image(url=f"attachment://{filename}")
                dm_embed.set_footer(
                    text=footer,
                    icon_url=getattr(member.display_avatar, "url", None),
                )
                await send_to_dm.send(embed=dm_embed, file=dm_file)
            except Exception:
                pass

    except Exception:
        try:
            await _safe_send(channel, content=greeting_text)
        except Exception:
            pass

# ── Voice channel utilities ────────────────────────────────────────────────────

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

# ── Bot setup ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.voice_states    = True
intents.message_content = True
intents.members         = True
bot = commands.Bot(command_prefix="!", intents=intents)

_image_pool = None

# ── Command list & suggestion system ──────────────────────────────────────────

COMMANDS = [
    "69", "aibooru", "aihentai", "anal", "bfuck", "boobjob", "boobs",
    "butt", "cum", "danbooru", "dickride", "doujin", "e621", "fap",
    "footjob", "fuck", "futafuck", "gelbooru", "grabboobs", "grabbutts",
    "handjob", "happyend", "hentai", "hentaigif", "hentaijk", "hvideo",
    "irl", "konachan", "kuni", "lewdere", "lewdkitsune", "lewdneko",
    "paizuri", "pussy", "realbooru", "rule34", "safebooru", "suck",
    "suckboobs", "threesome", "trap", "vtuber", "yaoifuck", "yurifuck",
]

def suggest_commands(text):
    """Return up to 5 close matches from COMMANDS using fuzzy matching."""
    return difflib.get_close_matches(text, COMMANDS, n=5, cutoff=0.3)

# ── Background tasks ───────────────────────────────────────────────────────────

@tasks.loop(seconds=AUTOSAVE_INTERVAL)
async def autosave_task():
    try:
        await save_data()
    except Exception:
        pass


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

@tasks.loop(seconds=20)
async def prefetch_pool_filler():
    if _image_pool is None: return
    if _image_pool.qsize() >= 4: return
    try:
        url, _, _ = await fetch_gif(bot.http_session)
        if url:
            image_bytes, ctype = await _download_bytes(bot.http_session, url)
            if image_bytes and len(image_bytes) <= DISCORD_MAX_UPLOAD:
                try:
                    _image_pool.put_nowait((url, image_bytes, ctype))
                except asyncio.QueueFull:
                    pass
    except Exception:
        pass

@tasks.loop(minutes=35)
async def random_mood_drop():
    if not VC_CHANNEL_ID: return
    channel = bot.get_channel(VC_CHANNEL_ID)
    if not channel: return
    for vc_id in VC_IDS:
        vc = bot.get_channel(vc_id)
        if vc and isinstance(vc, discord.VoiceChannel):
            if [m for m in vc.members if not m.bot]:
                try:
                    await channel.send(random.choice(MOOD_MESSAGES))
                except Exception:
                    pass
                break

# ── Bot events ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    global _image_pool
    logger.info(f"Logged in as {bot.user}")

    connector = aiohttp.TCPConnector(
        limit=15,
        limit_per_host=3,
        ttl_dns_cache=600,
        enable_cleanup_closed=True,
    )
    bot.http_session = aiohttp.ClientSession(connector=connector)

    _image_pool = asyncio.Queue(maxsize=6)

    asyncio.create_task(_keep_alive_server())

    for task in (autosave_task, vc_reconnect_heartbeat,
                 prefetch_pool_filler, random_mood_drop):
        if not task.is_running():
            task.start()

    for guild in bot.guilds:
        try:
            await update_vc_position(guild)
        except Exception:
            pass

@bot.event
async def on_close():
    try:
        if hasattr(bot, "http_session") and not bot.http_session.closed:
            await bot.http_session.close()
    except Exception:
        pass
    await save_data()

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id: return
    guild   = member.guild
    channel = bot.get_channel(VC_CHANNEL_ID) if VC_CHANNEL_ID else None

    was_monitored = before and before.channel and before.channel.id in VC_IDS
    now_monitored = after  and after.channel  and after.channel.id  in VC_IDS

    if was_monitored or now_monitored:
        if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
            await update_vc_position(guild, target_channel=after.channel)
        else:
            await update_vc_position(guild)

    if not channel: return

    if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
        await maybe_send_daily_open(channel)

        greeting = get_join_greeting(member)

        others = [m for m in after.channel.members if not m.bot and m.id != member.id]
        if not others:
            greeting += "\n*— alone in the dark. interesting choice.*"
        elif len(others) >= 4:
            greeting += f"\n*— {len(others)} others already here. the audience is ready.*"

        gif_url, _, _ = await fetch_gif(bot.http_session, member.id)
        if gif_url:
            await send_greeting_embed(channel, bot.http_session, greeting, gif_url, member,
                                      send_to_dm=member, event_type="join")
        else:
            await _safe_send(channel, content=greeting)

    elif was_monitored and not now_monitored:
        leave_msg = get_leave_greeting(member.display_name)
        gif_url, _, _ = await fetch_gif(bot.http_session, member.id)
        if gif_url:
            await send_greeting_embed(channel, bot.http_session, leave_msg, gif_url, member,
                                      send_to_dm=member, event_type="leave")
        else:
            await _safe_send(channel, content=leave_msg)

# ── on_message — command channel filter + neko prefix + suggestions ────────────

_COMMANDS_LIST_MSG = (
    "📋 **Available Commands:**\n"
    "```\n"
    "69          aibooru     aihentai    anal        bfuck\n"
    "boobjob     boobs       butt        cum         danbooru\n"
    "dickride    doujin      e621        fap         footjob\n"
    "fuck        futafuck    gelbooru    grabboobs   grabbutts\n"
    "handjob     happyend    hentai      hentaigif   hentaijk\n"
    "hvideo      irl         konachan    kuni        lewdere\n"
    "lewdkitsune lewdneko    paizuri     pussy       realbooru\n"
    "rule34      safebooru   suck        suckboobs   threesome\n"
    "trap        vtuber      yaoifuck    yurifuck\n"
    "```"
)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if COMMAND_CHANNEL_ID and message.channel.id == COMMAND_CHANNEL_ID:

        if not message.content.lower().startswith("neko"):
            try:
                await message.delete()
            except Exception:
                pass
            await message.channel.send(_COMMANDS_LIST_MSG)
            await message.channel.send(
                f"✅ **Example:** `neko hentai` {message.author.mention}"
            )
            return

        parts = message.content.split()
        if len(parts) < 2:
            await message.channel.send(_COMMANDS_LIST_MSG)
            await message.channel.send(
                f"✅ **Example:** `neko hentai` {message.author.mention}"
            )
            return

        cmd = parts[1].lower()

        if cmd not in COMMANDS:
            suggestions = suggest_commands(cmd)
            if suggestions:
                suggestion_text = "\n".join([f"`neko {s}`" for s in suggestions])
                await message.channel.send(
                    f"❓ Unknown command **`{cmd}`**\n\nDid you mean:\n{suggestion_text}\n\n"
                    f"✅ **Example:** `neko hentai` {message.author.mention}"
                )
            else:
                await message.channel.send(_COMMANDS_LIST_MSG)
                await message.channel.send(
                    f"❌ Unknown command **`{cmd}`** {message.author.mention}\n"
                    f"✅ **Example:** `neko hentai`"
                )
            return

    await bot.process_commands(message)

# ── Run ────────────────────────────────────────────────────────────────────────

if not TOKEN:
    logger.error("TOKEN env var is not set.")
    sys.exit(1)

bot.run(TOKEN)
