# NSFW Discord Bot — Full Setup Guide
# =========================================
# For personal use on a private Discord server.
# You are fully responsible for legal compliance in your jurisdiction.

═══════════════════════════════════════════
  FILES YOU NEED
═══════════════════════════════════════════
  bot.py             ← the bot (run this)
  requirements.txt   ← Python dependencies

═══════════════════════════════════════════
  STEP 1 — CREATE A DISCORD BOT
═══════════════════════════════════════════
1. Go to: https://discord.com/developers/applications
2. Click "New Application" → name it anything
3. Go to "Bot" tab → click "Add Bot"
4. Under "Privileged Gateway Intents" enable ALL THREE:
     ✅ Presence Intent
     ✅ Server Members Intent
     ✅ Message Content Intent
5. Click "Reset Token" and COPY your bot token — this is your TOKEN env var
6. Go to "OAuth2" → "URL Generator"
   - Scopes: ✅ bot
   - Bot Permissions: ✅ Send Messages, ✅ Embed Links, ✅ Attach Files,
                      ✅ Read Message History, ✅ Connect, ✅ Move Members
7. Copy the generated URL and open it in your browser to invite the bot to your server

═══════════════════════════════════════════
  STEP 2 — GET YOUR CHANNEL IDs
═══════════════════════════════════════════
In Discord: Settings → Advanced → Enable "Developer Mode"

Right-click a VOICE CHANNEL → "Copy Channel ID"
  → Add it to VC_IDS (can be multiple, comma-separated)

Right-click a TEXT CHANNEL → "Copy Channel ID"
  → This is your VC_CHANNEL_ID (where join/leave messages and image drops go)
  → Mark this text channel as NSFW in Discord: Edit Channel → Enable "Age-Restricted Channel"

═══════════════════════════════════════════
  STEP 3 — DEPLOY ON RENDER
═══════════════════════════════════════════
1. Go to https://render.com and sign up (free)
2. Click "New +" → "Web Service"
3. Connect your GitHub account OR upload files directly
4. If using GitHub:
   - Create a repo with bot.py and requirements.txt
   - Connect that repo on Render
5. Configure the service:
   - Environment: Python 3
   - Build Command: pip install -r requirements.txt
   - Start Command: python bot.py
   - Instance Type: Free
6. Add all the environment variables below (Render → Environment tab)
7. Click "Create Web Service" — it will deploy and stay online

═══════════════════════════════════════════
  ENVIRONMENT VARIABLES (set these on Render)
═══════════════════════════════════════════

━━━ REQUIRED ━━━

TOKEN
  Your Discord bot token (from discord.com/developers)
  Example: TOKEN=MTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

VC_CHANNEL_ID
  ID of the text channel where the bot sends:
    - Join/leave greeting messages with NSFW image
    - Periodic NSFW image drops (when 2+ users in VC)
    - DMs are ALSO sent to the member who joined/left
  Example: VC_CHANNEL_ID=1371916812903780573

VC_IDS
  Comma-separated list of voice channel IDs the bot monitors and follows users in.
  Example: VC_IDS=1353875050809524267,1379350260010455051,1353882705246556220,1409170559337762980,1353875404217253909

NSFW_MODE
  Set to true to unlock e621, yande.re, and hypnohub providers (more explicit content)
  Example: NSFW_MODE=true

AGE_VERIFICATION
  Set to true to confirm you accept legal responsibility for adult content moderation.
  Example: AGE_VERIFICATION=true

━━━ OPTIONAL (free accounts — more providers & higher rate limits) ━━━

GELBOORU_API_KEY + GELBOORU_USER
  Free account at: https://gelbooru.com/index.php?page=account&s=register
  After registering, go to Account → API Key
  Example: GELBOORU_API_KEY=your_key   GELBOORU_USER=your_user_id

DANBOORU_USER + DANBOORU_API_KEY
  Free account at: https://danbooru.donmai.us/users/new
  After registering, go to My Account → API Key
  Example: DANBOORU_USER=yourusername   DANBOORU_API_KEY=your_api_key

E621_USER + E621_API_KEY    (requires NSFW_MODE=true)
  Free account at: https://e621.net/users/new
  After registering, go to Account → Manage API Access → Generate Key
  Example: E621_USER=yourusername   E621_API_KEY=your_api_key
  Note: Without a key, e621 still works but has a lower rate limit

WAIFUIM_API_KEY
  Free key at: https://waifu.im/dashboard
  Example: WAIFUIM_API_KEY=your_token_here

WAIFU_IT_API_KEY
  Free key from: join waifu.it's Discord server → run the /token command
  Discord: https://discord.gg/waifuit
  Example: WAIFU_IT_API_KEY=your_token_here

━━━ OPTIONAL (tuning) ━━━

DEBUG_FETCH=false          Set to true for verbose provider logs (useful if images aren't loading)
TRUE_RANDOM=false          Set to true to ignore provider weights (equal random chance)
REQUEST_TIMEOUT=14         Seconds before a provider request times out
FETCH_ATTEMPTS=40          How many fetch attempts before giving up on a request
MAX_USED_GIFS_PER_USER=1000   How many image hashes to remember per user (avoids repeats)
AUTOSAVE_INTERVAL=30       How often (in seconds) to save tag/history data to disk
DATA_FILE=data_nsfw.json   Filename for persistent storage
PORT=10000                 Flask port (Render sets this automatically, don't change)

═══════════════════════════════════════════
  PROVIDERS SUMMARY (18 total)
═══════════════════════════════════════════

Always active (no auth needed):
  rule34      — rule34.xxx — explicit, heavy GIF bias — weight 45
  gelbooru    — gelbooru.com — huge library — weight 18
  nekosapi    — nekosapi.com — explicit, no auth — weight 15
  konachan    — konachan.com — anime explicit — weight 12
  nekobot     — nekobot.xyz — various categories — weight 10
  danbooru    — danbooru.donmai.us — anime explicit — weight 8
  nekos_life  — nekos.life — animated/explicit — weight 8
  tbib        — tbib.org — The Big Image Board — weight 7
  xbooru      — xbooru.com — gelbooru-based — weight 7
  realbooru   — realbooru.com — gelbooru-based — weight 6
  waifu_im    — waifu.im — optional free key — weight 5
  paheal      — rule34.paheal.net — XML API — weight 5
  waifu_it    — waifu.it — optional free key — weight 4
  nekos_moe   — nekos.moe — NSFW gallery — weight 3
  waifu_pics  — waifu.pics — NSFW image API — weight 2

Requires NSFW_MODE=true:
  e621        — e621.net — furry/explicit, rich tag system — weight 18
  yandere     — yande.re — high-quality anime explicit — weight 14
  hypnohub    — hypnohub.net — gelbooru-based — weight 6

═══════════════════════════════════════════
  AUTO BEHAVIORS (no commands needed)
═══════════════════════════════════════════
• Bot joins voice channels on startup
• Bot follows users when they join/switch VC
• Bot moves to first occupied VC (by VC_IDS order)
• Bot stays in last VC even when all users leave (24/7 presence)
• Bot reconnects every 5 minutes if disconnected
• On user JOIN: sends embed message + NSFW image to text channel
                AND sends same image + message as DM to the user
• On user LEAVE: sends embed message + NSFW image to text channel
                 AND sends same image + message as DM to the user
• Every 45 seconds with 2+ users in VC: drops a random NSFW image
