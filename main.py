"""
================================================================================
🤖 TELEGRAM ACADEMIC BOT - VASUKI (v11.0 - COMPLETE)
================================================================================
Author: Custom AI
Architecture: Monolithic (Supabase Integrated)
System:
  - Python 3.10+
  - Python-Telegram-Bot v21+
  - Google Gemini AI
  - Flask (Keep-Alive)
  - Supabase (PostgreSQL Persistence)

Features:
  1. 🛡️ CLOUD PERSISTENCE (Supabase JSONB Storage)
  2. ⚡ NON-BLOCKING SAVES (Threaded Database Writes)
  3. 🔄 SMART STATE MANAGEMENT
  4. 🛡️ AUTO-RESTORE SYSTEM
  5. 📸 AI AUTO-SCHEDULING (Gemini Vision)
  6. 🎨 HTML RICH MESSAGES
  7. 📊 ATTENDANCE TRACKING & EXPORT
  8. 👥 MULTIPLE ADMIN SUPPORT
  9. 📚 SHOW ALL SUBJECTS (Restored)
================================================================================
"""

import logging
import asyncio
import os
import json
import io
import time
import traceback
import html
import re
from threading import Thread
from datetime import datetime, timedelta, time as dtime
import pytz
from flask import Flask
from dotenv import load_dotenv
import google.generativeai as genai
from PIL import Image
import urllib.request

# ------------------------------------------------------------------------------
# 📦 EXTERNAL IMPORTS
# ------------------------------------------------------------------------------
from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    ChatMember,
    ChatMemberUpdated
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ChatMemberHandler,
    Defaults,
    filters,
    JobQueue
)
from telegram.request import HTTPXRequest 

# SUPABASE CLIENT
from supabase import create_client, Client

# ==============================================================================
# 🔐 1. SYSTEM CONFIGURATION & ENVIRONMENT
# ==============================================================================
load_dotenv()

# Critical Environment Variables
TOKEN = os.environ.get("BOT_TOKEN")

# Robust Multi-Admin Parsing
# Robust Multi-Admin Parsing
raw_admins = os.environ.get("ADMIN_USERNAMES", "")
ADMIN_USERNAMES = [
    u.strip().replace("@", "").lower() 
    for u in raw_admins.split(",") 
    if u.strip()
]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ENV_GROUP_ID = os.environ.get("GROUP_CHAT_ID")

# SUPABASE CREDENTIALS
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Timezone Configuration (India Standard Time)
IST = pytz.timezone('Asia/Kolkata')

# ------------------------------------------------------------------------------
# 📝 LOGGING CONFIGURATION
# ------------------------------------------------------------------------------
class ISTFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp, IST)
        return dt.timetuple()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
for handler in logging.getLogger().handlers:
    handler.setFormatter(ISTFormatter(fmt='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S IST'))

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# 🧠 AI & DB ENGINE INITIALIZATION
# ------------------------------------------------------------------------------
# Gemini is LAZY LOADED to save memory (~100MB)
model = None  # Will be initialized on first AI request

def get_gemini_model():
    """Lazy-load Gemini model only when needed"""
    global model
    if model is None and GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.5-flash')
            logger.info("✅ Gemini AI loaded (lazy)")
        except Exception as e:
            logger.error(f"❌ Gemini AI Failed: {e}")
    return model

# Supabase Connection
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase Connected")
    except Exception as e:
        logger.error(f"❌ Supabase Connection Failed: {e}")
else:
    logger.critical("⚠️ SUPABASE_URL or SUPABASE_KEY missing! Persistence will fail on Render.")

# ==============================================================================
# 💾 2. DATABASE & PERSISTENCE LAYER (SUPABASE VERSION)
# ==============================================================================

# Regex to identify Menu Buttons (Updated with 📚)
MENU_REGEX = "^(📸|🧠|🟦|🟧|➕|📂|✏️|🗑️|📅|📊|📚|📤|📥|🔙)"

# Default Database Structure
DEFAULT_DB = {
    "config": {
        "group_id": int(ENV_GROUP_ID) if ENV_GROUP_ID else None,
        "group_name": "Linked via Env Var" if ENV_GROUP_ID else "❌ No Group Linked"
    },
    "subjects": {
        "CSDA": [], 
        "AICS": []
    }, 
    "active_jobs": [],
    "attendance": {},
    "feedback": [],
    "system_stats": {
        "start_time": time.time(),
        "classes_scheduled": 0,
        "ai_requests": 0
    },
    "schedules": [] 
}

DB = DEFAULT_DB.copy()

def load_db():
    global DB
    if not supabase:
        logger.warning("⚠️ Using In-Memory DB (No Supabase)")
        return

    try:
        response = supabase.table("bot_storage").select("data").eq("id", 1).execute()
        if response.data and len(response.data) > 0:
            cloud_data = response.data[0]['data']
            if not cloud_data:
                save_db()
            else:
                DB = cloud_data
                if "active_jobs" not in DB: DB["active_jobs"] = []
                if "schedules" not in DB: DB["schedules"] = []
                if "subjects" not in DB: DB["subjects"] = {"CSDA": [], "AICS": []}
                if "admins" not in DB: DB["admins"] = []
                if "topics" not in DB: DB["topics"] = {}
                logger.info("📂 Database Loaded from Supabase.")
        else:
            logger.info("🆕 No Cloud Data found. Initializing...")
            save_db()
    except Exception as e:
        logger.error(f"❌ Failed to load DB from Cloud: {e}")

def _save_db_thread():
    if not supabase: return
    # Delays in seconds: 1m, 1m, 1m, 5m, 10m
    delays = [60, 60, 60, 300, 600]
    
    for i, delay in enumerate(delays):
        try:
            supabase.table("bot_storage").upsert({"id": 1, "data": DB}).execute()
            logger.info("✅ Database saved to Cloud.")
            return
        except Exception as e:
            logger.error(f"❌ Cloud Save Failed (Attempt {i+1}/{len(delays)}): {e}")
            logger.info(f"⏳ Retrying in {delay/60} minutes...")
            time.sleep(delay)
    
    # Final attempt or failure
    logger.error("❌ CLOUD SAVE FAILED after multiple attempts.")

def save_db():
    t = Thread(target=_save_db_thread)
    t.start()

async def force_cloud_save(update, context):
    """Manually trigger cloud save with UI feedback"""
    if not await require_private_admin(update, context): return
    
    msg = await update.message.reply_text(
        "☁️ <b>SAVING TO CLOUD...</b>\n"
        "⏳ <i>Please wait...</i>",
        parse_mode=ParseMode.HTML
    )
    
    try:
        # Run sync save in thread but wait for it
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _save_db_thread)
        
        await msg.edit_text(
            "✅ <b>CLOUD SAVE SUCCESSFUL!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💾 <i>Data has been synced to Supabase.</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>SAVE FAILED!</b>\n\n"
            f"<i>Error:</i> {str(e)}",
            parse_mode=ParseMode.HTML
        )

def refresh_db():
    """Reload database from Supabase - for live sync without restart"""
    global DB
    if not supabase:
        logger.warning("⚠️ Cannot refresh: No Supabase connection")
        return False
    
    try:
        response = supabase.table("bot_storage").select("data").eq("id", 1).execute()
        if response.data and len(response.data) > 0:
            cloud_data = response.data[0]['data']
            if cloud_data:
                # Preserve only runtime data, update everything else
                old_active_jobs = DB.get("active_jobs", [])
                DB.update(cloud_data)
                # Keep local active_jobs if cloud has none (runtime jobs)
                if not cloud_data.get("active_jobs"):
                    DB["active_jobs"] = old_active_jobs
                logger.info("🔄 Database refreshed from Supabase")
                return True
    except Exception as e:
        logger.error(f"❌ Refresh failed: {e}")
    return False

async def refresh_db_command(update, context):
    """Manual database refresh command"""
    if not await require_private_admin(update, context): return
    
    msg = await update.message.reply_text(
        "🔄 <b>REFRESHING DATABASE...</b>",
        parse_mode=ParseMode.HTML
    )
    
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, refresh_db)
    
    if success:
        await msg.edit_text(
            "✅ <b>DATABASE REFRESHED!</b>\n\n"
            "<i>Supabase changes are now live.</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        await msg.edit_text(
            "❌ <b>REFRESH FAILED!</b>\n\n"
            "<i>Check logs for details.</i>",
            parse_mode=ParseMode.HTML
        )

load_db()

# ------------------------------------------------------------------------------
# 🔄 JOB PERSISTENCE HELPERS
# ------------------------------------------------------------------------------
def add_job_to_db(job_name, run_timestamp, chat_id, data):
    job_entry = {
        "name": job_name,
        "timestamp": run_timestamp,
        "chat_id": chat_id,
        "data": data
    }
    DB["active_jobs"] = [j for j in DB["active_jobs"] if j["name"] != job_name]
    DB["active_jobs"].append(job_entry)
    save_db()

def remove_job_from_db(job_name):
    original_count = len(DB["active_jobs"])
    DB["active_jobs"] = [j for j in DB["active_jobs"] if j["name"] != job_name]
    if len(DB["active_jobs"]) < original_count:
        save_db()

def update_all_jobs_chat_id(new_chat_id):
    """Update all pending jobs to use the new chat_id - fixes 'kicked from group' errors"""
    updated = 0
    for job in DB.get("active_jobs", []):
        if job.get("chat_id") != new_chat_id:
            job["chat_id"] = new_chat_id
            updated += 1
    if updated > 0:
        save_db()
        logger.info(f"🔄 Updated {updated} jobs with new chat_id: {new_chat_id}")
    return updated

def cleanup_old_data(context=None):
    """
    Clean up old data to prevent memory bloat.
    Can be run as a scheduled job (context) or standalone.
    """
    cleaned = 0
    now_ts = time.time()
    thirty_days = 30 * 24 * 60 * 60
    
    # 1. Clean Attendance
    keys_to_remove = []
    if "attendance" in DB:
        for job_id in DB["attendance"]:
            try:
                parts = job_id.split('_')
                if len(parts) >= 4:
                    ts = int(parts[3])
                    if now_ts - ts > thirty_days:
                        keys_to_remove.append(job_id)
            except:
                continue
            
    for k in keys_to_remove:
        del DB["attendance"][k]
        cleaned += 1

    # 2. Clean Feedback (Keep last 50)
    if "feedback" in DB and len(DB["feedback"]) > 50:
        old_len = len(DB["feedback"])
        DB["feedback"] = DB["feedback"][-50:]
        removed = old_len - 50
        logger.info(f"🧹 Pruned {removed} old feedback entries")
        cleaned += removed



    # 4. Clean Stale Active Jobs (older than 24h)
    if "active_jobs" in DB:
        valid_jobs = []
        stale_jobs = 0
        for job in DB["active_jobs"]:
            # If job is more than 24 hours in the past, it's dead
            if job["timestamp"] < now_ts - 86400:
                stale_jobs += 1
            else:
                valid_jobs.append(job)
        
        if stale_jobs > 0:
            DB["active_jobs"] = valid_jobs
            logger.info(f"🧹 Removed {stale_jobs} stale active jobs")
            cleaned += stale_jobs

    if cleaned > 0:
        save_db()
        logger.info(f"🧹 Total cleanup: {cleaned} records removed.")

# ==============================================================================
# 🚦 3. CONVERSATION STATES
# ==============================================================================
(
    SELECT_BATCH, NEW_SUBJECT_INPUT, SELECT_SUB_OR_ADD, SELECT_DAYS, 
    INPUT_START_DATE, INPUT_END_DATE, INPUT_TIME, INPUT_LINK,
    SELECT_OFFSET, MSG_TYPE_CHOICE, INPUT_MANUAL_MSG, GEMINI_PROMPT_INPUT,
    EDIT_SELECT_JOB, EDIT_CHOOSE_FIELD, EDIT_NEW_VALUE, ADD_ADMIN_INPUT,
    REMOVE_ADMIN_INPUT, CUSTOM_OFFSET_INPUT, NIGHT_SCHEDULE_TIME,
    CUSTOM_MSG_BATCH, CUSTOM_MSG_TIME, CUSTOM_MSG_START, CUSTOM_MSG_END,
    CUSTOM_MSG_TEXT, CUSTOM_MSG_LINK, CUSTOM_MSG_DAYS,
    SELECT_TOPIC, ADD_TOPIC_NAME, ADD_TOPIC_ID, REMOVE_TOPIC_INPUT,
    EDIT_SUB_SELECT_BATCH, EDIT_SUB_SELECT_SUBJECT, EDIT_SUB_ACTION, EDIT_SUB_NEW_NAME,
    RESET_CONFIRM, EDIT_TOPIC_SELECT, EDIT_TOPIC_NEW_NAME, DELETE_TOPIC_CONFIRM,
    EDIT_SELECT_SCOPE, EDIT_BULK_DAYS
) = range(40)

# Regex to match any menu button for canceling wizards
MENU_REGEX = "^(📸 AI Auto-Schedule|🧠 Custom AI|🟦 Schedule CSDA|🟧 Schedule AICS|📝 Custom Message|➕ Add Subject|📂 More Options|✏️ Edit Class|🗑️ Delete Class|📅 View Schedule|📊 Attendance|📚 All Subjects|📤 Export Data|📥 Import Data|👥 Manage Admins|💬 Manage Topics|🛠️ Admin Tools|🔙 Back to Main|🌙 Night Schedule|☁️ Force Save|🔄 Reset System)$"

# ==============================================================================
# 🛠️ UTILITY FUNCTIONS
# ==============================================================================

# Telegram-allowed HTML tags (official list)
ALLOWED_HTML_TAGS = [
    'b', 'strong',           # Bold
    'i', 'em',               # Italic
    'u', 'ins',              # Underline
    's', 'strike', 'del',    # Strikethrough
    'span', 'tg-spoiler',    # Spoiler
    'a',                     # Links
    'code', 'pre',           # Code
    'blockquote',            # Blockquote
    'tg-emoji'               # Custom emoji
]

def validate_html(text):
    """
    Validate that HTML only uses Telegram-allowed tags.
    Returns: (is_valid: bool, error_message: str or None)
    """
    import re
    # Find all tags (opening and closing)
    tags = re.findall(r'</?(\w+(?:-\w+)?)[^>]*>', text)
    invalid_tags = [t for t in tags if t.lower() not in ALLOWED_HTML_TAGS]
    
    if invalid_tags:
        unique_invalid = list(set(invalid_tags))
        return False, f"❌ Invalid HTML tags: {', '.join(unique_invalid)}\n\n✅ Allowed: {', '.join(ALLOWED_HTML_TAGS[:8])}..."
    return True, None

def sanitize_html(text):
    """Remove or convert forbidden HTML tags to safe alternatives"""
    if not text:
        return text
    # Replace common forbidden tags
    replacements = [
        ('<br>', '\n'), ('<br/>', '\n'), ('<br />', '\n'),
        ('<p>', ''), ('</p>', '\n'),
        ('<div>', ''), ('</div>', '\n'),
        ('<h1>', '<b>'), ('</h1>', '</b>\n'),
        ('<h2>', '<b>'), ('</h2>', '</b>\n'),
        ('<h3>', '<b>'), ('</h3>', '</b>\n'),
        ('<li>', '• '), ('</li>', '\n'),
        ('<ul>', ''), ('</ul>', ''),
        ('<ol>', ''), ('</ol>', ''),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text

async def send_long_message(bot, chat_id, text, parse_mode=None, reply_markup=None, **kwargs):
    """
    Split and send long messages in chunks of 4000 characters.
    Handles Telegram's 4096 character limit safely.
    """
    MAX_LEN = 4000  # Leave buffer for safety
    
    if len(text) <= MAX_LEN:
        return await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, **kwargs)
    
    # Split into chunks
    chunks = []
    current = ""
    for line in text.split('\n'):
        if len(current) + len(line) + 1 > MAX_LEN:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + '\n' + line if current else line
    if current:
        chunks.append(current)
    
    # Send each chunk
    last_msg = None
    for i, chunk in enumerate(chunks):
        # Only add reply_markup to last chunk
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            last_msg = await bot.send_message(
                chat_id, chunk, 
                parse_mode=parse_mode, 
                reply_markup=markup, 
                **kwargs
            )
            await asyncio.sleep(0.1)  # Rate limiting - 10 msg/sec max
        except Exception as e:
            if "too long" in str(e).lower():
                # Emergency split
                for j in range(0, len(chunk), MAX_LEN):
                    await bot.send_message(chat_id, chunk[j:j+MAX_LEN], **kwargs)
                    await asyncio.sleep(0.1)
            else:
                raise e
    return last_msg

