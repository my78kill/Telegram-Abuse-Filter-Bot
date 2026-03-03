import os
import logging
import re
import motor.motor_asyncio
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.constants import ParseMode
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import telegram.error
from dotenv import load_dotenv

load_dotenv()

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("OWNER_ID", "7563434309"))  # fallback optional

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- DB ----------------
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["AntiAbuseBot"]
users_collection = db["users"]
groups_collection = db["groups"]
authorized_users_collection = db["authorized_users"]

ALLOWED_USERS = {OWNER_ID, 7717913705}  # same as before
ABUSE_FILE = "abuse.txt"
USER_WARNINGS = {}

WARNING_MESSAGES = {
    1: "⚠️ {mention}, please keep it respectful!",
    2: "⛔ {mention}, second warning! Watch your words.",
    3: "🚦 {mention}, you're on thin ice! Final warning.",
    4: "🛑 {mention}, stop now, or you will be muted!",
    5: "🚷 {mention}, last chance before removal!",
    6: "🔇 {mention}, you've been muted for repeated violations!",
    7: "🚫 {mention}, you’ve crossed the line. Consider this a final notice!",
    8: "☢️ {mention}, next time, you're banned!",
    9: "⚰️ {mention}, you’re getting removed now!",
    10: "🔥 {mention}, you are banned from this group!"
}

def load_abusive_words():
    if os.path.exists(ABUSE_FILE):
        try:
            with open(ABUSE_FILE, "r", encoding="utf-8") as f:
                return set(word.strip().lower() for word in f if word.strip())
        except Exception as e:
            logger.error(f"Failed to load abusive words: {e}")
    return set()

ABUSIVE_WORDS = load_abusive_words()

# ---------------- TELEGRAM APP ----------------
application = Application.builder().token(BOT_TOKEN).build()

# ---------------- CHECKS ----------------
async def is_admin(update: Update, user_id: int):
    if user_id in ALLOWED_USERS:
        return True
    try:
        chat_member = await update.effective_chat.get_member(user_id)
        return chat_member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except telegram.error.BadRequest:
        return False

async def is_owner(update: Update, user_id: int):
    if user_id in ALLOWED_USERS:
        return True
    try:
        chat_member = await update.effective_chat.get_member(user_id)
        return chat_member.status == ChatMember.OWNER
    except telegram.error.BadRequest:
        return False

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚨 **Anti-Abuse Bot Active!** 🚨\n\n"
        "Use `/admin on` to activate abuse filtering.\n"
        "Use `/admin off` to disable it.\n\n"
        "✅ Only group **owners** or **allowed users** can toggle filtering.\n"
        "✅ Only admins or allowed users can `/auth` or `/unauth` members."
    )

async def handle_new_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    existing_group = await groups_collection.find_one({"group_id": chat_id})
    if not existing_group:
        await groups_collection.insert_one({"group_id": chat_id, "filtering": True})
    await update.message.reply_text(
        "✅ This group is now protected!\n\n"
        "Please give me delete message permission."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    user = update.message.from_user

    group_settings = await groups_collection.find_one({"group_id": chat_id})
    if group_settings and not group_settings.get("filtering", True):
        return

    authorized = await authorized_users_collection.find_one(
        {"group_id": chat_id, "user_id": user.id}
    )
    if authorized:
        return

    message_words = re.findall(r'\b\w+\b', update.message.text.lower())

    if any(word in ABUSIVE_WORDS for word in message_words):
        try:
            await update.message.delete()
        except telegram.error.BadRequest:
            logger.warning("Delete failed")

        mention = f"[{user.first_name}](tg://user?id={user.id})"
        warning_count = USER_WARNINGS.get(user.id, 0) + 1
        USER_WARNINGS[user.id] = warning_count

        warning_text = WARNING_MESSAGES.get(
            warning_count,
            "⚠️ {mention}, please keep it respectful!"
        ).format(mention=mention)

        await update.message.reply_text(warning_text, parse_mode=ParseMode.MARKDOWN)

async def admin_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /admin on OR /admin off")
        return

    command = context.args[0].lower()
    chat_id = update.message.chat_id
    sender_id = update.message.from_user.id

    if not await is_owner(update, sender_id):
        await update.message.reply_text("🚫 Only group owner can use this!")
        return

    filtering = command == "on"
    await groups_collection.update_one(
        {"group_id": chat_id},
        {"$set": {"filtering": filtering}},
        upsert=True
    )

    await update.message.reply_text(
        "✅ Filtering ENABLED" if filtering else "❌ Filtering DISABLED"
    )

async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to user to authorize.")
        return

    chat_id = update.message.chat_id
    admin_id = update.message.from_user.id
    user_id = update.message.reply_to_message.from_user.id
    user_name = update.message.reply_to_message.from_user.first_name

    if not await is_admin(update, admin_id):
        await update.message.reply_text("Admins only.")
        return

    await authorized_users_collection.update_one(
        {"group_id": chat_id, "user_id": user_id},
        {"$set": {"user_name": user_name}},
        upsert=True
    )

    await update.message.reply_text(
        f"✅ [{user_name}](tg://user?id={user_id}) authorized.",
        parse_mode="Markdown"
    )

async def unauth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to user to unauthorize.")
        return

    chat_id = update.message.chat_id
    admin_id = update.message.from_user.id
    user_id = update.message.reply_to_message.from_user.id

    if not await is_admin(update, admin_id):
        await update.message.reply_text("Admins only.")
        return

    await authorized_users_collection.delete_one(
        {"group_id": chat_id, "user_id": user_id}
    )

    await update.message.reply_text("❌ User unauthorized.")

async def block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Not allowed.")
        return

    chat_id = update.message.chat_id
    await update.message.reply_text("🚫 Blocking this group.")
    await update.message.bot.leave_chat(chat_id)

# ---------------- HANDLERS ----------------
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("admin", admin_control))
application.add_handler(CommandHandler("auth", auth))
application.add_handler(CommandHandler("unauth", unauth))
application.add_handler(CommandHandler("block", block))
application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_group))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ---------------- FLASK ----------------
flask_app = Flask(__name__)

@flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "ok"

@flask_app.route("/")
def home():
    return "Bot is Live 🚀"

async def set_webhook():
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        await application.bot.set_webhook(f"{render_url}/{BOT_TOKEN}")

# ---------------- MAIN ----------------
if __name__ == "__main__":
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(set_webhook())
    flask_app.run(host="0.0.0.0", port=10000)
