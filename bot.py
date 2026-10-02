import os
import sqlite3
import telebot
from telebot import types

# ============== تنظیمات ==============
# این‌ها از "Variables" پروژه‌ی ریلوی خونده میشن؛ اینجا مقداردهی نکن.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ATHENA_ID_RAW = os.environ.get("ATHENA_ID")

if not BOT_TOKEN:
    raise RuntimeError("متغیر محیطی BOT_TOKEN تنظیم نشده.")
if not ATHENA_ID_RAW:
    raise RuntimeError("متغیر محیطی ATHENA_ID تنظیم نشده.")
ATHENA_ID = int(ATHENA_ID_RAW)

# مسیر فایل دیتابیس؛ روی ریلوی باید داخل یه Volume قرار بگیره تا با هر دیپلوی پاک نشه
DB_FILE = os.environ.get("DB_FILE", "bot.db")
# =======================================

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

DEFAULT_SETTINGS = {
    "welcome_text": "به ربات پیام ناشناس خوش اومدی! 🎭\nهر پیامی بفرستی (متن، عکس، ویس، فیلم، استیکر، گیف و ...) بصورت کاملا ناشناس ارسال میشه.",
    "confirm_text": "پیامت ارسال شد ✅",
    "anon_header": "💌 آتنا جوابتو داد:",
    "broadcast_header": "📢 پیام همگانی از طرف آتنا:",
    "off_text": "⛔️ ربات فعلاً خاموشه، لطفاً بعداً دوباره امتحان کن.",
    "bot_enabled": "1",
}


# ================== دیتابیس (SQLite) ==================
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")


