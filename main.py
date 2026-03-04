import os
import re
import logging
import motor.motor_asyncio
from dotenv import load_dotenv
from telegram import Update, ChatMember
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- LOAD ENV ----------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("OWNER_ID", "7563434309"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- DATABASE ----------------
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["AntiAbuseBot"]
groups_collection = db["groups"]
authorized_users_collection = db["authorized_users"]

ALLOWED_USERS = {OWNER_ID, 7717913705}
USER_WARNINGS = {}
ABUSE_FILE = "abuse.txt"

WARNING_MESSAGES = {
    1: "⚠️ {mention}, please keep it respectful!",
    2: "⛔ {mention}, second warning!",
    3: "🚦 {mention}, final warning!",
    4: "🛑 {mention}, muted next!",
    5: "🚫 {mention}, you will be removed!",
    6: "🔥 {mention}, banned!"
}

# ---------------- LOAD ABUSE WORDS ----------------
def load_abusive_words():
    if os.path.exists(ABUSE_FILE):
        with open(ABUSE_FILE, "r", encoding="utf-8") as f:
            return set(word.strip().lower() for word in f if word.strip())
    return set()

ABUSIVE_WORDS = load_abusive_words()

# ---------------- PERMISSION CHECK ----------------

async def is_admin(chat, user_id):
    if user_id in ALLOWED_USERS:
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status in ["administrator", "creator"]
    except Exception:
        return False


async def is_owner(chat, user_id):
    if user_id in ALLOWED_USERS:
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status == "creator"
    except Exception:
        return False

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚨 Anti-Abuse Bot Active!\n\n"
        "Use /admin on OR /admin off\n"
        "Use /auth (reply)\n"
        "Use /unauth (reply)"
    )

async def admin_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /admin on OR /admin off")

    chat = update.effective_chat
    sender_id = update.effective_user.id

    # Allow all admins
    if not await is_admin(chat, sender_id):
        return await update.message.reply_text("🚫 Admins only.")

    filtering = context.args[0].lower() == "on"

    await groups_collection.update_one(
        {"group_id": chat.id},
        {"$set": {"filtering": filtering}},
        upsert=True
    )

    await update.message.reply_text(
        "✅ Filtering Enabled" if filtering else "❌ Filtering Disabled"
    )

async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to a user.")

    if not await is_admin(update.effective_chat, update.effective_user.id):
        return await update.message.reply_text("Admins only.")

    target = update.message.reply_to_message.from_user

    await authorized_users_collection.update_one(
        {"group_id": update.effective_chat.id, "user_id": target.id},
        {"$set": {"user_name": target.first_name}},
        upsert=True
    )

    await update.message.reply_text(
        f"✅ [{target.first_name}](tg://user?id={target.id}) authorized.",
        parse_mode=ParseMode.MARKDOWN
    )

async def unauth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return await update.message.reply_text("Reply to a user.")

    if not await is_admin(update.effective_chat, update.effective_user.id):
        return await update.message.reply_text("Admins only.")

    target = update.message.reply_to_message.from_user

    await authorized_users_collection.delete_one(
        {"group_id": update.effective_chat.id, "user_id": target.id}
    )

    await update.message.reply_text("❌ User unauthorized.")

async def block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("🚫 Not allowed.")

    await update.message.reply_text("Leaving group.")
    await context.bot.leave_chat(update.effective_chat.id)

# ---------------- MESSAGE FILTER ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    user = update.effective_user

    group_settings = await groups_collection.find_one({"group_id": chat.id})
    if group_settings and not group_settings.get("filtering", True):
        return

    authorized = await authorized_users_collection.find_one(
        {"group_id": chat.id, "user_id": user.id}
    )
    if authorized:
        return

    words = re.findall(r'\b\w+\b', update.message.text.lower())

    if any(word in ABUSIVE_WORDS for word in words):
        try:
            await update.message.delete()
        except:
            pass

        count = USER_WARNINGS.get(user.id, 0) + 1
        USER_WARNINGS[user.id] = count

        mention = f"[{user.first_name}](tg://user?id={user.id})"
        text = WARNING_MESSAGES.get(count, "⚠️ {mention} warned.").format(mention=mention)

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ---------------- APP SETUP ----------------
application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("admin", admin_control))
application.add_handler(CommandHandler("auth", auth))
application.add_handler(CommandHandler("unauth", unauth))
application.add_handler(CommandHandler("block", block))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ---------------- RUN WEBHOOK ----------------
if __name__ == "__main__":
    application.run_webhook(
        listen="0.0.0.0",
        port=10000,
        url_path=BOT_TOKEN,
        webhook_url=f"{RENDER_URL}/{BOT_TOKEN}",
    )