async def send_message_safe(bot, chat_id, text, parse_mode=ParseMode.HTML, **kwargs):
    """
    Bulletproof message sender with multiple fallbacks.
    1. Try with HTML
    2. If fails, strip HTML and send plain
    3. If too long, chunk it
    """
    try:
        # First try normal send
        if len(text) > 4000:
            return await send_long_message(bot, chat_id, text, parse_mode=parse_mode, **kwargs)
        return await bot.send_message(chat_id, text, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        err = str(e).lower()
        if "parse" in err or "entity" in err or "tag" in err:
            # HTML parsing error - strip all HTML and retry
            import re
            clean_text = re.sub(r'<[^>]+>', '', text)
            logger.warning(f"HTML parse error, sending plain text: {e}")
            return await bot.send_message(chat_id, clean_text, **kwargs)
        elif "too long" in err:
            return await send_long_message(bot, chat_id, text, parse_mode=parse_mode, **kwargs)
        else:
            raise e

# ==============================================================================
# 🌐 4. KEEP-ALIVE SERVER (FLASK)
# ==============================================================================
app = Flask('')

@app.route('/')
def home():
    uptime = int(time.time() - DB["system_stats"]["start_time"])
    gid = DB["config"]["group_id"]
    return f"""
    <html>
    <body style="font-family: monospace; background: #0d1117; color: #c9d1d9; padding: 20px;">
        <h1>🤖 VASUKI BOT STATUS: <span style="color: #2ea043;">ONLINE</span></h1>
        <hr>
        <p><b>Server Time:</b> {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}</p>
        <p><b>Persistence:</b> {'Supabase ✅' if supabase else 'Local ⚠️'}</p>
        <p><b>Target Group:</b> {gid}</p>
        <p><b>Pending Jobs:</b> {len(DB['active_jobs'])}</p>
    </body>
    </html>
    """

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ==============================================================================
# 🧠 5. ARTIFICIAL INTELLIGENCE LOGIC
# ==============================================================================
async def analyze_timetable_image(image_bytes):
    ai_model = get_gemini_model()
    if not ai_model: return None
    prompt = """
    Analyze this timetable image. Extract class details into strict JSON:
    [{"day": "Mon", "time": "10:00", "subject": "Maths", "batch": "CSDA"}]
    Constraints: 
    1. Days: Mon, Tue, Wed, Thu, Fri, Sat, Sun.
    2. Time: 24h format HH:MM.
    3. Return ONLY raw JSON string.
    """
    try:
        DB["system_stats"]["ai_requests"] += 1
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((1024, 1024)) 
        response = await asyncio.to_thread(ai_model.generate_content, [prompt, img])
        text = response.text
        text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```", "", text)
        return json.loads(text.strip())
    except Exception as e:
        logger.error(f"AI Vision Error: {e}")
        return None

async def generate_hype_message(batch, subject, time_str, link):
    ai_model = get_gemini_model()
    if not ai_model: return None
    try:
        DB["system_stats"]["ai_requests"] += 1
        date_str = datetime.now(IST).strftime('%A, %d %B')
        prompt = (
            f"Create a HTML notification for a class.\n"
            f"Info: {batch} | {subject} | {time_str} | {date_str} | {link}\n"
            f"Rules: Use HTML tags (<b>, <i>, <code>, <a href='...'>). "
            f"Do NOT use <br> or <div>. Use newlines (\\n) for breaks. "
            f"Include <a href='{link}'>JOIN CLASS</a>. Make it exciting."
        )
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        text = response.text
        # Sanitize common forbidden tags
        text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<p>", "").replace("</p>", "\n")
        return text
    except Exception: return None

async def custom_gemini_task(prompt):
    ai_model = get_gemini_model()
    if not ai_model: return "❌ AI Disabled."
    try:
        DB["system_stats"]["ai_requests"] += 1
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        return response.text
    except Exception as e: return f"Error: {e}"

# ==============================================================================
# 🎨 6. UI COMPONENTS
# ==============================================================================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📸 AI Auto-Schedule"), KeyboardButton("🧠 Custom AI")],
        [KeyboardButton("🟦 Schedule CSDA"), KeyboardButton("🟧 Schedule AICS")],
        [KeyboardButton("📝 Custom Message"), KeyboardButton("➕ Add Subject")],
        [KeyboardButton("📂 More Options ⤵️")]
    ], resize_keyboard=True, is_persistent=True)

def get_more_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("✏️ Edit Class"), KeyboardButton("🗑️ Delete Class")],
        [KeyboardButton("📅 View Schedule"), KeyboardButton("📊 Attendance")],
        [KeyboardButton("📚 All Subjects"), KeyboardButton("📤 Export Data")], 
        [KeyboardButton("📥 Import Data"), KeyboardButton("👥 Manage Admins")],
        [KeyboardButton("🛠️ Admin Tools"), KeyboardButton("🔙 Back to Main")]
    ], resize_keyboard=True, is_persistent=True)

def get_admin_mgmt_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("👤 Add Admin"), KeyboardButton("🗑️ Remove Admin")],
        [KeyboardButton("📋 View Admins"), KeyboardButton("🔙 Back to Main")]
    ], resize_keyboard=True, is_persistent=True)

def days_keyboard(selected_days):
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    buttons = []
    row = []
    for d in days:
        icon = "✅" if d in selected_days else "⬜"
        row.append(InlineKeyboardButton(f"{icon} {d}", callback_data=f"toggle_{d}"))
        if len(row) == 3: 
            buttons.append(row)
            row = []
    if row: buttons.append(row)
    buttons.append([InlineKeyboardButton("🚀 DONE", callback_data="days_done")])
    return InlineKeyboardMarkup(buttons)

# ==============================================================================
# 🛡️ 7. ACCESS CONTROL
# ==============================================================================
def is_admin(username):
    """Check if username is an admin (from env or database)"""
    if not username: return False
    username = str(username).lower()
    
    # Check environment variable admins (already lowercased)
    if ADMIN_USERNAMES and ADMIN_USERNAMES != ['']:
        if username in ADMIN_USERNAMES:
            return True
    
    # Check database admins
    db_admins = [a.lower() for a in DB.get("admins", [])]
    if username in db_admins:
        return True
        
    return False

def is_super_admin(username):
    """Check if username is the primary admin (from env)"""
    if not username: return False
    # Strict: explicit list required
    if ADMIN_USERNAMES and username in ADMIN_USERNAMES:
        return True
    return False

def is_private_chat(update):
    """Check if the message is from a private chat"""
    return update.effective_chat.type == 'private'

async def require_private_admin(update, context):
    """
    Check if user is admin AND in private chat.
    Returns True if allowed, False if not (and sends appropriate message).
    """
    try:
        user = update.effective_user
        
        # Check if admin
        if not is_admin(user.username):
            await update.message.reply_text(
                f"⛔ <b>ACCESS DENIED</b>\n\n"
                f"<i>You are not authorized to control Vasuki Bot.</i>\n\n"
                f"🔐 <b>Grant Access:</b> Contact @AvadaKedavaaraa\n"
                f"🔑 <b>Or Login:</b> <code>/login [password]</code>",
                parse_mode=ParseMode.HTML
            )
            return False
        
        # Check if private chat
        if not is_private_chat(update):
            await update.message.reply_text(
                "🔒 <b>PRIVATE CHAT ONLY!</b>\n\n"
                "<i>Please use this command in a private chat with me.</i>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error in require_private_admin: {e}")
        return False

# ==============================================================================
# 👥 ADMIN MANAGEMENT
# ==============================================================================
async def start_add_admin(update, context):
    """Start the add admin conversation"""
    try:
        if not await require_private_admin(update, context): return ConversationHandler.END
        
        # Only super admins can add other admins
        if not is_super_admin(update.effective_user.username):
            await update.message.reply_text(
                "⛔ <b>ACCESS DENIED!</b>\n\n"
                "<i>Only the primary admin can add new admins.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        current_admins = DB.get("admins", [])
        admin_list = "\n".join([f"• @{a}" for a in current_admins]) if current_admins else "<i>No additional admins</i>"
        
        await update.message.reply_text(
            "👥 <b>ADD NEW ADMIN</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Current Admins:</b>\n{admin_list}\n\n"
            "<i>Enter the username to add (without @):</i>\n"
            "<i>Or send /cancel to abort.</i>",
            parse_mode=ParseMode.HTML
        )
        return ADD_ADMIN_INPUT
    except Exception as e:
        logger.error(f"Error in start_add_admin: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def save_new_admin(update, context):
    """Save the new admin username"""
    try:
        username = update.message.text.strip().replace("@", "")
        
        if not username or len(username) < 3:
            await update.message.reply_text(
                "❌ <b>INVALID USERNAME!</b>\n\n"
                "<i>Username must be at least 3 characters.</i>",
                parse_mode=ParseMode.HTML
            )
            return ADD_ADMIN_INPUT
        
        if "admins" not in DB:
            DB["admins"] = []
        
        if username in DB["admins"]:
            await update.message.reply_text(
                f"⚠️ <b>ALREADY AN ADMIN!</b>\n\n"
                f"<i>@{username} is already in the admin list.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        DB["admins"].append(username)
        save_db()
        
        await update.message.reply_text(
            f"✅ <b>ADMIN ADDED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>@{username}</b> is now an admin.\n\n"
            f"<i>They can now use all admin features!</i> 🎉",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in save_new_admin: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def start_remove_admin(update, context):
    """Start the remove admin conversation"""
    try:
        if not await require_private_admin(update, context): return ConversationHandler.END
        
        if not is_super_admin(update.effective_user.username):
            await update.message.reply_text(
                "⛔ <b>ACCESS DENIED!</b>\n\n"
                "<i>Only the primary admin can remove admins.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        current_admins = DB.get("admins", [])
        if not current_admins:
            await update.message.reply_text(
                "📭 <b>NO ADMINS TO REMOVE!</b>\n\n"
                "<i>There are no additional admins.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        admin_list = "\n".join([f"• @{a}" for a in current_admins])
        
        await update.message.reply_text(
            "🗑️ <b>REMOVE ADMIN</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Current Admins:</b>\n{admin_list}\n\n"
            "<i>Enter the username to remove (without @):</i>\n"
            "<i>Or send /cancel to abort.</i>",
            parse_mode=ParseMode.HTML
        )
        return REMOVE_ADMIN_INPUT
    except Exception as e:
        logger.error(f"Error in start_remove_admin: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def remove_admin_save(update, context):
    """Remove the admin username"""
    try:
        username = update.message.text.strip().replace("@", "")
        
        if username not in DB.get("admins", []):
            await update.message.reply_text(
                f"❌ <b>NOT FOUND!</b>\n\n"
                f"<i>@{username} is not in the admin list.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        DB["admins"].remove(username)
        save_db()
        
        await update.message.reply_text(
            f"✅ <b>ADMIN REMOVED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>@{username}</b> is no longer an admin.",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in remove_admin_save: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def view_admins(update, context):
    """View all current admins"""
    try:
        if not await require_private_admin(update, context): return
        
        env_admins = ADMIN_USERNAMES if ADMIN_USERNAMES and ADMIN_USERNAMES != [''] else []
        db_admins = DB.get("admins", [])
        
        msg = "👥 <b>ADMIN LIST</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if env_admins:
            msg += "🔐 <b>Primary Admins (ENV):</b>\n"
            for a in env_admins:
                msg += f"   • @{a}\n"
            msg += "\n"
        
        if db_admins:
            msg += "👤 <b>Additional Admins:</b>\n"
            for a in db_admins:
                msg += f"   • @{a}\n"
        else:
            msg += "<i>No additional admins added.</i>\n"
        
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in view_admins: {e}")
        await update.message.reply_text("❌ An error occurred.")

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track when bot is added to groups - only update if no group linked"""
    result = update.my_chat_member.new_chat_member
    chat = update.effective_chat
    
    if result.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR]:
        # Only update group if no group is currently linked
        current_group = DB.get("config", {}).get("group_id")
        
        if current_group is None:
            # No group linked - set this one
            DB["config"]["group_id"] = chat.id
            DB["config"]["group_name"] = chat.title
            save_db()
            logger.info(f"🆕 LINKED GROUP: {chat.title} ({chat.id})")
            
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"🤖 <b>VASUKI SYSTEM ONLINE</b>\n"
                     f"✅ Connected: <b>{chat.title}</b>\n"
                     f"🕒 Timezone: IST (GMT+5:30)\n"
                     f"🚀 <b>Ready to schedule classes.</b>",
                parse_mode=ParseMode.HTML
            )
        elif current_group == chat.id:
            # Same group - update name if changed
            if DB["config"]["group_name"] != chat.title:
                DB["config"]["group_name"] = chat.title
                save_db()
                logger.info(f"📝 Updated group name: {chat.title}")
        else:
            # Different group - log but don't overwrite
            logger.info(f"ℹ️ Bot added to {chat.title} but already linked to {DB['config']['group_name']}")

async def updategroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update the linked group ID - ENV ADMINS ONLY, responds in private"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Only works in groups
    if chat.type == "private":
        await update.message.reply_text(
            "⚠️ <b>Use this command in a GROUP, not private chat!</b>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Both ENV and DB admins allowed
    if not is_admin(user.username):
        # Silently ignore non-admins
        return

    
    # Delete the command message from group immediately
    try:
        await update.message.delete()
    except:
        pass  # May fail if bot lacks delete permission
    
    old_id = DB.get("config", {}).get("group_id")
    new_id = chat.id
    
    # Update config
    DB["config"]["group_id"] = new_id
    DB["config"]["group_name"] = chat.title
    
    # Update all pending jobs
    updated_jobs = update_all_jobs_chat_id(new_id)
    
    save_db()
    
    # Send response to admin's PRIVATE chat
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"✅ <b>GROUP UPDATED!</b>\n"
                 f"━━━━━━━━━━━━━━━━━━━━\n\n"
                 f"📍 <b>Group:</b> {chat.title}\n"
                 f"🆔 <b>New ID:</b> <code>{new_id}</code>\n"
                 f"🔄 <b>Jobs Updated:</b> {updated_jobs}\n\n"
                 f"<i>All scheduled messages will now be sent here.</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        # Fallback: send a silent message in group if private fails
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"✅ Group updated! Check /verifytopics for details.",
            disable_notification=True
        )
    
    logger.info(f"🔄 Group updated: {old_id} → {new_id} ({chat.title})")

# ==============================================================================
# 🏠 8. CORE HANDLERS
# ==============================================================================

async def verify_topic_connectivity(bot, group_id, topic_id):
    """
    Verify topic is functional by sending and immediately deleting a test message.
    Returns: (success: bool, error_message: str or None)
    """
    if not group_id or not topic_id:
        return False, "No group or topic configured"
    
    try:
        # Send silent test message
        msg = await bot.send_message(
            chat_id=group_id,
            text="🔄 Topic verification...",
            message_thread_id=int(topic_id),
            disable_notification=True
        )
        # Immediately delete
        await msg.delete()
        return True, None
    except Exception as e:
        error = str(e).lower()
        if "thread" in error or "topic" in error:
            return False, "Topic not found or closed"
        elif "chat not found" in error:
            return False, "Group not accessible"
        else:
            return False, str(e)[:50]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_type = update.effective_chat.type

    # Strict Access Control - non-admins get nothing
    if not is_admin(user.username):
        await update.message.reply_text(
            f"⛔ <b>ACCESS DENIED</b>\n\n"
            f"<i>You are not authorized to control The Bot.</i>\n\n"
            f"🔐 <b>Grant Access:</b> Contact @AvadaKedavaaraa",
            parse_mode=ParseMode.HTML
        )
        return

    # GROUP/SUPERGROUP: Link and auto-delete message
    if chat_type in ['group', 'supergroup']:
        DB["config"]["group_id"] = update.effective_chat.id
        DB["config"]["group_name"] = update.effective_chat.title
        save_db()
        try:
            msg = await update.message.reply_text(
                f"🚀 <b>VASUKI ACTIVATED!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <i>Successfully linked to:</i>\n"
                f"📍 <b>{update.effective_chat.title}</b>\n\n"
                f"💡 <i>Use /start in DM for full control!</i>\n\n"
                f"<i>This message will auto-delete in 5 seconds...</i>",
                parse_mode=ParseMode.HTML,
                disable_notification=True  # Silent
            )
            # Schedule auto-delete after 5 seconds
            await asyncio.sleep(5)
            try:
                await msg.delete()
            except:
                pass
        except Exception as e:
            logger.error(f"Failed to reply in group start: {e}")
        return

    # PRIVATE CHAT: Show dashboard with topic verification
    grp_name = DB.get("config", {}).get("group_name", "❌ No Group Linked")
    grp_id = DB.get("config", {}).get("group_id")
    topics = DB.get("topics", {})
    
    # Verify group and topic connectivity
    group_status = "🟢" if grp_id else "🔴"
    topic_status = "🔴 None"
    topic_count = len(topics)
    
    if grp_id and topics:
        # Test first topic
        first_topic_id = list(topics.keys())[0]
        first_topic_name = topics[first_topic_id]
        success, error = await verify_topic_connectivity(context.bot, grp_id, first_topic_id)
        
        if success:
            topic_status = f"🟢 {topic_count} connected"
        else:
            topic_status = f"🟡 {topic_count} (verify needed)"
    elif topics:
        topic_status = f"🟡 {topic_count} (no group)"
    
    # Build keyboard with verify option
    kb = []
    if grp_id and topics:
        kb.append([InlineKeyboardButton("🔄 Verify Topics", callback_data="verify_topics")])
    
    try:
        await update.message.reply_text(
            f"⚡ <b>VASUKI COMMAND CENTER</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 <i>Welcome back,</i> <b>{user.first_name}!</b>\n\n"
            f"🔌 <b>CONNECTION STATUS</b>\n"
            f"┣ 🎯 <b>Target:</b> {group_status} {grp_name}\n"
            f"┣ 💬 <b>Topics:</b> {topic_status}\n"
            f"┣ ⏰ <b>Time:</b> {datetime.now(IST).strftime('%H:%M IST')}\n"
            f"┣ 📅 <b>Scheduled:</b> {len(DB.get('active_jobs', []))} classes\n"
            f"┗ 💾 <b>Storage:</b> {'☁️ Supabase' if supabase else '💻 Local'}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Select an option below to begin!</i> 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb) if kb else get_main_keyboard()
        )
        # Also show main keyboard
        if kb:
            await update.message.reply_text("📱 <b>Main Menu</b>", parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Failed to send dashboard: {e}")

# Pagination constant
TOPICS_PER_PAGE = 10

async def verify_topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command handler for /verifytopics"""
    if not await require_private_admin(update, context): return
    
    await show_verify_topics_page(update, context, page=0)

async def verify_topics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination callbacks for verify topics"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("verify_page_"):
        page = int(data.split("_")[-1])
        await show_verify_topics_page(update, context, page=page, is_callback=True)

async def show_verify_topics_page(update_or_query, context, page=0, is_callback=False):
    """Helper to show a page of verified topics"""
    grp_id = DB.get("config", {}).get("group_id")
    grp_name = DB.get("config", {}).get("group_name", "Unknown")
    topics = DB.get("topics", {})
    pending_jobs = len(DB.get("active_jobs", []))
    
    # Reply target
    target = update_or_query.message if is_callback else update_or_query.message
    if is_callback:
        edit_func = update_or_query.edit_message_text
    else:
        edit_func = target.reply_text

    if not grp_id:
        text = "❌ <b>No group linked!</b>\n\nUse /start in a group first."
        if is_callback: await edit_func(text, parse_mode=ParseMode.HTML)
        else: await edit_func(text, parse_mode=ParseMode.HTML)
        return
    
    if not topics:
        text = "❌ <b>No topics registered!</b>\n\nGo to a topic and use /topic TopicName"
        if is_callback: await edit_func(text, parse_mode=ParseMode.HTML)
        else: await edit_func(text, parse_mode=ParseMode.HTML)
        return

    # Convert to list and sort
    topic_items = list(topics.items())
    total_topics = len(topic_items)
    total_pages = (total_topics + TOPICS_PER_PAGE - 1) // TOPICS_PER_PAGE
    
    # Slice for current page
    start_idx = page * TOPICS_PER_PAGE
    end_idx = start_idx + TOPICS_PER_PAGE
    current_batch = topic_items[start_idx:end_idx]
    
    # Verify this batch
    if not is_callback: # Only show "Verifying..." on initial command
        initial_msg = await target.reply_text("🔄 <b>Verifying topics...</b>", parse_mode=ParseMode.HTML)
        edit_func = initial_msg.edit_text # Switch to editing the status msg

    results = []
    success_count = 0
    # Note: Success count here is only for THIS page. 
    # To get global success count we'd need to verify all, which is slow.
    # We'll just show status for current page items.
    
    for tid, name in current_batch:
        success, error = await verify_topic_connectivity(context.bot, grp_id, tid)
        icon = "✅" if success else "❌"
        status = f"(ID: {tid})" if success else f"- {error}"
        results.append(f"{icon} <b>{name}</b> {status}")
        if success: success_count += 1
        
    # Build Navigation Keyboard
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"verify_page_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"verify_page_{page+1}"))
    
    keyboard = [nav_row] if nav_row else []
    
    # Refresh button
    keyboard.append([InlineKeyboardButton("🔄 Refresh List", callback_data=f"verify_page_{page}")])
    
    result_msg = (
        f"🔍 <b>TOPIC VERIFICATION</b> (Page {page+1}/{total_pages})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 <b>Group:</b> {grp_name}\n"
        f"🆔 <b>ID:</b> <code>{grp_id}</code>\n"
        f"📅 <b>Pending Jobs:</b> {pending_jobs}\n\n"
        + "\n".join(results) +
        f"\n\n💡 <i>Use /updategroup in your group to fix ID issues</i>"
    )
    
    await edit_func(result_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_navigation(update, context):
    try:
        user = update.effective_user
        if not is_admin(user.username):
            await update.message.reply_text(
                "⛔ <b>ACCESS DENIED</b>\nContact @AvadaKedavaaraa",
                parse_mode="HTML"
            )
            return

        msg = update.message.text
        if "More Options" in msg:
            await update.message.reply_text(
                "📂 <b>ADVANCED TOOLS</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Pick a tool from below:</i> 🛠️",
                reply_markup=get_more_keyboard(),
                parse_mode=ParseMode.HTML
            )
        elif "Manage Admins" in msg:
            if not await require_private_admin(update, context): return
            await update.message.reply_text(
                "👥 <b>ADMIN MANAGEMENT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Manage who can control this bot:</i> 👇",
                reply_markup=get_admin_mgmt_keyboard(),
                parse_mode=ParseMode.HTML
            )
        elif "Back" in msg:
            await update.message.reply_text(
                "🏠 <b>MAIN MENU</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>What would you like to do?</i> ✨",
                reply_markup=get_main_keyboard(),
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Error in handle_navigation: {e}")
        await update.message.reply_text("❌ An error occurred.")

# NEW FEATURE: VIEW ALL SUBJECTS
async def view_all_subjects(update, context):
    if not await require_private_admin(update, context): return
    
    subjects = DB.get("subjects", {})
    if not subjects or (not subjects.get("CSDA") and not subjects.get("AICS")):
        await update.message.reply_text(
            "📭 <b>NO SUBJECTS FOUND!</b>\n\n"
            "<i>Add subjects using</i> ➕ <b>Add Subject</b>",
            parse_mode=ParseMode.HTML
        )
        return

    msg = "📚 <b>REGISTERED SUBJECTS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for batch, sub_list in subjects.items():
        if sub_list:
            msg += f"🏷️ <b>{batch}</b>\n"
            for s in sub_list:
                msg += f"   ├ 📖 {s}\n"
            msg += "\n"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ==============================================================================
# 🧙‍♂️ 9. SCHEDULING WIZARD
# ==============================================================================
async def cancel_wizard(update, context):
    await update.message.reply_text(
        "❌ <b>CANCELLED</b>\n\n"
        "<i>Operation cancelled. Back to menu!</i> 👋",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def init_schedule_wizard(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    if not DB["config"]["group_id"]:
        await update.message.reply_text(
            "⛔ <b>NO GROUP LINKED!</b>\n\n"
            "<i>Add me to a group first, then use /start there.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    text = update.message.text
    batch = "CSDA" if "CSDA" in text else "AICS"
    context.user_data['sch_batch'] = batch
    context.user_data['sch_days'] = [] 
    
    subs = DB["subjects"].get(batch, [])
    if not subs:
        await update.message.reply_text(
            f"⚠️ <b>NO SUBJECTS IN {batch}!</b>\n\n"
            f"<i>Use</i> ➕ <b>Add Subject</b> <i>first.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    rows = [[InlineKeyboardButton(f"📖 {s}", callback_data=f"pick_{s}")] for s in subs]
    await update.message.reply_text(
        f"📚 <b>SELECT SUBJECT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <i>Batch:</i> <b>{batch}</b>\n\n"
        f"<i>Choose a subject below:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return SELECT_SUB_OR_ADD

async def wizard_pick_sub(update, context):
    context.user_data['sch_sub'] = update.callback_query.data.split("_")[1]
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📅 <b>SELECT DAYS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Tap to toggle days, then hit</i> <b>DONE</b> 🚀",
        reply_markup=days_keyboard([]),
        parse_mode=ParseMode.HTML
    )
    return SELECT_DAYS

async def wizard_toggle_days(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "days_done":
        if not context.user_data.get('sch_days'): return SELECT_DAYS
        await query.edit_message_text(
            "� <b>START DATE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in format:</i> <code>DD-MM-YYYY</code>\n"
            "<i>Or type:</i> <code>Today</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_START_DATE
    
    day = query.data.split("_")[1]
    days = context.user_data.get('sch_days', [])
    if day in days: days.remove(day)
    else: days.append(day)
    context.user_data['sch_days'] = days
    await query.edit_message_reply_markup(days_keyboard(days))
    return SELECT_DAYS

async def wizard_start_date(update, context):
    text = update.message.text.strip().lower()
    try:
        if text == 'today': start_dt = datetime.now(IST)
        else: start_dt = datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=IST)
        context.user_data['start_dt'] = start_dt
        await update.message.reply_text(
            "� <b>END DATE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in format:</i> <code>DD-MM-YYYY</code>\n"
            "<i>Or type:</i> <code>None</code> <i>for one-time class</i>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_END_DATE
    except:
        await update.message.reply_text(
            "❌ <b>INVALID FORMAT!</b>\n\n"
            "<i>Please use:</i> <code>DD-MM-YYYY</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_START_DATE

async def wizard_end_date(update, context):
    text = update.message.text.strip().lower()
    try:
        if text == 'none': context.user_data['end_dt'] = None
        else: context.user_data['end_dt'] = datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=IST)
        await update.message.reply_text(
            "⏰ <b>CLASS TIME</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in 24h format:</i> <code>HH:MM</code>\n"
            "<i>Example:</i> <code>14:30</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_TIME
    except:
        await update.message.reply_text(
            "❌ <b>INVALID FORMAT!</b>\n\n"
            "<i>Please use:</i> <code>DD-MM-YYYY</code>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_END_DATE

async def wizard_time(update, context):
    context.user_data['sch_time'] = update.message.text
    await update.message.reply_text(
        "🔗 <b>CLASS LINK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Paste the meeting link</i>\n"
        "<i>Or type:</i> <code>None</code>",
        parse_mode=ParseMode.HTML
    )
    return INPUT_LINK

async def wizard_link(update, context):
    context.user_data['sch_link'] = update.message.text
    
    # Check for topics
    topics = DB.get("topics", {})
    if topics:
        kb = []
        row = []
        for tid, name in topics.items():
            row.append(InlineKeyboardButton(name, callback_data=f"topic_{tid}"))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("📢 General (No Topic)", callback_data="topic_general")])
        
        await update.message.reply_text(
            "💬 <b>SELECT TOPIC</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Where should this class be posted?</i> 👇",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return SELECT_TOPIC
    else:
        # No topics, skip to offset
        context.user_data['sch_topic_id'] = None
        return await show_offset_selection(update)

async def wizard_topic_selection(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "topic_general":
        context.user_data['sch_topic_id'] = None
    else:
        tid = data.replace("topic_", "")
        context.user_data['sch_topic_id'] = int(tid)
        
    return await show_offset_selection(update)

async def show_offset_selection(update):
    kb = [
        [InlineKeyboardButton("⏰ Exact Time", callback_data="offset_0")],
        [InlineKeyboardButton("⏱️ 5 Mins Before", callback_data="offset_5"),
         InlineKeyboardButton("⏱️ 10 Mins Before", callback_data="offset_10")],
        [InlineKeyboardButton("⏱️ 15 Mins Before", callback_data="offset_15"),
         InlineKeyboardButton("✏️ Custom", callback_data="offset_custom")]
    ]
    
    msg_text = (
        "⌛ <b>NOTIFICATION TIMING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>When should I notify before class?</i> 👇"
    )
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(
            msg_text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            msg_text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    return SELECT_OFFSET

async def wizard_offset(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "offset_custom":
        await query.edit_message_text(
            "⏱️ <b>CUSTOM TIMING</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter minutes before class (1-60):</i>\n"
            "<i>Example:</i> <code>20</code>",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_OFFSET_INPUT
    
    context.user_data['sch_offset'] = int(query.data.split("_")[1])
    kb = [
        [InlineKeyboardButton("✨ AI Auto-Write", callback_data="msg_ai")],
        [InlineKeyboardButton("✍️ Manual Message", callback_data="msg_manual")]
    ]
    await query.edit_message_text(
        "📝 <b>MESSAGE STYLE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>How should I announce the class?</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return MSG_TYPE_CHOICE

async def wizard_custom_offset(update, context):
    """Handle custom offset input"""
    try:
        mins = int(update.message.text.strip())
        if mins < 1 or mins > 60:
            await update.message.reply_text(
                "❌ <b>INVALID!</b> Enter 1-60 minutes.",
                parse_mode=ParseMode.HTML
            )
            return CUSTOM_OFFSET_INPUT
        
        context.user_data['sch_offset'] = mins
        kb = [
            [InlineKeyboardButton("✨ AI Auto-Write", callback_data="msg_ai")],
            [InlineKeyboardButton("✍️ Manual Message", callback_data="msg_manual")]
        ]
        await update.message.reply_text(
            "📝 <b>MESSAGE STYLE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ <i>Notification:</i> <b>{mins} mins before</b>\n\n"
            "<i>How should I announce the class?</i> 👇",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return MSG_TYPE_CHOICE
    except ValueError:
        await update.message.reply_text(
            "❌ <b>INVALID!</b> Enter a number (1-60).",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_OFFSET_INPUT

async def wizard_msg_choice(update, context):
    if update.callback_query.data == "msg_manual":
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "✍️ <b>CUSTOM MESSAGE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Type your announcement below:</i>",
            parse_mode=ParseMode.HTML
        )
        return INPUT_MANUAL_MSG
    else:
        context.user_data['sch_manual_msg'] = None
        return await wizard_finalize(update.callback_query, context)

async def wizard_manual_msg(update, context):
    context.user_data['sch_manual_msg'] = update.message.text
    return await wizard_finalize(update, context)

async def wizard_finalize(update_obj, context):
    d = context.user_data
    batch, sub, days = d['sch_batch'], d['sch_sub'], d['sch_days']
    start_dt, end_dt = d['start_dt'], d['end_dt']
    t_str = d['sch_time']
    try: h, m = map(int, t_str.split(':'))
    except: return ConversationHandler.END

    day_map = {"Mon":0, "Tue":1, "Wed":2, "Thu":3, "Fri":4, "Sat":5, "Sun":6}
    target_weekdays = [day_map[day] for day in days]
    dates = []
    
    if end_dt:
        curr = start_dt
        while curr <= end_dt:
            if curr.weekday() in target_weekdays: dates.append(curr)
            curr += timedelta(days=1)
    else:
        for wd in target_weekdays:
            curr = start_dt
            delta = wd - curr.weekday()
            if delta < 0: delta += 7
            dates.append(curr + timedelta(days=delta))

    count = 0
    gid = DB["config"]["group_id"]
    if not gid: return ConversationHandler.END

    for dt in dates:
        run_dt = dt.replace(hour=h, minute=m, second=0)
        notify_dt = run_dt - timedelta(minutes=d['sch_offset'])
        job_id = f"{batch}_{int(time.time())}_{count}"
        job_data = {
            "batch": batch, "subject": sub, "time_display": t_str, 
            "link": d['sch_link'], "manual_msg": d.get('sch_manual_msg'),
            "msg_type": "MANUAL" if d.get('sch_manual_msg') else "AI",
            "message_thread_id": d.get('sch_topic_id')
        }
        context.job_queue.run_once(send_alert_job, notify_dt, chat_id=gid, name=job_id, data=job_data)
        add_job_to_db(job_id, notify_dt.timestamp(), gid, job_data)
        count += 1
    
    topic_name = DB.get("topics", {}).get(str(d.get('sch_topic_id')), "General") if d.get('sch_topic_id') else "General"
    
    msg = (
        f"🎉 <b>SUCCESS!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ <b>{count} class(es) scheduled!</b>\n\n"
        f"📌 <i>Subject:</i> <b>{sub}</b>\n"
        f"🎯 <i>Batch:</i> <b>{batch}</b>\n"
        f"💬 <i>Topic:</i> <b>{topic_name}</b>\n"
        f"⏰ <i>Time:</i> <b>{t_str}</b>\n\n"
        f"<i>Notifications will be sent automatically!</i> 🚀"
    )
    if isinstance(update_obj, Update): await update_obj.message.reply_text(msg, parse_mode=ParseMode.HTML)
    else: await update_obj.message.reply_text(msg, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ==============================================================================
# ➕ 10. ADD SUBJECT & EDIT
# ==============================================================================
async def start_add_sub(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    kb = [
        [InlineKeyboardButton("🟦 CSDA", callback_data="sub_CSDA"), 
         InlineKeyboardButton("🟧 AICS", callback_data="sub_AICS")]
    ]
    await update.message.reply_text(
        "➕ <b>ADD NEW SUBJECT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select the batch:</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return SELECT_BATCH

async def save_batch_for_sub(update, context):
    context.user_data['temp_batch'] = update.callback_query.data.split("_")[1]
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📝 <b>SUBJECT NAME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Type the subject name below:</i>",
        parse_mode=ParseMode.HTML
    )
    return NEW_SUBJECT_INPUT

async def save_new_sub(update, context):
    b = context.user_data['temp_batch']
    s = update.message.text
    if s not in DB["subjects"][b]:
        DB["subjects"][b].append(s)
        save_db()
    await update.message.reply_text(
        f"✅ <b>SUBJECT ADDED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📖 <b>{s}</b>\n"
        f"🎯 <i>Batch:</i> <b>{b}</b>\n\n"
        f"<i>You can now schedule classes for this subject!</i> 🚀",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def start_edit(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    jobs = context.job_queue.jobs()
    
    # Filter valid class jobs
    class_jobs = [j for j in jobs if j.name and isinstance(j.data, dict) and 'batch' in j.data and len(f"edit_{j.name}") <= 64]
    
    if not class_jobs:
        await update.message.reply_text(
            "📭 <b>NO CLASSES FOUND!</b>\n\n"
            "<i>Schedule some classes first.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    # Sort by time
    class_jobs.sort(key=lambda j: j.next_t)
    
    # Pagination - max 8 per page
    PAGE_SIZE = 8
    page = context.user_data.get('edit_page', 0)
    total_pages = (len(class_jobs) + PAGE_SIZE - 1) // PAGE_SIZE
    
    start_idx = page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, len(class_jobs))
    page_jobs = class_jobs[start_idx:end_idx]
    
    rows = []
    for job in page_jobs:
        d = job.data
        try:
            time_str = job.next_t.strftime("%d %b %H:%M")
        except:
            time_str = d.get('time_display', '')
        rows.append([InlineKeyboardButton(f"📖 {d['batch']} {d['subject'][:15]} ({time_str})", callback_data=f"edit_{job.name}")])
    
    # Add navigation buttons if needed
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="edit_page_prev"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data="edit_page_next"))
    if nav_row:
        rows.append(nav_row)
    
    await update.message.reply_text(
        f"✏️ <b>EDIT CLASS</b> ({len(class_jobs)} total)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Page {page + 1}/{total_pages}</i>\n\n"
        "<i>Select a class to modify:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SELECT_JOB

async def edit_select_job(update, context):
    query = update.callback_query
    await query.answer()
    
    # Handle pagination
    if query.data in ["edit_page_prev", "edit_page_next"]:
        current_page = context.user_data.get('edit_page', 0)
        if query.data == "edit_page_prev":
            context.user_data['edit_page'] = max(0, current_page - 1)
        else:
            context.user_data['edit_page'] = current_page + 1
        
        # Rebuild the class list for new page
        jobs = context.job_queue.jobs()
        class_jobs = [j for j in jobs if j.name and isinstance(j.data, dict) and 'batch' in j.data and len(f"edit_{j.name}") <= 64]
        class_jobs.sort(key=lambda j: j.next_t)
        
        PAGE_SIZE = 8
        page = context.user_data['edit_page']
        total_pages = (len(class_jobs) + PAGE_SIZE - 1) // PAGE_SIZE
        
        start_idx = page * PAGE_SIZE
        end_idx = min(start_idx + PAGE_SIZE, len(class_jobs))
        page_jobs = class_jobs[start_idx:end_idx]
        
        rows = []
        for job in page_jobs:
            d = job.data
            try:
                time_str = job.next_t.strftime("%d %b %H:%M")
            except:
                time_str = d.get('time_display', '')
            rows.append([InlineKeyboardButton(f"📖 {d['batch']} {d['subject'][:15]} ({time_str})", callback_data=f"edit_{job.name}")])
        
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="edit_page_prev"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("➡️ Next", callback_data="edit_page_next"))
        if nav_row:
            rows.append(nav_row)
        
        await query.edit_message_text(
            f"✏️ <b>EDIT CLASS</b> ({len(class_jobs)} total)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Page {page + 1}/{total_pages}</i>\n\n"
            "<i>Select a class to modify:</i> 👇",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML
        )
        return EDIT_SELECT_JOB
    
    # Handle class selection
    context.user_data['edit_job_name'] = query.data.replace("edit_", "")
    context.user_data['edit_page'] = 0  # Reset page for next time
    jobs = context.job_queue.get_jobs_by_name(context.user_data['edit_job_name'])
    if not jobs: return ConversationHandler.END
    job_data = jobs[0].data
    context.user_data['old_job_data'] = job_data
    context.user_data['old_next_t'] = jobs[0].next_t
    
    kb = [
        [InlineKeyboardButton("⏰ Change Time", callback_data="field_time")],
        [InlineKeyboardButton("📅 Change Date", callback_data="field_date")],
        [InlineKeyboardButton("🔗 Change Link", callback_data="field_link")]
    ]
    
    # Add Edit Message option if it's a custom message or manual alert
    if job_data.get('manual_msg'):
        kb.append([InlineKeyboardButton("📝 Edit Text", callback_data="field_msg")])
        
    # Add Edit Topic option if applicable
    kb.append([InlineKeyboardButton("💬 Edit Topic", callback_data="field_topic")])
    
    await query.edit_message_text(
        "🔧 <b>WHAT TO EDIT?</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select what you want to change:</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return EDIT_CHOOSE_FIELD

async def edit_choose_field(update, context):
    query = update.callback_query
    await query.answer()
    
    field = query.data.replace("field_", "")
    context.user_data['edit_field'] = field
    
    prompts = {
        "time": "⏰ <b>NEW TIME:</b>\n<i>Enter in HH:MM format (24h)</i>",
        "date": "📅 <b>NEW DATE:</b>\n<i>Enter YYYY-MM-DD</i>",
        "link": "🔗 <b>NEW LINK:</b>\n<i>Enter the new meeting link</i>",
        "msg": "📝 <b>NEW MESSAGE TEXT:</b>\n<i>Enter the new content (HTML supported)</i>",
        "topic": "💬 <b>NEW TOPIC ID:</b>\n<i>Enter Topic ID (0 for General)</i>"
    }
    
    await query.edit_message_text(
        prompts.get(field, "❓ Enter new value:"),
        parse_mode=ParseMode.HTML
    )
    return EDIT_NEW_VALUE

async def edit_save(update, context):
    """Store new value and show scope selection"""
    new_val = update.message.text
    field = context.user_data['edit_field']
    
    # Validate input first
    if field == "time":
        try:
            h, m = map(int, new_val.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except:
            await update.message.reply_text("❌ <b>INVALID TIME!</b> Use HH:MM (00:00-23:59)", parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE
            
    elif field == "date":
        try:
            datetime.strptime(new_val, "%Y-%m-%d")
        except:
            await update.message.reply_text("❌ <b>INVALID DATE!</b> Use YYYY-MM-DD", parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE
    
    elif field == "topic":
        if not (new_val.isdigit() or new_val == "0"):
            await update.message.reply_text("❌ <b>INVALID TOPIC ID!</b> Numbers only", parse_mode=ParseMode.HTML)
            return EDIT_NEW_VALUE
    
    # Store the new value
    context.user_data['edit_new_value'] = new_val
    
    # Get job info for scope display
    original_name = context.user_data['edit_job_name']
    jobs = context.job_queue.get_jobs_by_name(original_name)
    if not jobs:
        await update.message.reply_text("❌ <b>JOB NOT FOUND!</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    
    job = jobs[0]
    subject = job.data.get('subject', 'Unknown')
    batch = job.data.get('batch', 'Unknown')
    day_name = job.next_t.strftime('%A')
    
    # Count matching jobs for display
    all_jobs = context.job_queue.jobs()
    same_subject_count = len([j for j in all_jobs if j.data.get('subject') == subject and j.data.get('batch') == batch])
    same_day_count = len([j for j in all_jobs if j.data.get('subject') == subject and j.data.get('batch') == batch and j.next_t.strftime('%A') == day_name])
    
    kb = [
        [InlineKeyboardButton(f"🎯 This Class Only", callback_data="scope_single")],
        [InlineKeyboardButton(f"📅 All {subject} on {day_name} ({same_day_count})", callback_data="scope_day")],
        [InlineKeyboardButton(f"📚 All {subject} ({same_subject_count})", callback_data="scope_subject")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="scope_cancel")]
    ]
    
    await update.message.reply_text(
        f"✅ <b>APPLY TO WHICH CLASSES?</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📖 Subject: <b>{subject}</b>\n"
        f"🎯 Batch: <b>{batch}</b>\n"
        f"🔧 Change: <b>{field.upper()}</b> → <code>{new_val[:30]}</code>\n\n"
        f"<i>Select scope:</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SELECT_SCOPE

async def edit_scope_handler(update, context):
    """Handle scope selection and apply edits"""
    query = update.callback_query
    await query.answer()
    scope = query.data.replace("scope_", "")
    
    if scope == "cancel":
        await query.edit_message_text("❌ Edit cancelled.")
        return ConversationHandler.END
    
    # Get stored edit data
    field = context.user_data['edit_field']
    new_val = context.user_data['edit_new_value']
    original_name = context.user_data['edit_job_name']
    
    # Get original job for reference
    jobs = context.job_queue.get_jobs_by_name(original_name)
    if not jobs:
        await query.edit_message_text("❌ <b>JOB NOT FOUND!</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    
    ref_job = jobs[0]
    subject = ref_job.data.get('subject')
    batch = ref_job.data.get('batch')
    ref_day = ref_job.next_t.strftime('%A')
    
    # Find jobs to edit based on scope
    all_jobs = context.job_queue.jobs()
    jobs_to_edit = []
    
    if scope == "single":
        jobs_to_edit = [ref_job]
    elif scope == "day":
        jobs_to_edit = [j for j in all_jobs if j.data.get('subject') == subject and j.data.get('batch') == batch and j.next_t.strftime('%A') == ref_day]
    elif scope == "subject":
        jobs_to_edit = [j for j in all_jobs if j.data.get('subject') == subject and j.data.get('batch') == batch]
    
    if not jobs_to_edit:
        await query.edit_message_text("❌ <b>NO MATCHING JOBS!</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    
    # Apply edits to all matching jobs
    edited_count = 0
    for job in jobs_to_edit:
        try:
            data = job.data.copy()
            next_t = job.next_t
            chat_id = job.chat_id
            old_name = job.name
            
            # Apply the edit
            if field == "time":
                h, m = map(int, new_val.split(":"))
                next_t = next_t.replace(hour=h, minute=m)
                data['time_display'] = new_val
            elif field == "date":
                d = datetime.strptime(new_val, "%Y-%m-%d")
                next_t = next_t.replace(year=d.year, month=d.month, day=d.day)
            elif field == "link":
                data['link'] = new_val
            elif field == "msg":
                data['manual_msg'] = new_val
            elif field == "topic":
                tid = int(new_val) if new_val != "0" else None
                data['message_thread_id'] = tid
            
            # Reschedule
            job.schedule_removal()
            new_job_id = f"{data['batch']}_{int(time.time())}_{edited_count}"
            context.job_queue.run_once(send_alert_job, next_t, chat_id=chat_id, name=new_job_id, data=data)
            
            # Update DB
            remove_job_from_db(old_name)
            add_job_to_db(new_job_id, next_t.timestamp(), chat_id, data)
            edited_count += 1
            
        except Exception as e:
            logger.error(f"Failed to edit job {job.name}: {e}")
            continue
    
    scope_text = {"single": "this class", "day": f"all {subject} on {ref_day}", "subject": f"all {subject}"}
    await query.edit_message_text(
        f"✅ <b>BULK EDIT COMPLETE!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>{edited_count}</b> classes updated\n"
        f"🔧 <b>{field.upper()}</b> → <code>{new_val[:30]}</code>\n"
        f"📌 Applied to: <i>{scope_text.get(scope, scope)}</i>",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# ==============================================================================
# 📨 11. JOB EXECUTION
# ==============================================================================


async def send_alert_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Bulletproof scheduled class alert with multi-layer fallback:
    1. Try sending with topic ID + HTML
    2. If topic fails → Send to General 
    3. If HTML fails → Strip and send plain text
    4. If all fails → Retry with exponential backoff
    """
    import random
    job = context.job
    data = job.data
    max_retries = 4  # Increased retries
    retry_count = data.get('retry_count', 0)
    
    try:
        link = data.get('link') if data.get('link') != 'None' else "https://t.me/"

        # Generate message content
        if data.get('msg_type') == "AI":
            text = await generate_hype_message(data['batch'], data['subject'], data['time_display'], link)
            if not text: 
                text = f"<b>🔔 {data['batch']} CLASS: {data['subject']}</b>\n⏰ {data['time_display']}"
        else:
            text = f"{data.get('manual_msg')}\n⏰ {data['time_display']}"
        
        # Sanitize any forbidden HTML tags
        text = sanitize_html(text)
        
        msg = f"{text}\n\n👇 <i>Mark attendance:</i>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🙋 I am Present", callback_data=f"att_{job.name}")]])
        
        sent = False
        
        # FALLBACK LEVEL 1: Try with topic + HTML
        try:
            await context.bot.send_message(
                job.chat_id, 
                text=msg, 
                parse_mode=ParseMode.HTML, 
                reply_markup=kb, 
                disable_web_page_preview=True,
                message_thread_id=data.get('message_thread_id')
            )
            sent = True
        except Exception as e1:
            err1 = str(e1)
            logger.warning(f"Fallback 1 failed for {job.name}: {err1}")
            
            # FALLBACK LEVEL 2: Send to General (no topic)
            if "thread" in err1.lower() or "topic" in err1.lower():
                try:
                    await context.bot.send_message(
                        job.chat_id, 
                        text=f"⚠️ <i>Topic unavailable</i>\n\n{msg}", 
                        parse_mode=ParseMode.HTML, 
                        reply_markup=kb, 
                        disable_web_page_preview=True,
                        message_thread_id=None
                    )
                    sent = True
                except Exception as e2:
                    err1 = str(e2)  # Update error for next fallback
                    logger.warning(f"Fallback 2 failed for {job.name}: {e2}")
            
            # FALLBACK LEVEL 3: Strip HTML, send plain text
            if not sent and ("parse" in err1.lower() or "entity" in err1.lower() or "tag" in err1.lower()):
                try:
                    clean_msg = re.sub(r'<[^>]+>', '', msg)
                    await context.bot.send_message(
                        job.chat_id, 
                        text=clean_msg, 
                        reply_markup=kb, 
                        disable_web_page_preview=True,
                        message_thread_id=None
                    )
                    sent = True
                    logger.info(f"Sent {job.name} as plain text (HTML stripped)")
                except Exception as e3:
                    logger.warning(f"Fallback 3 failed for {job.name}: {e3}")
        
        if sent:
            remove_job_from_db(job.name)
            logger.info(f"✅ Alert sent: {job.name}")
        else:
            raise Exception("All fallback attempts failed")
        
    except Exception as e:
        logger.error(f"❌ Failed to send alert (attempt {retry_count + 1}): {e}")
        
        if retry_count < max_retries:
            # Retry with jitter (1-2 minutes)
            new_data = data.copy()

            new_data['retry_count'] = retry_count + 1
            
            # Add jitter to prevent thundering herd (60-90 seconds)
            jitter = random.randint(0, 30)
            retry_time = datetime.now(IST) + timedelta(seconds=60 + jitter)
            context.job_queue.run_once(
                send_alert_job, 
                retry_time, 
                chat_id=job.chat_id, 
                name=f"{job.name}_retry{retry_count + 1}", 
                data=new_data
            )
            logger.info(f"🔄 Retry scheduled for {job.name} in ~1 minute")
        else:
            # Final fallback - log for admin review
            logger.error(f"❌ CRITICAL: Max retries ({max_retries}) reached for {job.name}. Alert LOST.")
            # Could add notification to admin here in future

async def restore_jobs(application: Application):
    count = 0
    now_ts = datetime.now(IST).timestamp()
    jobs_to_restore = DB.get("active_jobs", [])[:]
    
    for job_entry in jobs_to_restore:
        try:
            if job_entry["timestamp"] < now_ts:
                remove_job_from_db(job_entry["name"])
                continue
            run_dt = datetime.fromtimestamp(job_entry["timestamp"], IST)
            application.job_queue.run_once(send_alert_job, run_dt, chat_id=job_entry["chat_id"], name=job_entry["name"], data=job_entry["data"])
            count += 1
        except Exception: continue
    if count > 0: logger.info(f"♻️ RESTORED {count} JOBS")

# ==============================================================================
# 📝 CUSTOM MESSAGE SCHEDULER
# ==============================================================================
async def start_custom_msg(update, context):
    """Start custom message scheduler"""
    try:
        if not await require_private_admin(update, context): return ConversationHandler.END
        if not DB["config"]["group_id"]:
            await update.message.reply_text(
                "⛔ <b>NO GROUP LINKED!</b>\n\n"
                "<i>Add me to a group first.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        kb = [
            [InlineKeyboardButton("🟦 CSDA", callback_data="cmsg_CSDA"),
             InlineKeyboardButton("🟧 AICS", callback_data="cmsg_AICS")],
            [InlineKeyboardButton("📢 Both Batches", callback_data="cmsg_BOTH")]
        ]
        await update.message.reply_text(
            "📝 <b>CUSTOM MESSAGE SCHEDULER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Schedule a custom announcement!</i>\n\n"
            "👇 <b>Select target batch:</b>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_BATCH
    except Exception as e:
        logger.error(f"Error in start_custom_msg: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def cmsg_batch_selected(update, context):
    """Handle batch selection"""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data['cmsg_batch'] = query.data.replace("cmsg_", "")
        context.user_data['cmsg_days'] = []  # Initialize empty days list
        
        await query.edit_message_text(
            "📅 <b>SELECT DAYS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Tap to toggle days, then hit</i> <b>DONE</b> 🚀",
            reply_markup=days_keyboard([]),
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_DAYS
    except Exception as e:
        logger.error(f"Error in cmsg_batch_selected: {e}")
        return ConversationHandler.END

async def cmsg_toggle_days(update, context):
    """Handle day toggling for custom message"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "days_done":
        if not context.user_data.get('cmsg_days'): 
            await query.answer("⚠️ Please select at least one day!", show_alert=True)
            return CUSTOM_MSG_DAYS
            
        await query.edit_message_text(
            "📅 <b>START DATE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in format:</i> <code>DD-MM-YYYY</code>\n"
            "<i>Or type:</i> <code>Today</code>",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_START
    
    # Toggle individual day
    if query.data.startswith("toggle_"):
        day = query.data.split("_")[1]
        days = context.user_data.get('cmsg_days', [])
        
        if day in days: 
            days.remove(day)
        else: 
            days.append(day)
            
        context.user_data['cmsg_days'] = days
        await query.edit_message_reply_markup(days_keyboard(days))
        
    return CUSTOM_MSG_DAYS

async def cmsg_time_input(update, context):
    """Handle time input (Moved to after End Date)"""
    try:
        text = update.message.text.strip()
        try:
            h, m = map(int, text.split(':'))
            if h < 0 or h > 23 or m < 0 or m > 59:
                raise ValueError()
            context.user_data['cmsg_time'] = text
        except:
            await update.message.reply_text(
                "❌ <b>INVALID TIME!</b>\n\n"
                "<i>Use format:</i> <code>HH:MM</code>",
                parse_mode=ParseMode.HTML
            )
            return CUSTOM_MSG_TIME
        
        await update.message.reply_text(
            "✍️ <b>MESSAGE CONTENT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Type your announcement message:</i>\n\n"
            "💡 <b>Tip:</b> You can use HTML:\n"
            "<code>&lt;b&gt;bold&lt;/b&gt;</code>, <code>&lt;i&gt;italic&lt;/i&gt;</code>\n"
            "<code>&lt;a href='url'&gt;link&lt;/a&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_TEXT
    except Exception as e:
        logger.error(f"Error in cmsg_time_input: {e}")
        return ConversationHandler.END

async def cmsg_start_date(update, context):
    """Handle start date input"""
    try:
        text = update.message.text.strip().lower()
        if text == 'today':
            start_dt = datetime.now(IST).replace(hour=0, minute=0, second=0)
        else:
            try:
                start_dt = datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=IST)
            except:
                await update.message.reply_text(
                    "❌ <b>INVALID FORMAT!</b>\n\n"
                    "<i>Use:</i> <code>DD-MM-YYYY</code>",
                    parse_mode=ParseMode.HTML
                )
                return CUSTOM_MSG_START
        
        context.user_data['cmsg_start'] = start_dt
        await update.message.reply_text(
            "📅 <b>END DATE (Optional)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter in format:</i> <code>DD-MM-YYYY</code>\n"
            "<i>Or type:</i> <code>None</code> <i>for one-time message</i>",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_END
    except Exception as e:
        logger.error(f"Error in cmsg_start_date: {e}")
        return ConversationHandler.END

async def cmsg_end_date(update, context):
    """Handle end date input"""
    try:
        text = update.message.text.strip().lower()
        if text == 'none':
            context.user_data['cmsg_end'] = None
        else:
            try:
                end_dt = datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=IST)
                context.user_data['cmsg_end'] = end_dt
            except:
                await update.message.reply_text(
                    "❌ <b>INVALID FORMAT!</b>\n\n"
                    "<i>Use:</i> <code>DD-MM-YYYY</code> or <code>None</code>",
                    parse_mode=ParseMode.HTML
                )
                return CUSTOM_MSG_END
        
        await update.message.reply_text(
            "⏰ <b>SCHEDULE TIME</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter time in 24h format:</i>\n"
            "<code>HH:MM</code> (e.g., <code>14:30</code>)",
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_TIME
    except Exception as e:
        logger.error(f"Error in cmsg_end_date: {e}")
        return ConversationHandler.END

async def cmsg_text_input(update, context):
    """Handle message text input"""
    try:
        context.user_data['cmsg_text'] = update.message.text
        
        kb = [
            [InlineKeyboardButton("⏭️ Skip (No Link)", callback_data="cmsg_link_skip")]
        ]
        await update.message.reply_text(
            "🔗 <b>ADD LINK (Optional)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter a link to include:</i>\n"
            "<i>Or tap Skip to continue without link</i>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
        return CUSTOM_MSG_LINK
    except Exception as e:
        logger.error(f"Error in cmsg_text_input: {e}")
        return ConversationHandler.END

async def cmsg_link_input(update, context):
    """Handle link input"""
    try:
        if update.callback_query:
            await update.callback_query.answer()
            context.user_data['cmsg_link'] = None
        else:
            context.user_data['cmsg_link'] = update.message.text.strip()
        
        # Check for topics
        topics = DB.get("topics", {})
        if topics:
            kb = []
            row = []
            for tid, name in topics.items():
                row.append(InlineKeyboardButton(name, callback_data=f"ctopic_{tid}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("📢 General (No Topic)", callback_data="ctopic_general")])
            
            msg_obj = update.callback_query.message if update.callback_query else update.message
            await msg_obj.reply_text(
                "💬 <b>SELECT TOPIC</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>Where should this announcement go?</i> 👇",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return SELECT_TOPIC
        else:
            context.user_data['cmsg_topic_id'] = None
            return await cmsg_finalize(update, context)
            
    except Exception as e:
        logger.error(f"Error in cmsg_link_input: {e}")
        return ConversationHandler.END

async def cmsg_topic_selection(update, context):
    """Handle topic selection for custom message"""
    try:
        query = update.callback_query
        await query.answer()
        data = query.data
        
        if data == "ctopic_general":
            context.user_data['cmsg_topic_id'] = None
        else:
            tid = data.replace("ctopic_", "")
            context.user_data['cmsg_topic_id'] = int(tid)
            
        return await cmsg_finalize(update, context)
    except Exception as e:
        logger.error(f"Error in cmsg_topic_selection: {e}")
        return ConversationHandler.END

async def cmsg_finalize(update, context):
    """Finalize and schedule custom message"""
    try:
        d = context.user_data
        batch = d['cmsg_batch']
        time_str = d['cmsg_time']
        start_dt = d['cmsg_start']
        end_dt = d.get('cmsg_end')
        msg_text = d['cmsg_text']
        link = d.get('cmsg_link')
        topic_id = d.get('cmsg_topic_id')
        
        h, m = map(int, time_str.split(':'))
        gid = DB["config"]["group_id"]
        
        # Determine days to schedule
        # Determine days to schedule
        selected_days = d.get('cmsg_days', [])
        day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        target_weekdays = [day_map[day] for day in selected_days]
        days = []

        if end_dt:
            current = start_dt
            while current <= end_dt:
                if current.weekday() in target_weekdays:
                    days.append(current)
                current += timedelta(days=1)
        else:
            # If no end date, find the next occurrence for EACH selected day
            for target_wd in target_weekdays:
                current = start_dt
                # Calculate days until next target weekday
                days_ahead = target_wd - current.weekday()
                if days_ahead < 0:
                    days_ahead += 7
                next_date = current + timedelta(days=days_ahead)
                days.append(next_date)
            
            # Sort days just in case
            days.sort()
        
        count = 0
        for day in days:
            run_dt = day.replace(hour=h, minute=m, second=0)
            if run_dt < datetime.now(IST):
                continue
            
            job_id = f"cmsg_{batch}_{int(time.time())}_{count}"
            job_data = {
                "batch": batch,
                "subject": "Custom",
                "time_display": time_str,
                "link": link or "None",
                "msg_type": "custom",
                "manual_msg": msg_text,
                "message_thread_id": topic_id
            }
            
            context.job_queue.run_once(send_custom_msg_job, run_dt, chat_id=gid, name=job_id, data=job_data)
            add_job_to_db(job_id, run_dt.timestamp(), gid, job_data)
            count += 1
        
        save_db()
        
        msg_obj = update.callback_query if update.callback_query else update
        reply_func = msg_obj.message.reply_text if hasattr(msg_obj, 'message') else msg_obj.reply_text
        
        topic_name = DB.get("topics", {}).get(str(topic_id), "General") if topic_id else "General"
        
        await reply_func(
            f"✅ <b>CUSTOM MESSAGE SCHEDULED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📢 <b>Batch:</b> {batch}\n"
            f"💬 <b>Topic:</b> {topic_name}\n"
            f"⏰ <b>Time:</b> {time_str}\n"
            f"📅 <b>Messages:</b> {count} scheduled\n\n"
            f"<i>Your announcement will be sent!</i> 🚀",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in cmsg_finalize: {e}")
        if update.callback_query:
            await update.callback_query.message.reply_text("❌ An error occurred.")
        else:
            await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def send_custom_msg_job(context: ContextTypes.DEFAULT_TYPE):
    """Send custom scheduled message"""
    try:
        job = context.job
        data = job.data
        msg = data.get('manual_msg', '')
        link = data.get('link')
        topic_id = data.get('message_thread_id')
        
        if link and link != "None":
            msg += f"\n\n🔗 <a href='{link}'>Click Here</a>"
        
        await context.bot.send_message(
            job.chat_id, 
            text=msg, 
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
            message_thread_id=topic_id
        )
        remove_job_from_db(job.name)
        logger.info(f"✅ Custom message sent: {job.name}")
    except Exception as e:
        logger.error(f"❌ Failed to send custom message: {e}")

# ==============================================================================
# 💬 FORUM TOPIC MANAGEMENT
# ==============================================================================

async def register_topic_command(update, context):
    """Command to register current topic: /topic <name>"""
    try:
        user = update.effective_user
        if not is_admin(user.username): return
        
        chat = update.effective_chat
        if not chat.is_forum:
            await update.message.reply_text("⛔ This command is only for Supergroups with Topics enabled.")
            return

        thread_id = update.message.message_thread_id
        if not thread_id:
            await update.message.reply_text("⛔ Use this command INSIDE a topic.")
            return

        topic_name = " ".join(context.args)
        if not topic_name:
            # Try to get from reply or just default
            topic_name = f"Topic {thread_id}"
            await update.message.reply_text("⚠️ Please provide a name: `/topic Class Updates`")
            return

        if "topics" not in DB: DB["topics"] = {}
        DB["topics"][str(thread_id)] = topic_name
        save_db()

        await update.message.reply_text(
            f"✅ <b>TOPIC REGISTERED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>ID:</b> {thread_id}\n"
            f"🏷️ <b>Name:</b> {topic_name}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in register_topic: {e}")

async def auto_register_topic(update, context):
    """Auto-register new topics created in the group"""
    try:
        if not update.message or not update.message.forum_topic_created: return
        
        topic = update.message.forum_topic_created
        thread_id = update.message.message_thread_id
        name = topic.name
        
        if "topics" not in DB: DB["topics"] = {}
        DB["topics"][str(thread_id)] = name
        save_db()
        
        # Notify about auto-registration
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            message_thread_id=thread_id,
            text=f"✅ <b>TOPIC DETECTED!</b>\nAdded to Titan database.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error in auto_register_topic: {e}")

async def admin_command(update, context):
    """Show admin tools keyboard"""
    if not await require_private_admin(update, context): return
    
    kb = [
        [KeyboardButton("➕ Add Subject"), KeyboardButton("🗑️ Delete Class")],
        [KeyboardButton("📤 Export Data"), KeyboardButton("📥 Import Data")],
        [KeyboardButton("👥 Manage Admins"), KeyboardButton("💬 Manage Topics")],
        [KeyboardButton("🌙 Night Schedule"), KeyboardButton("☁️ Force Save")],
        [KeyboardButton("🔄 Reset System"), KeyboardButton("🔙 Back to Main")]
    ]
    await update.message.reply_text(
        "🛠️ <b>ADMIN TOOLS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select an action:</i> 👇",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode=ParseMode.HTML
    )

async def manage_topics_handler(update, context):
    """Show Manage Topics Menu"""
    if not await require_private_admin(update, context): return
    
    kb = [
        [KeyboardButton("➕ Add Topic Manual"), KeyboardButton("🗑️ Remove Topic")],
        [KeyboardButton("📋 List Topics"), KeyboardButton("🔙 Back to Main")]
    ]
    await update.message.reply_text(
        "💬 <b>MANAGE TOPICS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Manage your forum topics for scheduling.</i>\n\n"
        "💡 <b>Tip:</b> Go to a topic and type <code>/topic Name</code> to add it quickly!",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode=ParseMode.HTML
    )

async def view_topics(update, context):
    """List all registered topics"""
    if not await require_private_admin(update, context): return
    
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text("📭 <b>NO TOPICS FOUND.</b>")
        return

    msg = "💬 <b>REGISTERED TOPICS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for tid, name in topics.items():
        msg += f"🏷️ <b>{name}</b> (ID: {tid})\n"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# --- Add Topic Manual Wizard ---
async def start_add_topic(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    await update.message.reply_text(
        "➕ <b>ADD TOPIC</b>\n"
        "<i>Enter the Topic Name:</i>",
        parse_mode=ParseMode.HTML
    )
    return ADD_TOPIC_NAME

async def save_topic_name(update, context):
    context.user_data['new_topic_name'] = update.message.text
    await update.message.reply_text(
        "🆔 <b>ENTER TOPIC ID</b>\n"
        "<i>Enter the Message Thread ID:</i>\n"
        "(You can find this by forwarding a message from the topic to bots like @userinfobot)",
        parse_mode=ParseMode.HTML
    )
    return ADD_TOPIC_ID

async def save_topic_id(update, context):
    try:
        tid = update.message.text.strip()
        if not tid.isdigit():
            await update.message.reply_text("❌ <b>INVALID ID!</b> Numbers only.")
            return ADD_TOPIC_ID
            
        name = context.user_data['new_topic_name']
        if "topics" not in DB: DB["topics"] = {}
        DB["topics"][str(tid)] = name
        save_db()
        
        await update.message.reply_text(
            f"✅ <b>TOPIC ADDED!</b>\nName: {name}\nID: {tid}",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in save_topic_id: {e}")
        return ConversationHandler.END

# --- Remove Topic Wizard ---
async def start_remove_topic(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text("📭 <b>NO TOPICS TO REMOVE.</b>")
        return ConversationHandler.END
        
    msg = "🗑️ <b>REMOVE TOPIC</b>\n<i>Enter the Topic ID to remove:</i>\n\n"
    for tid, name in topics.items():
        msg += f"• {name} (ID: <code>{tid}</code>)\n"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    return REMOVE_TOPIC_INPUT

async def remove_topic_save(update, context):
    tid = update.message.text.strip()
    topics = DB.get("topics", {})
    
    if tid in topics:
        name = topics[tid]
        del DB["topics"][tid]
        save_db()
        await update.message.reply_text(f"✅ <b>REMOVED:</b> {name}", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ <b>ID NOT FOUND!</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# --- Edit Topic Command ---
async def start_edit_topic(update, context):
    """Start edit topic wizard - /edittopic"""
    if not await require_private_admin(update, context): return ConversationHandler.END
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text(
            "📭 <b>NO TOPICS TO EDIT.</b>\n\n"
            "<i>Add topics first using /topic in a forum thread.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    
    rows = []
    for tid, name in topics.items():
        rows.append([InlineKeyboardButton(f"🏷️ {name}", callback_data=f"edtopic_{tid}")])
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="edtopic_cancel")])
    
    await update.message.reply_text(
        "✏️ <b>EDIT TOPIC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select a topic to rename:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_TOPIC_SELECT

async def edit_topic_select(update, context):
    """Handle topic selection for editing"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "edtopic_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END
    
    tid = query.data.replace("edtopic_", "")
    context.user_data['edit_topic_id'] = tid
    context.user_data['edit_topic_old_name'] = DB.get("topics", {}).get(tid, "Unknown")
    
    await query.edit_message_text(
        f"✏️ <b>RENAME TOPIC</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current name: <b>{context.user_data['edit_topic_old_name']}</b>\n\n"
        f"<i>Enter the new name:</i>",
        parse_mode=ParseMode.HTML
    )
    return EDIT_TOPIC_NEW_NAME

async def edit_topic_save(update, context):
    """Save the renamed topic"""
    new_name = update.message.text.strip()
    tid = context.user_data['edit_topic_id']
    old_name = context.user_data['edit_topic_old_name']
    
    if "topics" not in DB:
        DB["topics"] = {}
    
    DB["topics"][tid] = new_name
    save_db()
    
    await update.message.reply_text(
        f"✅ <b>TOPIC RENAMED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔄 <b>{old_name}</b> ➡️ <b>{new_name}</b>",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# --- Delete Topic Command ---
async def start_delete_topic(update, context):
    """Start delete topic - /deletetopic"""
    if not await require_private_admin(update, context): return ConversationHandler.END
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text("📭 <b>NO TOPICS TO DELETE.</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    
    rows = []
    for tid, name in topics.items():
        rows.append([InlineKeyboardButton(f"🗑️ {name}", callback_data=f"deltopic_{tid}")])
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="deltopic_cancel")])
    
    await update.message.reply_text(
        "🗑️ <b>DELETE TOPIC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select a topic to delete:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return DELETE_TOPIC_CONFIRM

async def delete_topic_confirm(update, context):
    """Handle topic deletion"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "deltopic_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END
    
    tid = query.data.replace("deltopic_", "")
    topics = DB.get("topics", {})
    
    if tid in topics:
        name = topics[tid]
        del DB["topics"][tid]
        save_db()
        await query.edit_message_text(
            f"✅ <b>DELETED:</b> {name}\n\n"
            f"<i>Topic ID {tid} removed.</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text("❌ <b>Topic not found!</b>", parse_mode=ParseMode.HTML)
    
    return ConversationHandler.END

# --- Topics List Command ---
async def topics_command(update, context):
    """List all topics - /topics"""
    if not await require_private_admin(update, context): return
    
    topics = DB.get("topics", {})
    if not topics:
        await update.message.reply_text(
            "📭 <b>NO TOPICS REGISTERED</b>\n\n"
            "<i>Go to a forum topic and type:</i>\n"
            "<code>/topic TopicName</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    msg = "💬 <b>REGISTERED TOPICS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    for tid, name in topics.items():
        msg += f"🏷️ <b>{name}</b>\n    ID: <code>{tid}</code>\n\n"
    
    msg += (
        "━━━━━━━━━━━━━━━━━━\n"
        "📝 <b>Commands:</b>\n"
        "• /edittopic - Rename a topic\n"
        "• /deletetopic - Remove a topic"
    )
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ==============================================================================
# 🌙 NIGHT SCHEDULE (NEXT-DAY SUMMARY)
# ==============================================================================
async def start_night_schedule(update, context):
    """Setup night schedule for next-day class summary"""
    try:
        if not await require_private_admin(update, context): return ConversationHandler.END
        
        current_time = DB.get("config", {}).get("night_schedule_time", None)
        status = f"Currently set to: <b>{current_time}</b>" if current_time else "Not currently set"
        
        await update.message.reply_text(
            "🌙 <b>NIGHT SCHEDULE SETUP</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{status}\n\n"
            "<i>Enter time for daily next-day summary:</i>\n"
            "<code>HH:MM</code> (e.g., <code>21:00</code> for 9 PM)\n\n"
            "<i>Or type</i> <code>off</code> <i>to disable</i>",
            parse_mode=ParseMode.HTML
        )
        return NIGHT_SCHEDULE_TIME
    except Exception as e:
        logger.error(f"Error in start_night_schedule: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

async def save_night_schedule_time(update, context):
    """Save the night schedule time"""
    try:
        text = update.message.text.strip().lower()
        
        if text == "off":
            DB["config"]["night_schedule_time"] = None
            save_db()
            # Remove any existing night schedule job
            for job in context.job_queue.jobs():
                if job.name == "night_summary_job":
                    job.schedule_removal()
            
            await update.message.reply_text(
                "🌙 <b>NIGHT SCHEDULE DISABLED</b>\n\n"
                "<i>No more daily summaries will be sent.</i>",
                parse_mode=ParseMode.HTML
            )
            return ConversationHandler.END
        
        try:
            h, m = map(int, text.split(':'))
            if h < 0 or h > 23 or m < 0 or m > 59:
                raise ValueError()
        except:
            await update.message.reply_text(
                "❌ <b>INVALID TIME!</b>\n\n"
                "<i>Use format:</i> <code>HH:MM</code>",
                parse_mode=ParseMode.HTML
            )
            return NIGHT_SCHEDULE_TIME
        
        DB["config"]["night_schedule_time"] = text
        save_db()
        
        # Schedule the nightly job
        schedule_night_summary(context.application, h, m)
        
        await update.message.reply_text(
            f"🌙 <b>NIGHT SCHEDULE SET!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏰ <b>Time:</b> {text}\n\n"
            f"<i>I'll send a summary of tomorrow's classes every night!</i> 🌟",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in save_night_schedule_time: {e}")
        await update.message.reply_text("❌ An error occurred.")
        return ConversationHandler.END

def schedule_night_summary(app, hour, minute):
    """Schedule the nightly summary job"""
    # Remove existing job if any
    for job in app.job_queue.jobs():
        if job.name == "night_summary_job":
            job.schedule_removal()
    
    # Schedule daily job at the specified time
    target_time = dtime(hour=hour, minute=minute, tzinfo=IST)
    app.job_queue.run_daily(
        send_night_summary,
        time=target_time,
        name="night_summary_job"
    )
    logger.info(f"🌙 Night summary scheduled for {hour:02d}:{minute:02d}")

async def send_night_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send summary of next day's classes"""
    try:
        gid = DB["config"].get("group_id")
        if not gid:
            logger.error("No group ID for night summary")
            return
        
        tomorrow = datetime.now(IST) + timedelta(days=1)
        tomorrow_weekday = tomorrow.weekday()
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        tomorrow_day = day_names[tomorrow_weekday]
        
        # Get jobs scheduled for tomorrow
        jobs = context.job_queue.jobs()
        tomorrow_classes = []
        
        for job in jobs:
            if job.name and isinstance(job.data, dict) and 'batch' in job.data:
                if job.next_t.date() == tomorrow.date():
                    tomorrow_classes.append({
                        'batch': job.data['batch'],
                        'subject': job.data['subject'],
                        'time': job.data['time_display']
                    })
        
        if not tomorrow_classes:
            msg = (
                "🌙 <b>TOMORROW'S SCHEDULE</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>{tomorrow_day}, {tomorrow.strftime('%d %b')}</b>\n\n"
                "🎉 <i>No classes scheduled! Enjoy your day!</i>"
            )
        else:
            msg = (
                "🌙 <b>TOMORROW'S SCHEDULE</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>{tomorrow_day}, {tomorrow.strftime('%d %b')}</b>\n\n"
            )
            for c in sorted(tomorrow_classes, key=lambda x: x['time']):
                msg += f"📖 <b>{c['batch']}</b> • {c['subject']}\n"
                msg += f"     ⏰ {c['time']}\n\n"
            msg += "<i>Get ready for tomorrow! 💪</i>"
        
        await context.bot.send_message(gid, text=msg, parse_mode=ParseMode.HTML)
        logger.info("🌙 Night summary sent")
        
    except Exception as e:
        logger.error(f"Error sending night summary: {e}")

# ==============================================================================
# 📊 12. EXTRAS
# ==============================================================================
async def export_data(update, context):
    """Export complete database backup"""
    if not await require_private_admin(update, context): return
    
    # Ensure all keys exist in export
    export_db = {
        "config": DB.get("config", {"group_id": None, "group_name": "❌ No Group Linked"}),
        "subjects": DB.get("subjects", {"CSDA": [], "AICS": []}),
        "active_jobs": DB.get("active_jobs", []),
        "attendance": DB.get("attendance", {}),
        "feedback": DB.get("feedback", []),
        "system_stats": DB.get("system_stats", {}),
        "schedules": DB.get("schedules", []),
        "admins": DB.get("admins", []),
        "topics": DB.get("topics", {})
    }
    
    f = io.BytesIO(json.dumps(export_db, indent=2).encode())
    f.name = f"vasuki_backup_{datetime.now(IST).strftime('%Y%m%d_%H%M')}.json"
    
    # Count stats
    total_subjects = sum(len(s) for s in export_db['subjects'].values())
    
    await context.bot.send_document(
        update.effective_chat.id,
        document=f,
        caption=(
            "📦 <b>BACKUP EXPORTED!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>Contents:</b>\n"
            f"┣ 📚 Subjects: {total_subjects}\n"
            f"┣ 📅 Scheduled Jobs: {len(export_db['active_jobs'])}\n"
            f"┣ 💬 Topics: {len(export_db['topics'])}\n"
            f"┣ 👥 Admins: {len(export_db['admins'])}\n"
            f"┣ 📊 Attendance Records: {len(export_db['attendance'])}\n"
            f"┗ 💬 Feedback: {len(export_db['feedback'])}\n\n"
            "💾 <i>Import this file to restore all data!</i>"
        ),
        parse_mode=ParseMode.HTML
    )

async def import_request(update, context):
    if not await require_private_admin(update, context): return
    await update.message.reply_text(
        "📥 <b>IMPORT DATA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ <b>WARNING:</b> <i>This will OVERWRITE all current data!</i>\n\n"
        "<i>Upload your</i> <code>.json</code> <i>backup file below:</i>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['wait_import'] = True

async def handle_import_file(update, context):
    """Import database and restore scheduled jobs"""
    if not context.user_data.get('wait_import'): return
    
    try:
        file = await update.message.document.get_file()
        raw = await file.download_as_bytearray()
        imported_data = json.loads(raw.decode())
        
        # Validate imported data
        if not isinstance(imported_data, dict):
            await update.message.reply_text("❌ <b>INVALID FILE!</b>\n\nExpected JSON object.", parse_mode=ParseMode.HTML)
            return
        
        # Clear existing jobs from job queue
        jobs = context.job_queue.jobs()
        cleared = 0
        for job in jobs:
            if job.name and isinstance(job.data, dict):
                job.schedule_removal()
                cleared += 1
        
        # Merge with defaults to ensure all keys exist
        global DB
        DB = {
            "config": imported_data.get("config", {"group_id": None, "group_name": "❌ No Group Linked"}),
            "subjects": imported_data.get("subjects", {"CSDA": [], "AICS": []}),
            "active_jobs": imported_data.get("active_jobs", []),
            "attendance": imported_data.get("attendance", {}),
            "feedback": imported_data.get("feedback", []),
            "system_stats": imported_data.get("system_stats", {"start_time": time.time(), "classes_scheduled": 0, "ai_requests": 0}),
            "schedules": imported_data.get("schedules", []),
            "admins": imported_data.get("admins", []),
            "topics": imported_data.get("topics", {})
        }
        
        # Save to cloud
        save_db()
        
        # Restore jobs from imported data
        restored = 0
        now_ts = datetime.now(IST).timestamp()
        for job_entry in DB.get("active_jobs", []):
            try:
                if job_entry["timestamp"] < now_ts:
                    continue  # Skip expired jobs
                run_dt = datetime.fromtimestamp(job_entry["timestamp"], IST)
                context.job_queue.run_once(
                    send_alert_job, 
                    run_dt, 
                    chat_id=job_entry["chat_id"], 
                    name=job_entry["name"], 
                    data=job_entry["data"]
                )
                restored += 1
            except Exception as e:
                logger.error(f"Failed to restore job: {e}")
                continue
        
        await update.message.reply_text(
            "✅ <b>IMPORT SUCCESSFUL!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 <b>Data Imported:</b>\n"
            f"┣ 📚 Subjects: {sum(len(s) for s in DB['subjects'].values())}\n"
            f"┣ 📅 Schedules Restored: {restored}\n"
            f"┣ 💬 Topics: {len(DB.get('topics', {}))}\n"
            f"┣ 👥 Admins: {len(DB.get('admins', []))}\n"
            f"┗ 🎯 Group: {DB['config'].get('group_name', 'None')}\n\n"
            "✅ <i>All data is now live!</i>",
            parse_mode=ParseMode.HTML
        )
        
    except json.JSONDecodeError:
        await update.message.reply_text("❌ <b>INVALID JSON!</b>\n\nFile is not valid JSON format.", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Import error: {e}")
        await update.message.reply_text(f"❌ <b>IMPORT FAILED!</b>\n\n<code>{str(e)[:100]}</code>", parse_mode=ParseMode.HTML)
    finally:
        context.user_data['wait_import'] = False

async def mark_attendance(update, context):
    query = update.callback_query
    job_id = query.data.replace("att_", "")
    user = query.from_user
    uid = user.username or user.first_name

    if job_id not in DB["attendance"]: DB["attendance"][job_id] = []

    if uid in DB["attendance"][job_id]:
        await query.answer("⚠️ Already marked!", show_alert=True)
    else:
        DB["attendance"][job_id].append(uid)
        save_db()
        await query.answer(f"✅ Present: {uid}")

async def view_schedule_handler(update, context):
    """View schedule with pagination"""
    query = None
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
    if not await require_private_admin(update, context): return
    
    # Determine page number
    page = 0
    if query and query.data.startswith("schedule_page_"):
        page = int(query.data.split("_")[-1])
    
    jobs = context.job_queue.jobs()
    
    # Filter only class jobs
    class_jobs = [j for j in jobs if j.name and isinstance(j.data, dict) and 'batch' in j.data]
    
    if not class_jobs:
        msg = (
            "📭 <b>NO UPCOMING CLASSES!</b>\n\n"
            "<i>Schedule some classes first.</i>"
        )
        if query:
            await query.edit_message_text(msg, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return
    
    # Sort by time
    class_jobs.sort(key=lambda j: j.next_t)
    
    # Pagination Logic
    PAGE_SIZE = 5
    total_pages = (len(class_jobs) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1)) # Bounds check
    
    start_idx = page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, len(class_jobs))
    page_jobs = class_jobs[start_idx:end_idx]
    
    msg = (
        f"📅 <b>UPCOMING CLASSES</b> ({len(class_jobs)} total)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Page {page + 1}/{total_pages}</i>\n\n"
    )
    
    for job in page_jobs:
        d = job.data
        # Format date nicely
        try:
            date_str = job.next_t.strftime("%d %b, %H:%M")
        except:
            date_str = d.get('time_display', 'Unknown')
        msg += f"📖 <b>{d['batch']}</b> • {d['subject']}\n"
        msg += f"     ⏰ <i>{date_str}</i>\n\n"
        
    # Navigation Buttons
    buttons = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"schedule_page_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"schedule_page_{page+1}"))
    
    if nav_row:
        buttons.append(nav_row)
        
    # Send or Edit Message
    if query:
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)

async def prompt_image_upload(update, context):
    if not await require_private_admin(update, context): return
    await update.message.reply_text(
        "📸 <b>AI TIMETABLE SCANNER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧠 <i>Send me a photo of your timetable</i>\n"
        "🤖 <i>I'll automatically schedule all classes!</i>\n\n"
        "✨ <b>Tip:</b> <i>Clearer images = better results!</i>",
        parse_mode=ParseMode.HTML
    )

async def view_attendance_stats(update, context):
    if not await require_private_admin(update, context): return
    keys = list(DB["attendance"].keys())[-10:]
    if not keys:
        await update.message.reply_text(
            "📊 <b>NO ATTENDANCE DATA!</b>\n\n"
            "<i>No classes have been held yet.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    msg = (
        "📊 <b>ATTENDANCE REPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Last 10 classes:</i>\n\n"
    )
    for k in keys:
        try:
            parts = k.split('_')
            count = len(DB['attendance'][k])
            msg += f"📖 <b>{parts[0]}</b>\n"
            msg += f"     👥 <i>{count} students attended</i>\n\n"
        except: continue
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def handle_photo(update, context):
    if not await require_private_admin(update, context): return
    if not DB["config"]["group_id"]:
        await update.message.reply_text(
            "⛔ <b>NO GROUP LINKED!</b>\n\n"
            "<i>Add me to a group first!</i>",
            parse_mode=ParseMode.HTML
        )
        return

    msg = await update.message.reply_text(
        "🧠 <b>AI ANALYZING...</b>\n\n"
        "⏳ <i>Please wait while I scan your timetable...</i>",
        parse_mode=ParseMode.HTML
    )
    try:
        f = await update.message.photo[-1].get_file()
        b = await f.download_as_bytearray()
        sch = await analyze_timetable_image(b)
        
        if not sch:
            await msg.edit_text(
                "❌ <b>AI VISION FAILED!</b>\n\n"
                "<i>Could not read the timetable. Try a clearer image.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        c = 0
        now = datetime.now(IST)
        day_map = {"Mon":0, "Tue":1, "Wed":2, "Thu":3, "Fri":4, "Sat":5, "Sun":6}
        gid = DB["config"]["group_id"]
        weeks_to_schedule = 4  # Schedule for 4 weeks ahead
        
        for i in sch:
            batch, sub = i.get("batch", "CSDA"), i.get("subject", "Unk")
            day, t = i.get("day", "Mon"), i.get("time", "10:00")
            
            if batch not in DB["subjects"]: DB["subjects"][batch] = []
            if sub not in DB["subjects"][batch]: DB["subjects"][batch].append(sub)
            
            target = day_map.get(day, 0)
            delta = target - now.weekday()
            if delta <= 0: delta += 7
            
            h, m = map(int, t.split(':'))
            
            # Schedule for multiple weeks
            for week in range(weeks_to_schedule):
                run = now + timedelta(days=delta + (week * 7))
                run = run.replace(hour=h, minute=m, second=0)
                
                jid = f"{batch}_{day}_{int(time.time())}_{c}"
                jdata = {"batch": batch, "subject": sub, "time_display": t, "link": "Check Group", "msg_type": "AI", "day": day}
                
                context.job_queue.run_once(send_alert_job, run, chat_id=gid, name=jid, data=jdata)
                add_job_to_db(jid, run.timestamp(), gid, jdata)
                c += 1
        
        save_db()
        await msg.edit_text(
            f"🎉 <b>AI SCAN COMPLETE!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ <b>{c} classes scheduled!</b>\n\n"
            f"<i>Check</i> 📅 <b>View Schedule</b> <i>to see them!</i> 🚀",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>ERROR!</b>\n\n"
            f"<code>{e}</code>",
            parse_mode=ParseMode.HTML
        )

# ==============================================================================
# 🧠 13. CUSTOM AI
# ==============================================================================
async def start_gemini_tool(update, context):
    if not await require_private_admin(update, context): return ConversationHandler.END
    await update.message.reply_text(
        "🧠 <b>AI ASSISTANT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💬 <i>What would you like me to do?</i>\n\n"
        "<i>Type your prompt below:</i> 👇",
        parse_mode=ParseMode.HTML
    )
    return GEMINI_PROMPT_INPUT

async def process_gemini_prompt(update, context):
    msg = await update.message.reply_text(
        "� <b>THINKING...</b>\n\n"
        "⏳ <i>Processing your request...</i>",
        parse_mode=ParseMode.HTML
    )
    response = await custom_gemini_task(update.message.text)
    await msg.edit_text(response[:4000], parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ==============================================================================
# 📩 14. UTILS
# ==============================================================================
async def feedback_handler(update, context):
    user = update.effective_user
    chat_type = update.effective_chat.type
    
    # In private chat, require admin. In groups, allow anyone.
    if chat_type == 'private' and not is_admin(user.username):
        await update.message.reply_text(
            f"⛔ <b>ACCESS DENIED</b>\nContact @AvadaKedavaaraa",
            parse_mode=ParseMode.HTML
        )
        return

    msg = update.message.text.replace("/feedback", "").strip()
    if msg:
        # Store username and chat info PRIVATELY (admins can see this)
        username = user.username or "no_username"
        name = user.first_name or "Unknown"
        user_id = user.id
        chat_info = f"Group: {update.effective_chat.title}" if chat_type != 'private' else "Private Chat"
        
        # Store detailed info for admin viewing
        feedback_entry = {
            "timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "message": msg,
            "username": username,
            "name": name,
            "user_id": user_id,
            "chat_type": chat_info
        }
        DB["feedback"].append(feedback_entry)
        save_db()
        
        # Show ANONYMOUS confirmation to user (they think it's anonymous)
        await update.message.reply_text(
            "✅ <b>ANONYMOUS FEEDBACK SENT!</b>\n\n"
            "<i>Your feedback has been received anonymously.</i>\n"
            "<i>Thank you for helping us improve! 🙏</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "📝 <b>ANONYMOUS FEEDBACK</b>\n\n"
            "<i>Your feedback will be sent anonymously.</i>\n\n"
            "<b>Usage:</b> <code>/feedback Your message here</code>",
            parse_mode=ParseMode.HTML
        )

async def viewfeedback_handler(update, context):
    """View all feedback - Admin only, Private chat only"""
    if not await require_private_admin(update, context): return
    
    feedback_list = DB.get("feedback", [])
    
    if not feedback_list:
        await update.message.reply_text(
            "\ud83d\udced <b>NO FEEDBACK YET!</b>\n\n"
            "<i>No feedback has been submitted.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Build feedback display - handle both old (string) and new (dict) formats
    msg = "\ud83d\udcac <b>FEEDBACK INBOX</b>\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
    
    # Show last 10 feedback entries (newest first)
    recent_feedback = feedback_list[-10:][::-1]
    
    for i, entry in enumerate(recent_feedback, 1):
        if isinstance(entry, dict):
            # New format with user details
            timestamp = entry.get("timestamp", "Unknown time")
            message = entry.get("message", "No message")
            username = entry.get("username", "no_username")
            name = entry.get("name", "Unknown")
            user_id = entry.get("user_id", "N/A")
            chat_type = entry.get("chat_type", "Unknown")
            
            msg += f"<b>{i}.</b> \ud83d\udcc5 {timestamp}\n"
            msg += f"   \ud83d\udc64 <b>{name}</b> (@{username})\n"
            msg += f"   \ud83c\udd94 <code>{user_id}</code>\n"
            msg += f"   \ud83d\udccd {chat_type}\n"
            msg += f"   \ud83d\udcdd <i>{message[:100]}{'...' if len(message) > 100 else ''}</i>\n\n"
        else:
            # Old string format (legacy)
            msg += f"<b>{i}.</b> {str(entry)[:150]}{'...' if len(str(entry)) > 150 else ''}\n\n"
    
    total = len(feedback_list)
    msg += f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    msg += f"<i>Showing {len(recent_feedback)} of {total} total feedback entries</i>"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def delete_menu(update, context):
    """Delete classes with pagination - single message UI"""
    if not await require_private_admin(update, context): return
    jobs = context.job_queue.jobs()
    valid_jobs = [j for j in jobs if j.name and isinstance(j.data, dict) and 'batch' in j.data and len(f"kill_{j.name}") <= 64]
    valid_jobs.sort(key=lambda j: j.next_t)
    
    if not valid_jobs:
        await update.message.reply_text(
            "📭 <b>NO CLASSES TO DELETE!</b>\n\n"
            "<i>Schedule some classes first.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Store jobs in context for pagination
    context.user_data['delete_jobs'] = [j.name for j in valid_jobs]
    context.user_data['delete_page'] = 0
    
    await show_delete_page(update.message, context, valid_jobs)

async def show_delete_page(message_or_query, context, valid_jobs=None, edit=False):
    """Show delete page with pagination"""
    if valid_jobs is None:
        jobs = context.job_queue.jobs()
        job_names = context.user_data.get('delete_jobs', [])
        valid_jobs = [j for j in jobs if j.name in job_names]
        valid_jobs.sort(key=lambda j: j.next_t)
    
    PAGE_SIZE = 8
    page = context.user_data.get('delete_page', 0)
    total_pages = max(1, (len(valid_jobs) + PAGE_SIZE - 1) // PAGE_SIZE)
    
    # Ensure page is in bounds
    page = min(page, total_pages - 1)
    context.user_data['delete_page'] = page
    
    start_idx = page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, len(valid_jobs))
    page_jobs = valid_jobs[start_idx:end_idx]
    
    rows = []
    for j in page_jobs:
        d = j.data
        try:
            time_str = j.next_t.strftime("%d %b %H:%M")
        except:
            time_str = d.get('time_display', '')
        rows.append([InlineKeyboardButton(f"❌ {d['batch']} {d['subject'][:12]} ({time_str})", callback_data=f"kill_{j.name}")])
    
    # Navigation and batch delete
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="del_page_prev"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data="del_page_next"))
    if nav_row:
        rows.append(nav_row)
    
    # Add Delete All button
    if len(valid_jobs) > 1:
        rows.append([InlineKeyboardButton(f"🗑️ DELETE ALL ({len(valid_jobs)})", callback_data="kill_all_confirm")])
    
    text = (
        f"🗑️ <b>DELETE CLASSES</b> ({len(valid_jobs)} total)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Page {page + 1}/{total_pages}</i>\n\n"
        "<i>Tap to delete:</i> 👇"
    )
    
    if edit:
        await message_or_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML
        )
    else:
        await message_or_query.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML
        )

async def handle_kill(update, context):
    """Handle delete class callbacks - single deletion, pagination, and delete all"""
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # Handle pagination
    if data == "del_page_prev":
        context.user_data['delete_page'] = max(0, context.user_data.get('delete_page', 0) - 1)
        await show_delete_page(query, context, edit=True)
        return
    elif data == "del_page_next":
        context.user_data['delete_page'] = context.user_data.get('delete_page', 0) + 1
        await show_delete_page(query, context, edit=True)
        return
    
    # Handle delete all confirmation
    if data == "kill_all_confirm":
        job_names = context.user_data.get('delete_jobs', [])
        count = 0
        for name in job_names:
            jobs = context.job_queue.get_jobs_by_name(name)
            for j in jobs:
                j.schedule_removal()
            remove_job_from_db(name)
            count += 1
        
        context.user_data['delete_jobs'] = []
        await query.edit_message_text(
            f"✅ <b>DELETED {count} CLASSES!</b>\n\n"
            "<i>All scheduled classes have been removed.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Handle single class deletion
    qid = data.replace("kill_", "")
    jobs = context.job_queue.get_jobs_by_name(qid)
    for j in jobs:
        j.schedule_removal()
    remove_job_from_db(qid)
    
    # Update the job list and show next page
    if 'delete_jobs' in context.user_data:
        context.user_data['delete_jobs'] = [n for n in context.user_data['delete_jobs'] if n != qid]
    
    remaining = len(context.user_data.get('delete_jobs', []))
    if remaining > 0:
        await show_delete_page(query, context, edit=True)
    else:
        await query.edit_message_text(
            "✅ <b>ALL CLASSES DELETED!</b>\n\n"
            "<i>No more scheduled classes.</i>",
            parse_mode=ParseMode.HTML
        )

async def handle_expired(update, context):
    await update.callback_query.answer("⚠️ Expired.", show_alert=True)

# ==============================================================================
# � RESET / REVOKE COMMAND
# ==============================================================================
async def reset_command(update, context):
    """Manual reset command for admins to fix issues - DOES NOT clear database schedules"""
    if not await require_private_admin(update, context): return
    
    # Clear all scheduled jobs from MEMORY ONLY (not database)
    # This fixes issues without losing saved schedules
    jobs = context.job_queue.jobs()
    cleared = 0
    for job in jobs:
        if job.name and isinstance(job.data, dict):
            job.schedule_removal()
            cleared += 1
    
    # DO NOT clear DB["active_jobs"] - preserve schedules in database!
    # Jobs will be restored from database on next bot restart
    
    await update.message.reply_text(
        "🔄 <b>VASUKI MEMORY RESET COMPLETE!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Cleared <b>{cleared}</b> jobs from memory\n"
        f"💾 Schedules preserved in database\n"
        f"🔄 Jobs will restore on next restart\n\n"
        "<i>If you're still seeing issues:</i>\n"
        "┣ 1️⃣ Go to @BotFather\n"
        "┣ 2️⃣ Send /revoke\n"
        "┣ 3️⃣ Get new token\n"
        "┗ 4️⃣ Update on Render",
        parse_mode=ParseMode.HTML
    )

# ==============================================================================
# 🧨 15. RESET DATABASE COMMAND
# ==============================================================================
async def start_reset_db(update, context):
    """Start the reset database conversation"""
    if not await require_private_admin(update, context): return ConversationHandler.END
    
    kb = [
        [InlineKeyboardButton("💣 YES, DELETE EVERYTHING", callback_data="reset_confirm")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="reset_cancel")]
    ]
    
    await update.message.reply_text(
        "⚠️ <b>DANGER ZONE: RESET DATABASE</b> ⚠️\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "😱 <b>ARE YOU SURE?</b>\n"
        "<i>This will permanently delete:</i>\n"
        "• All scheduled classes\n"
        "• All subjects\n"
        "• Attendance records\n"
        "• System stats\n\n"
        "👉 <i>This action CANNOT be undone!</i>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return RESET_CONFIRM

async def confirm_reset_db(update, context):
    """Execute the database reset"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "reset_cancel":
        await query.edit_message_text(
            "✅ <b>RESET CANCELLED</b>\n\n"
            "<i>Your data is safe! Phew...</i> 😅",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
        
    if query.data == "reset_confirm":
        global DB
        # Preserve config link
        old_config = DB.get("config", DEFAULT_DB["config"])
        old_admins = DB.get("admins", []) # Preserve admins so they don't get locked out
        
        # Reset to default
        DB = DEFAULT_DB.copy()
        DB["config"] = old_config
        DB["admins"] = old_admins
        
        # Clear schedules from memory
        for job in context.job_queue.jobs():
            job.schedule_removal()
            
        save_db()
        
        await query.edit_message_text(
            "💥 <b>DATABASE WIPED!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "✅ <i>All data has been reset to factory defaults.</i>\n"
            "✅ <i>Admins and Group link preserved.</i>\n\n"
            "🚀 <i>Ready for a fresh start!</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

# ==============================================================================
# ✏️ 16. EDIT SUBJECT COMMAND
# ==============================================================================
async def start_edit_subject(update, context):
    """Start the edit subject wizard"""
    if not await require_private_admin(update, context): return ConversationHandler.END
    
    kb = [
        [InlineKeyboardButton("🟦 CSDA", callback_data="esub_CSDA"), 
         InlineKeyboardButton("🟧 AICS", callback_data="esub_AICS")]
    ]
    await update.message.reply_text(
        "✏️ <b>EDIT SUBJECT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select the batch:</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SUB_SELECT_BATCH

async def edit_sub_select_batch(update, context):
    """Handle batch selection and show subjects"""
    query = update.callback_query
    await query.answer()
    
    batch = query.data.split("_")[1]
    context.user_data['esub_batch'] = batch
    
    subs = DB["subjects"].get(batch, [])
    if not subs:
        await query.edit_message_text(
            f"⚠️ <b>NO SUBJECTS IN {batch}!</b>\n\n"
            f"<i>Add some subjects first.</i>"
        )
        return ConversationHandler.END
        
    rows = []
    for s in subs:
        rows.append([InlineKeyboardButton(f"📖 {s}", callback_data=f"esub_pick_{s}")])
    rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="esub_cancel")])
    
    await query.edit_message_text(
        f"✏️ <b>EDIT SUBJECT ({batch})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<i>Select a subject to modify:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SUB_SELECT_SUBJECT

async def edit_sub_select_subject(update, context):
    """Handle subject selection and show actions"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data == "esub_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END
        
    sub = data.replace("esub_pick_", "")
    context.user_data['esub_subject'] = sub
    
    kb = [
        [InlineKeyboardButton("✏️ Rename", callback_data="esub_rename")],
        [InlineKeyboardButton("🗑️ Delete", callback_data="esub_delete")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="esub_cancel")]
    ]
    
    await query.edit_message_text(
        f"🛠️ <b>MANAGE: {sub}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<i>What would you like to do?</i>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SUB_ACTION

async def edit_sub_action(update, context):
    """Handle rename or delete action"""
    query = update.callback_query
    await query.answer()
    
    action = query.data
    if action == "esub_cancel":
        await query.edit_message_text("❌ Cancelled.")
        return ConversationHandler.END
        
    if action == "esub_delete":
        batch = context.user_data['esub_batch']
        sub = context.user_data['esub_subject']
        
        if sub in DB["subjects"][batch]:
            DB["subjects"][batch].remove(sub)
            save_db()
            
        await query.edit_message_text(
            f"🗑️ <b>DELETED!</b>\n\n"
            f"✅ <i>{sub} has been removed from {batch}.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
        
    if action == "esub_rename":
        await query.edit_message_text(
            "✍️ <b>RENAME SUBJECT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Enter the new name:</i>",
            parse_mode=ParseMode.HTML
        )
        return EDIT_SUB_NEW_NAME

async def edit_sub_save_rename(update, context):
    """Save the renamed subject"""
    new_name = update.message.text.strip()
    batch = context.user_data['esub_batch']
    old_name = context.user_data['esub_subject']
    
    if new_name == old_name:
        await update.message.reply_text("⚠️ Name is same as before.")
        return ConversationHandler.END
        
    if old_name in DB["subjects"][batch]:
        # Rename in list (preserve order)
        idx = DB["subjects"][batch].index(old_name)
        DB["subjects"][batch][idx] = new_name
        save_db()
        
    await update.message.reply_text(
        f"✅ <b>RENAMED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔄 <b>{old_name}</b> ➡️ <b>{new_name}</b>\n"
        f"<i>Database updated.</i>",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# ==============================================================================
# 🛠️ ADMIN COMMAND SHORTCUTS
# ==============================================================================
async def admin_command(update, context):
    """Show admin tools keyboard"""
    if not await require_private_admin(update, context): return
    await update.message.reply_text(
        "🛠️ <b>ADMIN TOOLS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select an option from the keyboard below:</i> 👇",
        reply_markup=get_main_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def schedule_command(update, context):
    """Quick access to view schedule"""
    if not await require_private_admin(update, context): return
    await view_schedule_handler(update, context)

async def export_command(update, context):
    """Quick access to export data"""
    if not await require_private_admin(update, context): return
    await export_data(update, context)

async def subjects_command(update, context):
    """Quick access to view subjects"""
    if not await require_private_admin(update, context): return
    await view_all_subjects(update, context)

async def attendance_command(update, context):
    """Quick access to attendance stats"""
    if not await require_private_admin(update, context): return
    await view_attendance_stats(update, context)

# ==============================================================================
# ⚠️ GLOBAL ERROR HANDLER
# ==============================================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors and notify admins"""
    logger.error(f"Exception: {context.error}")
    
    # Try to notify the user if possible
    if update and hasattr(update, 'effective_message') and update.effective_message:
        error_msg = str(context.error)
        
        # Check for specific known errors
        if "Conflict" in error_msg:
            await update.effective_message.reply_text(
                "⚠️ <b>BOT CONFLICT DETECTED!</b>\n\n"
                "<i>Multiple bot instances are running.</i>\n\n"
                "🔧 <b>Quick Fix:</b> Use /reset\n"
                "🛡️ <b>Permanent Fix:</b> Revoke token via @BotFather",
                parse_mode=ParseMode.HTML
            )
        elif "Button_data_invalid" in error_msg:
            await update.effective_message.reply_text(
                "⚠️ <b>BUTTON ERROR!</b>\n\n"
                "<i>Some buttons have expired data.</i>\n\n"
                "🔧 <b>Fix:</b> Use /reset to clear old jobs",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.effective_message.reply_text(
                f"❌ <b>AN ERROR OCCURRED</b>\n\n"
                f"<code>{error_msg[:200]}</code>\n\n"
                f"<i>Try /reset if issues persist.</i>",
                parse_mode=ParseMode.HTML
            )

# ==============================================================================
# 🚀 16. MAIN
# ==============================================================================
async def post_init(app):
    # Group commands - feedback + updategroup for admins
    group_commands = [
        BotCommand("feedback", "💬 Send Feedback to Vasuki Bot"),
        BotCommand("updategroup", "🔄 Update Group Link (Admin)"),
    ]
    
    # Private chat commands - all commands including admin tools
    private_commands = [
        BotCommand("start", "🏠 Open Dashboard"),
        BotCommand("admin", "🛠️ Admin Tools"),
        BotCommand("schedule", "📅 View Schedule"),
        BotCommand("subjects", "📚 All Subjects"),
        BotCommand("editsubject", "✏️ Edit Subjects"),
        BotCommand("topics", "💬 View Topics"),
        BotCommand("edittopic", "✏️ Edit Topic"),
        BotCommand("deletetopic", "🗑️ Delete Topic"),
        BotCommand("verifytopics", "🔄 Verify Topics"),
        BotCommand("attendance", "📊 Attendance Report"),
        BotCommand("refresh", "🔄 Refresh Database"),
        BotCommand("export", "📤 Export Data"),
        BotCommand("reset", "🔄 Reset & Fix Issues"),
        BotCommand("manualrestart", "♻️ Safe Restart"),
        BotCommand("updategroup", "🔄 Update Group Link"),
        BotCommand("resetdatabase", "🧨 Factory Reset"),
        BotCommand("feedback", "💬 Send Feedback"),
    ]

    
    # Set commands for private chats (admins use all features here)
    await app.bot.set_my_commands(
        private_commands,
        scope=BotCommandScopeAllPrivateChats()
    )
    
    # Set commands for groups (only feedback available)
    await app.bot.set_my_commands(
        group_commands,
        scope=BotCommandScopeAllGroupChats()
    )
    
    # Restore scheduled jobs from database
    await restore_jobs(app)
    cleanup_old_data()
    
    # Schedule periodic cleanup (every 24 hours)
    async def scheduled_cleanup(context):
        cleanup_old_data(context)
    
    app.job_queue.run_repeating(scheduled_cleanup, interval=86400, first=86400)
    
    # Schedule Smart Keep-Alive (Ping every 5 mins, SLEEP 1-7 AM IST)
    async def smart_ping(context):
        now = datetime.now(IST)
        # Sleep between 01:00 and 07:00 IST (Cron-job.org will wake at 7 AM)
        if 1 <= now.hour < 7:
            logger.info("💤 Sleep window active (1-7 AM) - skipping ping")
            return
        
        try:
            import httpx
            port = int(os.environ.get("PORT", 8080))
            urls = [
                f"http://127.0.0.1:{port}/",
                os.environ.get("RENDER_EXTERNAL_URL")  # Critical: External ping!
            ]
            
            async with httpx.AsyncClient(timeout=10) as client:
                for url in urls:
                    if url:
                        try:
                            await client.get(url)
                            logger.info(f"🔔 Ping sent to {url}")
                        except Exception as inner_e:
                            logger.warning(f"⚠️ Ping failed for {url}: {inner_e}")

        except Exception as e:
            logger.warning(f"⚠️ Keep-alive mechanism error: {e}")

    # 5 minutes = 300 seconds
    app.job_queue.run_repeating(smart_ping, interval=300, first=60)
    
    # Memory monitor - Alert admin at 70% (358 MB of 512 MB)
    async def memory_monitor(context):
        try:
            import psutil
            process = psutil.Process()
            mem_mb = process.memory_info().rss / (1024 * 1024)
            mem_percent = (mem_mb / 512) * 100
            
            if mem_percent >= 70:
                # Alert all admins
                admin_list = ADMIN_USERNAMES + DB.get("admins", [])
                warning_msg = (
                    f"⚠️ <b>MEMORY WARNING</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 <b>Usage:</b> {mem_mb:.1f} MB / 512 MB ({mem_percent:.1f}%)\n"
                    f"⏰ <b>Time:</b> {datetime.now(IST).strftime('%H:%M:%S IST')}\n\n"
                    f"💡 <i>Consider using /manualrestart to free memory</i>"
                )
                for admin in admin_list:
                    if admin:
                        try:
                            # Try to get chat ID from username
                            # This works if admin has started the bot
                            pass  # We'll log instead since we can't easily get chat ID
                        except:
                            pass
                logger.warning(f"⚠️ Memory at {mem_percent:.1f}% ({mem_mb:.1f} MB)")
        except ImportError:
            pass  # psutil not installed
        except Exception as e:
            logger.error(f"Memory monitor error: {e}")
    
    # Check memory every 5 minutes
    app.job_queue.run_repeating(memory_monitor, interval=300, first=120)

    logger.info("✅ Vasuki Bot initialized successfully")

async def manual_restart_command(update, context):
    """Admin command to safely restart the bot - preserves all schedules"""
    if not await require_private_admin(update, context): return
    
    await update.message.reply_text(
        "🔄 <b>MANUAL RESTART</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💾 Saving database to cloud...",
        parse_mode=ParseMode.HTML
    )
    
    # Force save to Supabase
    save_db()
    
    # Get memory info
    try:
        import psutil
        process = psutil.Process()
        mem_mb = process.memory_info().rss / (1024 * 1024)
    except:
        mem_mb = 0
    
    await update.message.reply_text(
        f"✅ <b>DATABASE SAVED!</b>\n\n"
        f"📊 Memory before restart: {mem_mb:.1f} MB\n"
        f"📅 Active schedules: {len(DB.get('active_jobs', []))}\n\n"
        f"🔄 <i>Restarting in 3 seconds...</i>\n"
        f"<i>All schedules will be restored automatically.</i>",
        parse_mode=ParseMode.HTML
    )
    
    # Wait a bit for message to send
    await asyncio.sleep(3)
    
    logger.info("🔄 Manual restart triggered by admin")
    
    # Exit gracefully - Render will auto-restart
    import sys
    sys.exit(0)

async def login_command(update, context):
    """Allow users to gain admin access via password"""
    user = update.effective_user
    args = context.args
    
    if not ADMIN_PASSWORD:
        await update.message.reply_text("❌ <b>LOGIN DISABLED</b>\nNo password configured in settings.", parse_mode=ParseMode.HTML)
        return

    if not args:
        await update.message.reply_text("🔑 <b>ADMIN LOGIN</b>\n\nUsage: <code>/login [password]</code>", parse_mode=ParseMode.HTML)
        return
        
    password = args[0]
    if password == ADMIN_PASSWORD:
        if "admins" not in DB: DB["admins"] = []
        username = user.username
        
        # Check if already admin
        db_admins = [a.lower() for a in DB["admins"]]
        if username and username.lower() not in db_admins:
            DB["admins"].append(username)
            save_db()
            
        await update.message.reply_text(
            "✅ <b>ACCESS GRANTED!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Welcome, {user.first_name}!</b>\n"
            "<i>You are now an authenticated admin.</i>\n\n"
            "🚀 <b>TYPE /start TO BEGIN!</b>",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("⛔ <b>ACCESS DENIED</b>\nIncorrect password.", parse_mode=ParseMode.HTML)

def main():
    keep_alive()
    # Reduced connection pool: 8 → 4 (saves ~20-30MB memory)
    request = HTTPXRequest(connection_pool_size=4, connect_timeout=60.0, read_timeout=60.0)
    app = Application.builder().token(TOKEN).request(request).defaults(Defaults(tzinfo=IST)).post_init(post_init).build()

    # Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login_command))  # Added login command
    app.add_handler(CommandHandler("feedback", feedback_handler))
    app.add_handler(CommandHandler("viewfeedback", viewfeedback_handler))  # Admin view feedback
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("refresh", refresh_db_command))  # Live DB refresh
    app.add_handler(CommandHandler("manualrestart", manual_restart_command))  # Safe restart
    app.add_handler(CommandHandler("updategroup", updategroup_command))  # Fix group ID issues
    app.add_handler(MessageHandler(filters.Regex("^🔄 Reset System"), reset_command)) # Added button handler

    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("subjects", subjects_command))
    app.add_handler(CommandHandler("attendance", attendance_command))
    app.add_handler(CommandHandler("topic", register_topic_command))  # New topic command
    app.add_handler(CommandHandler("verifytopics", verify_topics_command))  # Verify topics
    app.add_handler(CallbackQueryHandler(verify_topics_callback, pattern="^verify_topics$"))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CREATED, auto_register_topic)) # Auto-register
    
    app.add_handler(MessageHandler(filters.Regex("^📂 More Options"), handle_navigation))
    app.add_handler(MessageHandler(filters.Regex("^🔙 Back"), handle_navigation))
    app.add_handler(MessageHandler(filters.Regex("^📤 Export"), export_data))
    app.add_handler(MessageHandler(filters.Regex("^📥 Import"), import_request))
    app.add_handler(MessageHandler(filters.Document.MimeType("application/json"), handle_import_file))
    app.add_handler(MessageHandler(filters.Regex("^🗑️ Delete Class"), delete_menu))
    app.add_handler(CallbackQueryHandler(handle_kill, pattern="^(kill_|del_page_)"))
    
    # NEW: Added View All Subjects Handler
    app.add_handler(MessageHandler(filters.Regex("^📚 All Subjects"), view_all_subjects))
    app.add_handler(MessageHandler(filters.Regex("^🛠️ Admin Tools"), admin_command))
    app.add_handler(MessageHandler(filters.Regex("^☁️ Force Save"), force_cloud_save))

    app.add_handler(MessageHandler(filters.Regex("^📸 AI Auto-Schedule"), prompt_image_upload)) 
    app.add_handler(MessageHandler(filters.Regex("^📊 Attendance"), view_attendance_stats)) 
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Regex("^📅 View Schedule"), view_schedule_handler))
    app.add_handler(CallbackQueryHandler(view_schedule_handler, pattern="^schedule_page_"))
    app.add_handler(CallbackQueryHandler(mark_attendance, pattern="^att_"))
    app.add_handler(CallbackQueryHandler(verify_topics_callback, pattern="^verify_page_")) # Pagination


    txt_filter = filters.TEXT & ~filters.Regex(MENU_REGEX)

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Add Subject"), start_add_sub)],
        states={
            SELECT_BATCH: [CallbackQueryHandler(save_batch_for_sub, pattern="^sub_")],
            NEW_SUBJECT_INPUT: [MessageHandler(txt_filter, save_new_sub)]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ Edit Class"), start_edit)],
        states={
            EDIT_SELECT_JOB: [CallbackQueryHandler(edit_select_job, pattern="^edit_")],
            EDIT_CHOOSE_FIELD: [CallbackQueryHandler(edit_choose_field, pattern="^field_")],
            EDIT_NEW_VALUE: [MessageHandler(txt_filter, edit_save)],
            EDIT_SELECT_SCOPE: [CallbackQueryHandler(edit_scope_handler, pattern="^scope_")]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🧠 Custom AI"), start_gemini_tool)],
        states={GEMINI_PROMPT_INPUT: [MessageHandler(txt_filter, process_gemini_prompt)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🟦 Schedule CSDA$|^🟧 Schedule AICS$"), init_schedule_wizard)],
        states={
            SELECT_SUB_OR_ADD: [CallbackQueryHandler(wizard_pick_sub, pattern="^pick_")],
            SELECT_DAYS: [CallbackQueryHandler(wizard_toggle_days, pattern="^toggle_|^days_done")],
            INPUT_START_DATE: [MessageHandler(txt_filter, wizard_start_date)],
            INPUT_END_DATE: [MessageHandler(txt_filter, wizard_end_date)],
            INPUT_TIME: [MessageHandler(txt_filter, wizard_time)],
            INPUT_LINK: [MessageHandler(txt_filter, wizard_link)],
            SELECT_TOPIC: [CallbackQueryHandler(wizard_topic_selection, pattern="^topic_")],
            SELECT_OFFSET: [CallbackQueryHandler(wizard_offset, pattern="^offset_")],
            CUSTOM_OFFSET_INPUT: [MessageHandler(txt_filter, wizard_custom_offset)],
            MSG_TYPE_CHOICE: [CallbackQueryHandler(wizard_msg_choice, pattern="^msg_")],
            INPUT_MANUAL_MSG: [MessageHandler(txt_filter, wizard_manual_msg)]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    # Custom Message Scheduler
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Custom Message"), start_custom_msg)],
        states={
            CUSTOM_MSG_BATCH: [CallbackQueryHandler(cmsg_batch_selected, pattern="^cmsg_")],
            CUSTOM_MSG_DAYS: [CallbackQueryHandler(cmsg_toggle_days, pattern="^toggle_|^days_done")],
            CUSTOM_MSG_TIME: [MessageHandler(txt_filter, cmsg_time_input)],
            CUSTOM_MSG_START: [MessageHandler(txt_filter, cmsg_start_date)],
            CUSTOM_MSG_END: [MessageHandler(txt_filter, cmsg_end_date)],
            CUSTOM_MSG_TEXT: [MessageHandler(txt_filter, cmsg_text_input)],
            CUSTOM_MSG_LINK: [
                MessageHandler(txt_filter, cmsg_link_input),
                CallbackQueryHandler(cmsg_link_input, pattern="^cmsg_link_skip")
            ],
            SELECT_TOPIC: [CallbackQueryHandler(cmsg_topic_selection, pattern="^ctopic_")]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    # Admin Management Handlers
    app.add_handler(MessageHandler(filters.Regex("^👥 Manage Admins"), handle_navigation))
    app.add_handler(MessageHandler(filters.Regex("^📋 View Admins"), view_admins))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👤 Add Admin"), start_add_admin)],
        states={ADD_ADMIN_INPUT: [MessageHandler(txt_filter, save_new_admin)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑️ Remove Admin"), start_remove_admin)],
        states={REMOVE_ADMIN_INPUT: [MessageHandler(txt_filter, remove_admin_save)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))

    # Night Schedule Handler
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("Night Schedule"), start_night_schedule)],
        states={NIGHT_SCHEDULE_TIME: [MessageHandler(txt_filter, save_night_schedule_time)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))

    # Topic Management Handlers
    app.add_handler(MessageHandler(filters.Regex("^💬 Manage Topics"), manage_topics_handler))
    app.add_handler(MessageHandler(filters.Regex("^📋 List Topics"), view_topics))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Add Topic Manual"), start_add_topic)],
        states={
            ADD_TOPIC_NAME: [MessageHandler(txt_filter, save_topic_name)],
            ADD_TOPIC_ID: [MessageHandler(txt_filter, save_topic_id)]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑️ Remove Topic"), start_remove_topic)],
        states={REMOVE_TOPIC_INPUT: [MessageHandler(txt_filter, remove_topic_save)]},
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard), CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))

    # Edit Subject Handler
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("editsubject", start_edit_subject)],
        states={
            EDIT_SUB_SELECT_BATCH: [CallbackQueryHandler(edit_sub_select_batch, pattern="^esub_")],
            EDIT_SUB_SELECT_SUBJECT: [CallbackQueryHandler(edit_sub_select_subject, pattern="^esub_")],
            EDIT_SUB_ACTION: [CallbackQueryHandler(edit_sub_action, pattern="^esub_")],
            EDIT_SUB_NEW_NAME: [MessageHandler(txt_filter, edit_sub_save_rename)]
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard), MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    # Reset Database Handler
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("resetdatabase", start_reset_db)],
        states={RESET_CONFIRM: [CallbackQueryHandler(confirm_reset_db, pattern="^reset_")]},
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=60
    ))

    # Topic Commands (/topics, /edittopic, /deletetopic)
    app.add_handler(CommandHandler("topics", topics_command))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edittopic", start_edit_topic)],
        states={
            EDIT_TOPIC_SELECT: [CallbackQueryHandler(edit_topic_select, pattern="^edtopic_")],
            EDIT_TOPIC_NEW_NAME: [MessageHandler(txt_filter, edit_topic_save)]
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=300
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("deletetopic", start_delete_topic)],
        states={DELETE_TOPIC_CONFIRM: [CallbackQueryHandler(delete_topic_confirm, pattern="^deltopic_")]},
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        conversation_timeout=60
    ))

    app.add_handler(CallbackQueryHandler(handle_expired))
    
    # Global error handler
    app.add_error_handler(error_handler)

    print("✅ VASUKI CLOUD BOT ONLINE")
    # drop_pending_updates=True prevents conflict with previous instances
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()