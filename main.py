"""
================================================================================
🤖 TELEGRAM ACADEMIC BOT - TITAN FINAL (v11.0 - COMPLETE)
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
from datetime import datetime, timedelta
import pytz
from flask import Flask
from dotenv import load_dotenv
import google.generativeai as genai
from PIL import Image

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
raw_admins = os.environ.get("ADMIN_USERNAMES", "")
ADMIN_USERNAMES = [
    u.strip().replace("@", "") 
    for u in raw_admins.split(",") 
    if u.strip()
]

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
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')
        logger.info("✅ Gemini AI Connected")
    except Exception as e:
        model = None
        logger.error(f"❌ Gemini AI Failed: {e}")
else:
    model = None

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
                # Fix legacy issues
                if "active_jobs" not in DB: DB["active_jobs"] = []
                if "schedules" not in DB: DB["schedules"] = []
                if "subjects" not in DB: DB["subjects"] = {"CSDA": [], "AICS": []}
                logger.info("📂 Database Loaded from Supabase.")
        else:
            logger.info("🆕 No Cloud Data found. Initializing...")
            save_db()
    except Exception as e:
        logger.error(f"❌ Failed to load DB from Cloud: {e}")

def _save_db_thread():
    if not supabase: return
    try:
        supabase.table("bot_storage").upsert({"id": 1, "data": DB}).execute()
    except Exception as e:
        logger.error(f"❌ Cloud Save Failed: {e}")

def save_db():
    t = Thread(target=_save_db_thread)
    t.start()

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

def cleanup_old_data():
    cleaned = 0
    now_ts = time.time()
    thirty_days = 30 * 24 * 60 * 60
    
    keys_to_remove = []
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
        
    if cleaned > 0:
        save_db()
        logger.info(f"🧹 Cleaned up {cleaned} old records.")

# ==============================================================================
# 🚦 3. CONVERSATION STATES
# ==============================================================================
(
    SELECT_BATCH, NEW_SUBJECT_INPUT, SELECT_SUB_OR_ADD, SELECT_DAYS, 
    INPUT_START_DATE, INPUT_END_DATE, INPUT_TIME, INPUT_LINK,
    SELECT_OFFSET, MSG_TYPE_CHOICE, INPUT_MANUAL_MSG, GEMINI_PROMPT_INPUT,
    EDIT_SELECT_JOB, EDIT_CHOOSE_FIELD, EDIT_NEW_VALUE
) = range(15)

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
        <h1>🤖 TITAN BOT STATUS: <span style="color: #2ea043;">ONLINE</span></h1>
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
    if not model: return None
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
        response = await asyncio.to_thread(model.generate_content, [prompt, img])
        text = response.text
        text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```", "", text)
        return json.loads(text.strip())
    except Exception as e:
        logger.error(f"AI Vision Error: {e}")
        return None

async def generate_hype_message(batch, subject, time_str, link):
    if not model: return None
    try:
        DB["system_stats"]["ai_requests"] += 1
        date_str = datetime.now(IST).strftime('%A, %d %B')
        prompt = (
            f"Create a HTML notification for a class.\n"
            f"Info: {batch} | {subject} | {time_str} | {date_str} | {link}\n"
            f"Rules: Use HTML tags (<b>, <i>, <code>, <a href='...'>). "
            f"Include <a href='{link}'>JOIN CLASS</a>. Make it exciting."
        )
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text
    except Exception: return None

async def custom_gemini_task(prompt):
    if not model: return "❌ AI Disabled."
    try:
        DB["system_stats"]["ai_requests"] += 1
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text
    except Exception as e: return f"Error: {e}"

# ==============================================================================
# 🎨 6. UI COMPONENTS
# ==============================================================================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📸 AI Auto-Schedule"), KeyboardButton("🧠 Custom AI Tool")],
        [KeyboardButton("🟦 Schedule CSDA"), KeyboardButton("🟧 Schedule AICS")],
        [KeyboardButton("➕ Add Subject"), KeyboardButton("📂 More Options ⤵️")]
    ], resize_keyboard=True, is_persistent=True)

def get_more_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("✏️ Edit Class"), KeyboardButton("🗑️ Delete Class")],
        [KeyboardButton("📅 View Schedule"), KeyboardButton("📊 Attendance")],
        # ADDED MISSING BUTTON HERE
        [KeyboardButton("📚 All Subjects"), KeyboardButton("📤 Export Data")], 
        [KeyboardButton("📥 Import Data"), KeyboardButton("🔙 Back to Main")]
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
    if not username: return False
    if not ADMIN_USERNAMES or ADMIN_USERNAMES == ['']: return True 
    return str(username) in ADMIN_USERNAMES

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.my_chat_member.new_chat_member
    chat = update.effective_chat
    
    if result.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR]:
        DB["config"]["group_id"] = chat.id
        DB["config"]["group_name"] = chat.title
        save_db()
        logger.info(f"🆕 LINKED GROUP: {chat.title} ({chat.id})")
        
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"🤖 <b>TITAN SYSTEM ONLINE</b>\n"
                 f"✅ Connected: <b>{chat.title}</b>\n"
                 f"🕒 Timezone: IST (GMT+5:30)\n"
                 f"🚀 <b>Ready to schedule classes.</b>",
            parse_mode=ParseMode.HTML
        )

# ==============================================================================
# 🏠 8. CORE HANDLERS
# ==============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_type = update.effective_chat.type

    if chat_type in ['group', 'supergroup']:
        if is_admin(user.username):
            DB["config"]["group_id"] = update.effective_chat.id
            DB["config"]["group_name"] = update.effective_chat.title
            save_db()
            try:
                await update.message.reply_text(
                    f"🚀 <b>TITAN ACTIVATED!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ <i>Successfully linked to:</i>\n"
                    f"📍 <b>{update.effective_chat.title}</b>\n\n"
                    f"💡 <i>Use /start in DM for full control!</i>",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to reply in group start: {e}")
        return

    if not is_admin(user.username): return

    grp_name = DB["config"]["group_name"]
    grp_id = DB["config"]["group_id"]
    status_icon = "🟢" if grp_id else "🔴"

    try:
        await update.message.reply_text(
            f"⚡ <b>TITAN COMMAND CENTER</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 <i>Welcome back,</i> <b>{user.first_name}!</b>\n\n"
            f"� <b>CONNECTION STATUS</b>\n"
            f"┣ 🎯 <b>Target:</b> {status_icon} {grp_name}\n"
            f"┣ � <b>Time:</b> {datetime.now(IST).strftime('%H:%M IST')}\n"
            f"┣ 📅 <b>Scheduled:</b> {len(DB['active_jobs'])} classes\n"
            f"┗ 💾 <b>Storage:</b> {'☁️ Supabase' if supabase else '💻 Local'}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Select an option below to begin!</i> 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to send dashboard: {e}")

async def handle_navigation(update, context):
    msg = update.message.text
    if "More Options" in msg:
        await update.message.reply_text(
            "📂 <b>ADVANCED TOOLS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Pick a tool from below:</i> 🛠️",
            reply_markup=get_more_keyboard(),
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

# NEW FEATURE: VIEW ALL SUBJECTS
async def view_all_subjects(update, context):
    if not is_admin(update.effective_user.username): return
    
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
    if not is_admin(update.effective_user.username): return ConversationHandler.END
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
    kb = [
        [InlineKeyboardButton("⏰ Exact Time", callback_data="offset_0")],
        [InlineKeyboardButton("⏱️ 5 Mins Before", callback_data="offset_5")]
    ]
    await update.message.reply_text(
        "⌛ <b>NOTIFICATION TIMING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>When should I notify the group?</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return SELECT_OFFSET

async def wizard_offset(update, context):
    context.user_data['sch_offset'] = int(update.callback_query.data.split("_")[1])
    await update.callback_query.answer()
    kb = [
        [InlineKeyboardButton("✨ AI Auto-Write", callback_data="msg_ai")],
        [InlineKeyboardButton("✍️ Manual Message", callback_data="msg_manual")]
    ]
    await update.callback_query.edit_message_text(
        "📝 <b>MESSAGE STYLE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>How should I announce the class?</i> 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return MSG_TYPE_CHOICE

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
            "msg_type": "MANUAL" if d.get('sch_manual_msg') else "AI"
        }
        context.job_queue.run_once(send_alert_job, notify_dt, chat_id=gid, name=job_id, data=job_data)
        add_job_to_db(job_id, notify_dt.timestamp(), gid, job_data)
        count += 1
    
    msg = (
        f"🎉 <b>SUCCESS!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ <b>{count} class(es) scheduled!</b>\n\n"
        f"📌 <i>Subject:</i> <b>{sub}</b>\n"
        f"🎯 <i>Batch:</i> <b>{batch}</b>\n"
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
    if not is_admin(update.effective_user.username): return ConversationHandler.END
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
    if not is_admin(update.effective_user.username): return ConversationHandler.END
    jobs = context.job_queue.jobs()
    if not jobs:
        await update.message.reply_text(
            "📭 <b>NO CLASSES FOUND!</b>\n\n"
            "<i>Schedule some classes first.</i>",
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    rows = []
    for job in jobs:
        if job.name and isinstance(job.data, dict) and 'batch' in job.data:
            if len(f"edit_{job.name}") > 64: continue
            d = job.data
            rows.append([InlineKeyboardButton(f"📖 {d['batch']} {d['subject']} ({d['time_display']})", callback_data=f"edit_{job.name}")])
    await update.message.reply_text(
        "✏️ <b>EDIT CLASS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select a class to modify:</i> 👇",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML
    )
    return EDIT_SELECT_JOB

async def edit_select_job(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data['edit_job_name'] = query.data.replace("edit_", "")
    jobs = context.job_queue.get_jobs_by_name(context.user_data['edit_job_name'])
    if not jobs: return ConversationHandler.END
    context.user_data['old_job_data'] = jobs[0].data
    context.user_data['old_next_t'] = jobs[0].next_t
    
    kb = [
        [InlineKeyboardButton("⏰ Change Time", callback_data="field_time")],
        [InlineKeyboardButton("🔗 Change Link", callback_data="field_link")]
    ]
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
    context.user_data['edit_field'] = query.data.replace("field_", "")
    field_name = "Time (HH:MM)" if context.user_data['edit_field'] == "time" else "Meeting Link"
    await query.edit_message_text(
        f"✏️ <b>ENTER NEW VALUE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<i>Field:</i> <b>{field_name}</b>\n\n"
        f"<i>Type the new value below:</i>",
        parse_mode=ParseMode.HTML
    )
    return EDIT_NEW_VALUE

async def edit_save(update, context):
    new_val = update.message.text.strip()
    d = context.user_data
    job_name = d['edit_job_name']
    
    old_jobs = context.job_queue.get_jobs_by_name(job_name)
    for j in old_jobs: j.schedule_removal()
    remove_job_from_db(job_name)
    
    new_data = d['old_job_data'].copy()
    run_dt = d['old_next_t']
    
    if d['edit_field'] == "time":
        try:
            h, m = map(int, new_val.split(':'))
            run_dt = run_dt.replace(hour=h, minute=m, second=0)
            new_data['time_display'] = new_val
        except: return ConversationHandler.END
    elif d['edit_field'] == "link":
        new_data['link'] = new_val

    context.job_queue.run_once(send_alert_job, run_dt, chat_id=DB["config"]["group_id"], name=job_name, data=new_data)
    add_job_to_db(job_name, run_dt.timestamp(), DB["config"]["group_id"], new_data)
    
    await update.message.reply_text(
        "✅ <b>UPDATED!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Class details have been modified successfully!</i> 🚀",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# ==============================================================================
# 📨 11. JOB EXECUTION
# ==============================================================================
async def send_alert_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    link = data.get('link') if data.get('link') != 'None' else "https://t.me/"

    if data.get('msg_type') == "AI":
        text = await generate_hype_message(data['batch'], data['subject'], data['time_display'], link)
        if not text: text = f"<b>🔔 {data['batch']} CLASS: {data['subject']}</b>\n⏰ {data['time_display']}"
    else:
        text = f"{data.get('manual_msg')}\n⏰ {data['time_display']}"
    
    msg = f"{text}\n\n👇 <i>Mark attendance:</i>"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🙋 I am Present", callback_data=f"att_{job.name}")]])
    
    try:
        await context.bot.send_message(job.chat_id, text=msg, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
        remove_job_from_db(job.name)
    except Exception as e:
        logger.error(f"❌ Failed to send alert: {e}")

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
# 📊 12. EXTRAS
# ==============================================================================
async def export_data(update, context):
    if not is_admin(update.effective_user.username): return
    f = io.BytesIO(json.dumps(DB, indent=4).encode())
    f.name = f"titan_backup_{int(time.time())}.json"
    await context.bot.send_document(
        update.effective_chat.id,
        document=f,
        caption=(
            "📦 <b>BACKUP EXPORTED!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "✅ <i>Your cloud data has been exported.</i>\n"
            "💾 <i>Keep this file safe!</i>"
        ),
        parse_mode=ParseMode.HTML
    )

async def import_request(update, context):
    if not is_admin(update.effective_user.username): return
    await update.message.reply_text(
        "📥 <b>IMPORT DATA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ <b>WARNING:</b> <i>This will OVERWRITE all current data!</i>\n\n"
        "<i>Upload your</i> <code>.json</code> <i>backup file below:</i>",
        parse_mode=ParseMode.HTML
    )
    context.user_data['wait_import'] = True

async def handle_import_file(update, context):
    if not context.user_data.get('wait_import'): return
    file = await update.message.document.get_file()
    raw = await file.download_as_bytearray()
    global DB
    DB = json.loads(raw.decode())
    save_db()
    await update.message.reply_text(
        "✅ <b>IMPORT SUCCESSFUL!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Cloud database has been updated.</i>\n\n"
        "⚠️ <b>Note:</b> <i>Restart the bot to apply all changes.</i>",
        parse_mode=ParseMode.HTML
    )
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
    jobs = context.job_queue.jobs()
    if not jobs:
        await update.message.reply_text(
            "📭 <b>NO UPCOMING CLASSES!</b>\n\n"
            "<i>Schedule some classes first.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    msg = (
        "📅 <b>UPCOMING CLASSES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for job in sorted(jobs, key=lambda j: j.next_t):
        if job.name and isinstance(job.data, dict) and 'batch' in job.data:
            d = job.data
            msg += f"📖 <b>{d['batch']}</b> • {d['subject']}\n"
            msg += f"     ⏰ <i>{d['time_display']}</i>\n\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def prompt_image_upload(update, context):
    if not is_admin(update.effective_user.username): return
    await update.message.reply_text(
        "📸 <b>AI TIMETABLE SCANNER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧠 <i>Send me a photo of your timetable</i>\n"
        "🤖 <i>I'll automatically schedule all classes!</i>\n\n"
        "✨ <b>Tip:</b> <i>Clearer images = better results!</i>",
        parse_mode=ParseMode.HTML
    )

async def view_attendance_stats(update, context):
    if not is_admin(update.effective_user.username): return
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
    if not is_admin(update.effective_user.username): return
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
        
        for i in sch:
            batch, sub = i.get("batch", "CSDA"), i.get("subject", "Unk")
            day, t = i.get("day", "Mon"), i.get("time", "10:00")
            
            if batch not in DB["subjects"]: DB["subjects"][batch] = []
            if sub not in DB["subjects"][batch]: DB["subjects"][batch].append(sub)
            
            target = day_map.get(day, 0)
            delta = target - now.weekday()
            if delta <= 0: delta += 7
            
            h, m = map(int, t.split(':'))
            run = now + timedelta(days=delta)
            run = run.replace(hour=h, minute=m, second=0)
            
            jid = f"{batch}_{day}_{int(time.time())}_{c}"
            jdata = {"batch": batch, "subject": sub, "time_display": t, "link": "Check Group", "msg_type": "AI"}
            
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
    if not is_admin(update.effective_user.username): return ConversationHandler.END
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
    msg = update.message.text.replace("/feedback", "").strip()
    if msg:
        DB["feedback"].append(f"{datetime.now(IST)}: {msg}")
        save_db()
        await update.message.reply_text(
            "✅ <b>FEEDBACK RECEIVED!</b>\n\n"
            "<i>Thank you for your feedback. We'll review it soon!</i> 🙏",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "📝 <b>SEND FEEDBACK</b>\n\n"
            "<i>Usage:</i> <code>/feedback Your message here</code>",
            parse_mode=ParseMode.HTML
        )

async def delete_menu(update, context):
    if not is_admin(update.effective_user.username): return
    jobs = context.job_queue.jobs()
    valid_jobs = [j for j in jobs if j.name and isinstance(j.data, dict) and 'batch' in j.data and len(f"kill_{j.name}") <= 64]
    
    if not valid_jobs:
        await update.message.reply_text(
            "📭 <b>NO CLASSES TO DELETE!</b>\n\n"
            "<i>Schedule some classes first.</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    await update.message.reply_text(
        "🗑️ <b>DELETE CLASSES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Tap</i> ❌ <i>to remove a class:</i>\n",
        parse_mode=ParseMode.HTML
    )
    
    for j in valid_jobs:
        kb = [[InlineKeyboardButton("❌ Delete This", callback_data=f"kill_{j.name}")]]
        await update.message.reply_text(
            f"📖 <b>{j.data['batch']}</b> • {j.data['subject']}\n"
            f"     ⏰ <i>{j.data['time_display']}</i>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

async def handle_kill(update, context):
    qid = update.callback_query.data.replace("kill_", "")
    jobs = context.job_queue.get_jobs_by_name(qid)
    for j in jobs: j.schedule_removal()
    remove_job_from_db(qid)
    await update.callback_query.edit_message_text(
        "✅ <b>CLASS DELETED!</b>\n\n"
        "<i>This class has been removed from the schedule.</i>",
        parse_mode=ParseMode.HTML
    )

async def handle_expired(update, context):
    await update.callback_query.answer("⚠️ Expired.", show_alert=True)

# ==============================================================================
# � RESET / REVOKE COMMAND
# ==============================================================================
async def reset_command(update, context):
    """Manual reset command for admins to fix issues"""
    if not is_admin(update.effective_user.username):
        await update.message.reply_text("⛔ <i>Admin only command.</i>", parse_mode=ParseMode.HTML)
        return
    
    # Clear all scheduled jobs from memory
    jobs = context.job_queue.jobs()
    cleared = 0
    for job in jobs:
        if job.name and isinstance(job.data, dict):
            job.schedule_removal()
            cleared += 1
    
    # Clear jobs from database
    DB["active_jobs"] = []
    save_db()
    
    await update.message.reply_text(
        "🔄 <b>SYSTEM RESET COMPLETE!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Cleared <b>{cleared}</b> scheduled jobs\n"
        f"✅ Database synced\n\n"
        "<i>If you're still seeing issues:</i>\n"
        "┣ 1️⃣ Go to @BotFather\n"
        "┣ 2️⃣ Send /revoke\n"
        "┣ 3️⃣ Get new token\n"
        "┗ 4️⃣ Update on Render",
        parse_mode=ParseMode.HTML
    )

# ==============================================================================
# 🛠️ ADMIN COMMAND SHORTCUTS
# ==============================================================================
async def admin_command(update, context):
    """Show admin tools keyboard"""
    if not is_admin(update.effective_user.username):
        await update.message.reply_text("⛔ <i>Admin only command.</i>", parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(
        "🛠️ <b>ADMIN TOOLS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Select an option from the keyboard below:</i> 👇",
        reply_markup=get_main_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def schedule_command(update, context):
    """Quick access to view schedule"""
    if not is_admin(update.effective_user.username):
        await update.message.reply_text("⛔ <i>Admin only command.</i>", parse_mode=ParseMode.HTML)
        return
    await view_schedule_handler(update, context)

async def export_command(update, context):
    """Quick access to export data"""
    if not is_admin(update.effective_user.username):
        await update.message.reply_text("⛔ <i>Admin only command.</i>", parse_mode=ParseMode.HTML)
        return
    await export_data(update, context)

async def subjects_command(update, context):
    """Quick access to view subjects"""
    if not is_admin(update.effective_user.username):
        await update.message.reply_text("⛔ <i>Admin only command.</i>", parse_mode=ParseMode.HTML)
        return
    await view_all_subjects(update, context)

async def attendance_command(update, context):
    """Quick access to attendance stats"""
    if not is_admin(update.effective_user.username):
        await update.message.reply_text("⛔ <i>Admin only command.</i>", parse_mode=ParseMode.HTML)
        return
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
    # Public commands visible to everyone
    public_commands = [
        BotCommand("start", "🏠 Open Dashboard"),
        BotCommand("feedback", "💬 Send Feedback"),
    ]
    
    # Admin commands - all commands including admin tools
    admin_commands = [
        BotCommand("start", "🏠 Open Dashboard"),
        BotCommand("admin", "🛠️ Admin Tools"),
        BotCommand("schedule", "📅 View Schedule"),
        BotCommand("subjects", "📚 All Subjects"),
        BotCommand("attendance", "📊 Attendance Report"),
        BotCommand("export", "📤 Export Data"),
        BotCommand("reset", "🔄 Reset & Fix Issues"),
        BotCommand("feedback", "💬 Send Feedback"),
    ]
    
    # Set default commands for all users
    await app.bot.set_my_commands(public_commands)
    
    # Set admin commands for private chats (where admins will use them)
    await app.bot.set_my_commands(
        admin_commands,
        scope=BotCommandScopeAllPrivateChats()
    )
    
    # Restore scheduled jobs from database
    await restore_jobs(app)
    cleanup_old_data()
    
    logger.info("✅ Bot initialized successfully")

def main():
    keep_alive()
    request = HTTPXRequest(connection_pool_size=8, connect_timeout=60.0, read_timeout=60.0)
    app = Application.builder().token(TOKEN).request(request).defaults(Defaults(tzinfo=IST)).post_init(post_init).build()

    # Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("feedback", feedback_handler))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("subjects", subjects_command))
    app.add_handler(CommandHandler("attendance", attendance_command))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    
    app.add_handler(MessageHandler(filters.Regex("^📂 More Options"), handle_navigation))
    app.add_handler(MessageHandler(filters.Regex("^🔙 Back"), handle_navigation))
    app.add_handler(MessageHandler(filters.Regex("^📤 Export"), export_data))
    app.add_handler(MessageHandler(filters.Regex("^📥 Import"), import_request))
    app.add_handler(MessageHandler(filters.Document.MimeType("application/json"), handle_import_file))
    app.add_handler(MessageHandler(filters.Regex("^🗑️ Delete Class"), delete_menu))
    app.add_handler(CallbackQueryHandler(handle_kill, pattern="^kill_"))
    
    # NEW: Added View All Subjects Handler
    app.add_handler(MessageHandler(filters.Regex("^📚 All Subjects"), view_all_subjects))

    app.add_handler(MessageHandler(filters.Regex("^📸 AI Auto-Schedule"), prompt_image_upload)) 
    app.add_handler(MessageHandler(filters.Regex("^📊 Attendance"), view_attendance_stats)) 
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Regex("^📅 View Schedule"), view_schedule_handler))
    app.add_handler(CallbackQueryHandler(mark_attendance, pattern="^att_"))

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
            EDIT_NEW_VALUE: [MessageHandler(txt_filter, edit_save)]
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
            SELECT_OFFSET: [CallbackQueryHandler(wizard_offset, pattern="^offset_")],
            MSG_TYPE_CHOICE: [CallbackQueryHandler(wizard_msg_choice, pattern="^msg_")],
            INPUT_MANUAL_MSG: [MessageHandler(txt_filter, wizard_manual_msg)]
        },
        fallbacks=[MessageHandler(filters.Regex(MENU_REGEX), cancel_wizard)],
        conversation_timeout=300
    ))

    app.add_handler(CallbackQueryHandler(handle_expired))
    
    # Global error handler
    app.add_error_handler(error_handler)

    print("✅ TITAN CLOUD BOT ONLINE")
    # drop_pending_updates=True prevents conflict with previous instances
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()