def init_db():
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS blocked_users (
            id INTEGER PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS filtered_words (
            word TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS links (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            to_chat_id INTEGER NOT NULL,
            to_message_id INTEGER,
            PRIMARY KEY (chat_id, message_id)
        )
    """)
    for k, v in DEFAULT_SETTINGS.items():
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()


init_db()

# وضعیت موقت آتنا (منتظر گرفتن ورودی برای کدوم اکشنه) - فقط تو حافظه، نیازی به دیتابیس نداره
athena_state = {"waiting_for": None}


# ---------- توابع کمکی تنظیمات ----------
def get_setting(key, default=None):
    cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_setting(key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def bot_is_enabled():
    return get_setting("bot_enabled", "1") == "1"


def set_bot_enabled(flag):
    set_setting("bot_enabled", "1" if flag else "0")


# ---------- توابع کمکی لینک پیام‌ها ----------
def get_link(chat_id, message_id):
    cur = conn.execute(
        "SELECT to_chat_id, to_message_id FROM links WHERE chat_id = ? AND message_id = ?",
        (chat_id, message_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"to_chat_id": row[0], "to_message_id": row[1]}


def set_link(chat_id, message_id, to_chat_id, to_message_id):
    conn.execute(
        "INSERT INTO links (chat_id, message_id, to_chat_id, to_message_id) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id, message_id) DO UPDATE SET to_chat_id = excluded.to_chat_id, "
        "to_message_id = excluded.to_message_id",
        (chat_id, message_id, to_chat_id, to_message_id),
    )
    conn.commit()


# ---------- توابع کمکی کاربرا ----------
def register_user(user):
    conn.execute(
        "INSERT OR IGNORE INTO users (id, first_name, username) VALUES (?, ?, ?)",
        (user.id, user.first_name, user.username),
    )
    conn.commit()


def get_all_user_ids(exclude_blocked=True):
    if exclude_blocked:
        cur = conn.execute("SELECT id FROM users WHERE id NOT IN (SELECT id FROM blocked_users)")
    else:
        cur = conn.execute("SELECT id FROM users")
    return [row[0] for row in cur.fetchall()]


def get_all_users_ordered():
    cur = conn.execute("SELECT id, first_name, username FROM users ORDER BY id")
    return cur.fetchall()


def count_users():
    cur = conn.execute("SELECT COUNT(*) FROM users")
    return cur.fetchone()[0]


# ---------- توابع کمکی بلاک ----------
def is_blocked(user_id):
    cur = conn.execute("SELECT 1 FROM blocked_users WHERE id = ?", (int(user_id),))
    return cur.fetchone() is not None


def block_user(user_id):
    conn.execute("INSERT OR IGNORE INTO blocked_users (id) VALUES (?)", (int(user_id),))
    conn.commit()


def count_blocked():
    cur = conn.execute("SELECT COUNT(*) FROM blocked_users")
    return cur.fetchone()[0]


def unblock_all():
    count = count_blocked()
    conn.execute("DELETE FROM blocked_users")
    conn.commit()
    return count


# ---------- توابع کمکی فیلتر کلمات ----------
def add_filtered_words(raw_text):
    parts = [w.strip().lower() for w in raw_text.replace(",", "\n").split("\n")]
    words = [w for w in parts if w]
    added = []
    for w in words:
        cur = conn.execute("SELECT 1 FROM filtered_words WHERE word = ?", (w,))
        if not cur.fetchone():
            conn.execute("INSERT INTO filtered_words (word) VALUES (?)", (w,))
            added.append(w)
    conn.commit()
    return added


def remove_filtered_word(raw_text):
    word = raw_text.strip().lower()
    cur = conn.execute("DELETE FROM filtered_words WHERE word = ?", (word,))
    conn.commit()
    return cur.rowcount > 0, word


def list_filtered_words():
    cur = conn.execute("SELECT word FROM filtered_words ORDER BY rowid")
    return [row[0] for row in cur.fetchall()]


def contains_filtered_word(text):
    if not text:
        return False
    low = text.lower()
    for w in list_filtered_words():
        if w and w in low:
            return True
    return False


def safe_send(chat_id, text, reply_to_message_id=None, reply_markup=None):
    # اگه پیامی که میخوایم روش ریپلای کنیم پاک شده باشه یا پیدا نشه،
    # به‌جای کرش کردن، پیام رو بدون ریپلای میفرستیم
    try:
        return bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id, reply_markup=reply_markup)
    except Exception:
        return bot.send_message(chat_id, text, reply_markup=reply_markup)


def safe_copy(to_chat_id, from_chat_id, message_id, caption=None, reply_to_message_id=None, reply_markup=None):
    try:
        return bot.copy_message(
            to_chat_id, from_chat_id, message_id,
            caption=caption, reply_to_message_id=reply_to_message_id, reply_markup=reply_markup,
        )
    except Exception:
        return bot.copy_message(to_chat_id, from_chat_id, message_id, caption=caption, reply_markup=reply_markup)


# انواعی که تلگرام روشون کپشن (caption) رو پشتیبانی میکنه
CAPTIONABLE_TYPES = {"photo", "video", "document", "audio", "animation", "voice"}

# همه نوع پیامی که ربات پشتیبانی میکنه
SUPPORTED_TYPES = [
    "text", "photo", "video", "voice", "document",
    "sticker", "audio", "video_note", "animation",
]


def forward_message(message, to_chat_id, to_message_id, header, reply_markup=None):
    """
    پیام (از هر نوعی که باشه) رو با یه هدر مشخص به مقصد میفرسته
    و لیست آیدی پیام‌هایی که ارسال شدن رو برمیگردونه (برای لینک کردن)
    """
    content_type = message.content_type
    sent_ids = []

    if content_type == "text":
        text = header + "\n\n" + message.text
        sent = safe_send(to_chat_id, text, reply_to_message_id=to_message_id, reply_markup=reply_markup)
        sent_ids.append(sent.message_id)

    elif content_type in CAPTIONABLE_TYPES:
        caption = header
        if message.caption:
            caption += "\n\n" + message.caption
        sent = safe_copy(to_chat_id, message.chat.id, message.message_id,
                          caption=caption, reply_to_message_id=to_message_id, reply_markup=reply_markup)
        sent_ids.append(sent.message_id)

    else:
        # مثل استیکر و ویدیو-نوت که کپشن رو پشتیبانی نمیکنن؛
        # اول یه پیام هدر جدا میفرستیم بعد خودِ محتوا رو
        header_msg = safe_send(to_chat_id, header, reply_to_message_id=to_message_id, reply_markup=reply_markup)
        sent_ids.append(header_msg.message_id)
        content_msg = safe_copy(to_chat_id, message.chat.id, message.message_id,
                                 reply_to_message_id=header_msg.message_id)
        sent_ids.append(content_msg.message_id)

    return sent_ids


def action_buttons(user_id, user_message_id):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🚫 بلاک کردن این کاربر", callback_data=f"block:{user_id}"),
        types.InlineKeyboardButton("👀 سین زدن", callback_data=f"seen:{user_id}:{user_message_id}"),
    )
    return kb


def update_button_in_markup(call, prefix, new_text, new_callback):
    """
    از رو کیبورد شیشه‌ای فعلیِ پیام، فقط دکمه‌ای که callback_data ش با prefix شروع میشه رو
    عوض میکنه و بقیه‌ی دکمه‌ها رو دست‌نخورده نگه میداره
    """
    kb = types.InlineKeyboardMarkup()
    for row in call.message.reply_markup.keyboard:
        new_row = []
        for btn in row:
            if btn.callback_data and btn.callback_data.startswith(prefix):
                new_row.append(types.InlineKeyboardButton(new_text, callback_data=new_callback))
            else:
                new_row.append(types.InlineKeyboardButton(btn.text, callback_data=btn.callback_data))
        kb.row(*new_row)
    return kb


# ================== منوی آتنا (کیبورد) ==================
BTN_SETTINGS_FOLDER = "📁 تغییر تکست های ربات"
BTN_STATS = "📊 آمار"
BTN_BLOCK_MANAGE = "🚫 مدیریت کاربرای بلاک شده"
BTN_BROADCAST = "📢 ارسال پیام همگانی"
BTN_FILTER_MANAGE = "🔍 مدیریت کلمات فیلتر شده"
BTN_TOGGLE_ON = "🔴 خاموش کردن ربات"    # وقتی روشنه نشون داده میشه
BTN_TOGGLE_OFF = "🟢 روشن کردن ربات"   # وقتی خاموشه نشون داده میشه

BTN_WELCOME = "✏️ تغییر تکست خوش آمدگویی"
BTN_CONFIRM = "✏️ تغییر تکست تایید پیام"
BTN_ANON = "✏️ تغییر تکست ناشناس"
BTN_BROADCAST_TEXT = "✏️ تغییر تکست پیام همگانی"
BTN_OFF_TEXT = "✏️ تغییر تکست خاموش بودن ربات"
BTN_BACK = "🔙 بازگشت"

BTN_UNBLOCK_ALL = "🔓 آنبلاک کردن همه"

BTN_FILTER_ADD = "➕ افزودن کلمه فیلتر"
BTN_FILTER_REMOVE = "➖ حذف کلمه فیلتر"
BTN_FILTER_LIST = "📋 لیست کلمات فیلتر شده"

MENU_BUTTON_TEXTS = {
    BTN_SETTINGS_FOLDER, BTN_STATS, BTN_BLOCK_MANAGE, BTN_BROADCAST, BTN_FILTER_MANAGE,
    BTN_TOGGLE_ON, BTN_TOGGLE_OFF,
    BTN_WELCOME, BTN_CONFIRM, BTN_ANON, BTN_BROADCAST_TEXT, BTN_OFF_TEXT,
    BTN_UNBLOCK_ALL, BTN_FILTER_ADD, BTN_FILTER_REMOVE, BTN_FILTER_LIST,
}

SETTING_LABELS = {
    "welcome_text": "خوش آمدگویی",
    "confirm_text": "تایید پیام",
    "anon_header": "ناشناس",
    "broadcast_header": "پیام همگانی",
    "off_text": "خاموش بودن ربات",
}


def main_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(BTN_SETTINGS_FOLDER, BTN_STATS)
    kb.row(BTN_BLOCK_MANAGE, BTN_BROADCAST)
    toggle_label = BTN_TOGGLE_ON if bot_is_enabled() else BTN_TOGGLE_OFF
    kb.row(BTN_FILTER_MANAGE, toggle_label)
    return kb


def settings_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(BTN_WELCOME)
    kb.row(BTN_CONFIRM)
    kb.row(BTN_ANON)
    kb.row(BTN_BROADCAST_TEXT)
    kb.row(BTN_OFF_TEXT)
    kb.row(BTN_BACK)
    return kb


def block_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(BTN_UNBLOCK_ALL)
    kb.row(BTN_BACK)
    return kb


def filter_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(BTN_FILTER_ADD)
    kb.row(BTN_FILTER_REMOVE)
    kb.row(BTN_FILTER_LIST)
    kb.row(BTN_BACK)
    return kb


def broadcast_wait_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(BTN_BACK)
    return kb


def send_main_menu(chat_id, text="منوی اصلی:"):
    athena_state["waiting_for"] = None
    bot.send_message(chat_id, text, reply_markup=main_menu_keyboard())


def send_settings_menu(chat_id):
    bot.send_message(chat_id, "یکی از گزینه‌ها رو انتخاب کن:", reply_markup=settings_menu_keyboard())


def send_block_menu(chat_id):
    bot.send_message(chat_id, f"🚫 تعداد کاربرای بلاک‌شده: {count_blocked()}", reply_markup=block_menu_keyboard())


def unblock_all_action(chat_id):
    count = unblock_all()
    bot.send_message(
        chat_id,
        f"✅ همه ({count} نفر) آنبلاک شدن.\n🚫 تعداد کاربرای بلاک‌شده الان: 0",
        reply_markup=block_menu_keyboard(),
    )


def send_filter_menu(chat_id):
    bot.send_message(chat_id, "مدیریت کلمات فیلتر شده:", reply_markup=filter_menu_keyboard())


def send_filter_list(chat_id):
    words = list_filtered_words()
    if not words:
        bot.send_message(chat_id, "لیست کلمات فیلتر شده خالیه.", reply_markup=filter_menu_keyboard())
        return
    text = "📋 کلمات فیلتر شده:\n\n" + "\n".join(f"{i + 1}. {w}" for i, w in enumerate(words))
    bot.send_message(chat_id, text, reply_markup=filter_menu_keyboard())


def add_filtered_words_action(chat_id, raw_text):
    added = add_filtered_words(raw_text)
    if added:
        bot.send_message(chat_id, "✅ اضافه شد: " + "، ".join(added))
    else:
        bot.send_message(chat_id, "این کلمه(ها) از قبل تو لیست بودن یا معتبر نبودن.")


def remove_filtered_word_action(chat_id, raw_text):
    removed, word = remove_filtered_word(raw_text)
    if removed:
        bot.send_message(chat_id, f"✅ کلمه «{word}» حذف شد.")
    else:
        bot.send_message(chat_id, f"❗️کلمه «{word}» تو لیست فیلتر نبود.")


def toggle_bot_action(chat_id):
    set_bot_enabled(not bot_is_enabled())
    status = "🟢 روشن" if bot_is_enabled() else "🔴 خاموش"
    bot.send_message(chat_id, f"وضعیت ربات الان: {status}", reply_markup=main_menu_keyboard())


def ask_new_text(chat_id, setting_key):
    athena_state["waiting_for"] = setting_key
    current = get_setting(setting_key, "")
    label = SETTING_LABELS[setting_key]
    bot.send_message(
        chat_id,
        f"متن فعلی «{label}»:\n\n{current}\n\nحالا متن جدید رو بفرست 👇\n(برای انصراف، بازگشت رو بزن)",
        reply_markup=settings_menu_keyboard(),
    )


def start_broadcast(chat_id):
    athena_state["waiting_for"] = "broadcast"
    bot.send_message(
        chat_id,
        "محتوایی که میخوای برای همه‌ی کسایی که ربات رو استارت زدن ارسال بشه رو بفرست (متن، عکس، ویس، فیلم و ...):",
        reply_markup=broadcast_wait_keyboard(),
    )


def do_broadcast(message):
    chat_id = message.chat.id
    header = get_setting("broadcast_header")
    targets = get_all_user_ids(exclude_blocked=True)

    success = 0
    fail = 0
    for uid in targets:
        try:
            sent_ids = forward_message(message, uid, None, header)
            for sid in sent_ids:
                set_link(uid, sid, ATHENA_ID, message.message_id)
            success += 1
        except Exception:
            fail += 1

    summary = f"📢 پیام همگانی ارسال شد.\n✅ موفق: {success}"
    if fail:
        summary += f"\n❌ ناموفق: {fail}"
    bot.send_message(chat_id, summary, reply_markup=main_menu_keyboard())


def format_users_list():
    users = get_all_users_ordered()
    if not users:
        return "هنوز هیچ کاربری ربات رو استارت نزده."
    lines = []
    for i, (uid, first_name, username) in enumerate(users, start=1):
        uname = f"@{username}" if username else "بدون یوزرنیم"
        name = first_name or "-"
        lines.append(f"{i}. {name} | {uname} | ایدی: {uid}")
    return f"👥 تعداد کل: {len(users)}\n\n" + "\n".join(lines)


def send_stats(chat_id):
    text = format_users_list()
    # اگه لیست خیلی طولانی بود، تیکه‌تیکه بفرست تا از سقف تلگرام رد نشه
    chunk_size = 3500
    if len(text) <= chunk_size:
        bot.send_message(chat_id, text)
        return
    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > chunk_size:
            bot.send_message(chat_id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        bot.send_message(chat_id, chunk)


SETTINGS_KEYS = ("welcome_text", "confirm_text", "anon_header", "broadcast_header", "off_text")


def handle_athena_menu(message):
    """
    اگه پیام آتنا مربوط به منو/تنظیمات باشه، پردازشش میکنه و True برمیگردونه.
    اگه ربطی به منو نداشت، False برمیگردونه تا مثل پیام عادی (ریپلای به کاربر) پردازش بشه.
    """
    chat_id = message.chat.id
    text = message.text if message.content_type == "text" else None

    waiting = athena_state["waiting_for"]

    if waiting:
        if text == BTN_BACK:
            athena_state["waiting_for"] = None
            if waiting in SETTINGS_KEYS:
                send_settings_menu(chat_id)
            elif waiting in ("filter_add", "filter_remove"):
                send_filter_menu(chat_id)
            else:  # broadcast
                send_main_menu(chat_id)
            return True

        elif text in MENU_BUTTON_TEXTS:
            # به‌جای ورودی مورد انتظار، یکی از دکمه‌های منو زده شده؛ حالت قبلی رو کنسل میکنیم
            athena_state["waiting_for"] = None
            # کد ادامه پیدا میکنه و پایین به‌عنوان یه ناوبری عادی پردازش میشه

        else:
            if waiting == "broadcast":
                do_broadcast(message)
                athena_state["waiting_for"] = None
                return True

            if waiting == "filter_add":
                if message.content_type != "text":
                    bot.send_message(chat_id, "لطفا فقط متن بفرست.")
                    return True
                add_filtered_words_action(chat_id, message.text)
                athena_state["waiting_for"] = None
                send_filter_menu(chat_id)
                return True

            if waiting == "filter_remove":
                if message.content_type != "text":
                    bot.send_message(chat_id, "لطفا فقط متن بفرست.")
                    return True
                remove_filtered_word_action(chat_id, message.text)
                athena_state["waiting_for"] = None
                send_filter_menu(chat_id)
                return True

            # در غیر این صورت یعنی داره یکی از تکست‌های ثابت رو تغییر میده
            if message.content_type != "text":
                bot.send_message(chat_id, "لطفا فقط متن بفرست.")
                return True
            set_setting(waiting, message.text)
            bot.send_message(chat_id, f"متن «{SETTING_LABELS[waiting]}» با موفقیت تغییر کرد ✅")
            send_settings_menu(chat_id)
            athena_state["waiting_for"] = None
            return True

    # ناوبری عادی منو (یا وقتی حالت انتظار با زدن یه دکمه‌ی دیگه کنسل شد)
    if text == BTN_SETTINGS_FOLDER:
        send_settings_menu(chat_id)
        return True
    if text == BTN_STATS:
        send_stats(chat_id)
        return True
    if text == BTN_BLOCK_MANAGE:
        send_block_menu(chat_id)
        return True
    if text == BTN_BROADCAST:
        start_broadcast(chat_id)
        return True
    if text == BTN_FILTER_MANAGE:
        send_filter_menu(chat_id)
        return True
    if text in (BTN_TOGGLE_ON, BTN_TOGGLE_OFF):
        toggle_bot_action(chat_id)
        return True
    if text == BTN_BACK:
        send_main_menu(chat_id)
        return True
    if text == BTN_WELCOME:
        ask_new_text(chat_id, "welcome_text")
        return True
    if text == BTN_CONFIRM:
        ask_new_text(chat_id, "confirm_text")
        return True
    if text == BTN_ANON:
        ask_new_text(chat_id, "anon_header")
        return True
    if text == BTN_BROADCAST_TEXT:
        ask_new_text(chat_id, "broadcast_header")
        return True
    if text == BTN_OFF_TEXT:
        ask_new_text(chat_id, "off_text")
        return True
    if text == BTN_UNBLOCK_ALL:
        unblock_all_action(chat_id)
        return True
    if text == BTN_FILTER_ADD:
        athena_state["waiting_for"] = "filter_add"
        bot.send_message(
            chat_id,
            "کلمه(های) مورد نظر رو بفرست (میتونی چندتا رو با کاما یا خط جدید جدا کنی):",
            reply_markup=filter_menu_keyboard(),
        )
        return True
    if text == BTN_FILTER_REMOVE:
        athena_state["waiting_for"] = "filter_remove"
        bot.send_message(chat_id, "کلمه‌ای که میخوای از لیست فیلتر حذف بشه رو بفرست:", reply_markup=filter_menu_keyboard())
        return True
    if text == BTN_FILTER_LIST:
        send_filter_list(chat_id)
        return True

    return False


# ---------- هندلر /start ----------
@bot.message_handler(commands=["start"])
def handle_start(message):
    if message.chat.id == ATHENA_ID:
        send_main_menu(message.chat.id, "به پنل ربات پیام ناشناس خوش اومدی آتنا 🌟")
    else:
        register_user(message.from_user)
        bot.send_message(message.chat.id, get_setting("welcome_text"))


# ---------- هندلر همه‌ی انواع پیام ----------
@bot.message_handler(content_types=SUPPORTED_TYPES)
def handle_any_message(message):
    if message.content_type == "text" and message.text.startswith("/"):
        return  # کامندها رو نادیده بگیر

    if message.chat.id == ATHENA_ID:
        if handle_athena_menu(message):
            return
        handle_athena_message(message)
    else:
        handle_user_message(message)


def handle_user_message(message):
    chat_id = message.chat.id
    register_user(message.from_user)

    if not bot_is_enabled():
        bot.send_message(chat_id, get_setting("off_text"))
        return

    if is_blocked(chat_id):
        bot.send_message(chat_id, "⛔️ شما توسط ادمین بلاک شده‌اید و امکان ارسال پیام ندارید.")
        return

    content_text = message.text if message.content_type == "text" else message.caption
    if contains_filtered_word(content_text):
        bot.send_message(chat_id, "❌ پیام شما شامل کلمات غیرمجاز بود و ارسال نشد.")
        return

    to_chat_id = ATHENA_ID
    to_message_id = None

    if message.reply_to_message:
        link = get_link(chat_id, message.reply_to_message.message_id)
        if link:
            to_chat_id = link["to_chat_id"]
            to_message_id = link["to_message_id"]

    reply_markup = action_buttons(chat_id, message.message_id) if to_chat_id == ATHENA_ID else None
    sent_ids = forward_message(message, to_chat_id, to_message_id, "📩 پیام ناشناس جدید:", reply_markup=reply_markup)

    # لینک میکنیم که اگه آتنا روی هرکدوم از این پیام‌ها ریپلای بزنه، جواب باید بره واسه همین یوزر
    # و دقیقا روی همین پیام کاربر ریپلای بشه
    for sid in sent_ids:
        set_link(to_chat_id, sid, chat_id, message.message_id)

    bot.send_message(chat_id, get_setting("confirm_text"), reply_to_message_id=message.message_id)


def handle_athena_message(message):
    chat_id = message.chat.id

    if not message.reply_to_message:
        bot.send_message(chat_id, "❗️برای ارسال پاسخ باید روی پیام همون کاربر ریپلای بزنی.")
        return

    link = get_link(chat_id, message.reply_to_message.message_id)
    if not link:
        bot.send_message(chat_id, "❗️این پیام قابل پاسخگویی نیست.")
        return

    to_chat_id = link["to_chat_id"]
    to_message_id = link["to_message_id"]

    sent_ids = forward_message(message, to_chat_id, to_message_id, get_setting("anon_header"))

    # لینک میکنیم که اگه کاربر روی هرکدوم از این پیام‌ها ریپلای بزنه، جواب باید بره واسه آتنا
    # و دقیقا روی همین پیام آتنا ریپلای بشه (زنجیره ادامه پیدا میکنه تا ابد)
    for sid in sent_ids:
        set_link(to_chat_id, sid, chat_id, message.message_id)

    bot.send_message(chat_id, "پیام ارسال شد ✅", reply_to_message_id=message.message_id)


# ---------- هندلر دکمه شیشه‌ای بلاک ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith("block:"))
def handle_block_callback(call):
    if call.message.chat.id != ATHENA_ID:
        bot.answer_callback_query(call.id)
        return

    user_id = call.data.split(":", 1)[1]

    if is_blocked(user_id):
        bot.answer_callback_query(call.id, "این کاربر قبلاً بلاک شده بود.")
        return

    block_user(user_id)
    bot.answer_callback_query(call.id, "🚫 کاربر بلاک شد.")
    try:
        kb = update_button_in_markup(call, "block:", "✅ کاربر بلاک شد", "noop")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data == "noop")
def handle_noop_callback(call):
    bot.answer_callback_query(call.id, "این کاربر قبلاً بلاک شده.")


# ---------- هندلر دکمه شیشه‌ای سین زدن ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith("seen:"))
def handle_seen_callback(call):
    if call.message.chat.id != ATHENA_ID:
        bot.answer_callback_query(call.id)
        return

    parts = call.data.split(":")
    user_id = int(parts[1])
    user_message_id = int(parts[2])

    try:
        bot.send_message(user_id, "👀 آتنا این پیامت رو دید.", reply_to_message_id=user_message_id)
        bot.answer_callback_query(call.id, "سین زده شد ✅")
    except Exception:
        bot.answer_callback_query(call.id, "ارسال سین با خطا مواجه شد.")
        return

    try:
        kb = update_button_in_markup(call, "seen:", "✅ سین شد", "noop_seen")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data == "noop_seen")
def handle_noop_seen_callback(call):
    bot.answer_callback_query(call.id, "قبلاً برای این پیام سین زده شده.")


if __name__ == "__main__":
    print("ربات روشن شد ...")
    bot.infinity_polling()
