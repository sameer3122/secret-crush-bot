import asyncio
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from telegram import (
    Update, LabeledPrice,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    PreCheckoutQueryHandler, CallbackQueryHandler,
    filters, ContextTypes,
)

TOKEN    = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ── Pricing (Telegram Stars) ────────────────────────────────────────────────
# 50 Stars ≈ $1 USD; creators cash out at ~$0.013/star
# Target: ~230 000 stars/month → $3 000 USD
REVEAL_PRICE  = 50    # stars – reveal who sent a confession
PREMIUM_PRICE = 200   # stars – 30-day unlimited premium
BOOST_PRICE   = 30    # stars – 10 extra sends (never expire)
FREE_DAILY    = 3     # free confessions per day

DB_PATH = "bot.db"

# ── Database ────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                first_name    TEXT,
                referred_by   INTEGER,
                is_premium    INTEGER DEFAULT 0,
                premium_until TEXT,
                extra_sends   INTEGER DEFAULT 0,
                sends_today   INTEGER DEFAULT 0,
                last_reset    TEXT,
                stars_spent   INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS confessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id    INTEGER,
                recipient_id INTEGER,
                message      TEXT,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                type       TEXT,
                stars      INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)


def ensure_user(user_id: int, username: str = None,
                first_name: str = None, referred_by: int = None) -> bool:
    with get_db() as conn:
        exists = conn.execute(
            "SELECT user_id FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (user_id, username, first_name, referred_by)"
                " VALUES (?,?,?,?)",
                (user_id, username, first_name, referred_by),
            )
            return True
    return False


def fetch_user(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def is_premium(user: dict) -> bool:
    if not user or not user["is_premium"]:
        return False
    if user["premium_until"]:
        if datetime.now() > datetime.fromisoformat(user["premium_until"]):
            with get_db() as conn:
                conn.execute(
                    "UPDATE users SET is_premium=0 WHERE user_id=?",
                    (user["user_id"],),
                )
            return False
    return True


def reset_daily_if_needed(user_id: int):
    today = datetime.now().date().isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_reset FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if row and row["last_reset"] != today:
            conn.execute(
                "UPDATE users SET sends_today=0, last_reset=? WHERE user_id=?",
                (today, user_id),
            )


def can_send(user_id: int) -> bool:
    reset_daily_if_needed(user_id)
    u = fetch_user(user_id)
    if not u:
        return False
    if is_premium(u) or u["extra_sends"] > 0:
        return True
    return u["sends_today"] < FREE_DAILY


def charge_send(user_id: int):
    u = fetch_user(user_id)
    if is_premium(u):
        return
    with get_db() as conn:
        if u["extra_sends"] > 0:
            conn.execute(
                "UPDATE users SET extra_sends=extra_sends-1 WHERE user_id=?",
                (user_id,),
            )
        else:
            conn.execute(
                "UPDATE users SET sends_today=sends_today+1 WHERE user_id=?",
                (user_id,),
            )


def save_confession(sender_id: int, recipient_id: int, message: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO confessions (sender_id, recipient_id, message)"
            " VALUES (?,?,?)",
            (sender_id, recipient_id, message),
        )
        return cur.lastrowid


def fetch_confession(confession_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM confessions WHERE id=?", (confession_id,)
        ).fetchone()
        return dict(row) if row else None


def log_payment(user_id: int, ptype: str, stars: int):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO payments (user_id, type, stars) VALUES (?,?,?)",
            (user_id, ptype, stars),
        )
        conn.execute(
            "UPDATE users SET stars_spent=stars_spent+? WHERE user_id=?",
            (stars, user_id),
        )


# ── Keyboards ────────────────────────────────────────────────────────────────

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Premium – 200 Stars/month", callback_data="buy_premium")],
        [InlineKeyboardButton("🚀 Boost Pack – 30 Stars (10 sends)", callback_data="buy_boost")],
        [InlineKeyboardButton("📊 My Stats", callback_data="my_stats")],
    ])


def reveal_keyboard(confession_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🔍 Reveal Sender – {REVEAL_PRICE} ⭐",
            callback_data=f"reveal_{confession_id}",
        )]
    ])


def upgrade_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Go Premium – unlimited sends", callback_data="buy_premium")],
        [InlineKeyboardButton("🚀 Boost Pack – 10 sends (30 ⭐)", callback_data="buy_boost")],
    ])


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    referred_by = None

    if context.args:
        try:
            referred_by = int(context.args[0])
        except ValueError:
            pass

    ensure_user(tg.id, tg.username, tg.first_name, referred_by)

    if referred_by and referred_by != tg.id:
        context.user_data["target"] = referred_by
        await update.message.reply_text(
            "💌 *Send your anonymous confession now.*\n\n"
            "_Your identity stays hidden. The recipient can pay ⭐ to reveal you._",
            parse_mode="Markdown",
        )
        return

    link = f"https://t.me/Secretcrushconfessionbot?start={tg.id}"
    await update.message.reply_text(
        f"👀 *Secret Crush Confession Bot*\n\n"
        f"Share your link to receive anonymous confessions:\n\n"
        f"`{link}`\n\n"
        f"🆓 Free: *{FREE_DAILY} confessions/day*\n"
        f"⭐ Premium: *Unlimited confessions – 200 Stars/month*\n"
        f"🔍 Reveal who confessed to you for *50 Stars*",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    ensure_user(tg.id, tg.username, tg.first_name)
    await context.bot.send_invoice(
        chat_id=tg.id,
        title="⭐ Secret Crush Premium",
        description="Unlimited confessions + priority delivery for 30 days.",
        payload="premium_monthly",
        currency="XTR",
        prices=[LabeledPrice("Premium – 30 days", PREMIUM_PRICE)],
    )


async def cmd_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    ensure_user(tg.id, tg.username, tg.first_name)
    await context.bot.send_invoice(
        chat_id=tg.id,
        title="🚀 Confession Boost Pack",
        description="10 extra confession sends – never expire.",
        payload="boost_pack",
        currency="XTR",
        prices=[LabeledPrice("Boost Pack – 10 sends", BOOST_PRICE)],
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    ensure_user(tg.id, tg.username, tg.first_name)
    reset_daily_if_needed(tg.id)
    u = fetch_user(tg.id)

    premium_ok  = is_premium(u)
    expiry_str  = u["premium_until"][:10] if u["premium_until"] else "—"

    with get_db() as conn:
        sent     = conn.execute("SELECT COUNT(*) FROM confessions WHERE sender_id=?",    (tg.id,)).fetchone()[0]
        received = conn.execute("SELECT COUNT(*) FROM confessions WHERE recipient_id=?", (tg.id,)).fetchone()[0]

    await update.message.reply_text(
        f"📊 *Your Stats*\n\n"
        f"Premium: {'✅ Active until ' + expiry_str if premium_ok else '❌ None'}\n"
        f"Extra sends: {u['extra_sends']}\n"
        f"Sends today: {u['sends_today']}/{FREE_DAILY} (free)\n"
        f"Total sent: {sent} | Received: {received}\n"
        f"Stars spent: {u['stars_spent']} ⭐",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    with get_db() as conn:
        total_users    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        premium_users  = conn.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
        total_conf     = conn.execute("SELECT COUNT(*) FROM confessions").fetchone()[0]
        total_stars    = conn.execute("SELECT COALESCE(SUM(stars),0) FROM payments").fetchone()[0]
        rev_premium    = conn.execute("SELECT COALESCE(SUM(stars),0) FROM payments WHERE type='premium'").fetchone()[0]
        rev_reveals    = conn.execute("SELECT COALESCE(SUM(stars),0) FROM payments WHERE type='reveal'").fetchone()[0]
        rev_boost      = conn.execute("SELECT COALESCE(SUM(stars),0) FROM payments WHERE type='boost'").fetchone()[0]

    usd = total_stars * 0.013
    await update.message.reply_text(
        f"📊 *Admin Dashboard*\n\n"
        f"👥 Users: {total_users}  |  ⭐ Premium: {premium_users}\n"
        f"💌 Confessions sent: {total_conf}\n\n"
        f"💰 *Revenue*\n"
        f"Total: {total_stars} ⭐ ≈ ${usd:,.2f} USD\n"
        f"  Premium subs: {rev_premium} ⭐\n"
        f"  Reveals:      {rev_reveals} ⭐\n"
        f"  Boost packs:  {rev_boost} ⭐",
        parse_mode="Markdown",
    )


# ── Message handler ───────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    ensure_user(tg.id, tg.username, tg.first_name)

    if "target" not in context.user_data:
        link = f"https://t.me/Secretcrushconfessionbot?start={tg.id}"
        await update.message.reply_text(
            f"Use a confession link to send a message, or share yours:\n`{link}`",
            parse_mode="Markdown",
        )
        return

    target_id = context.user_data["target"]

    if not can_send(tg.id):
        await update.message.reply_text(
            f"⚠️ *Daily limit reached!*\n\n"
            f"Free users get {FREE_DAILY} confessions/day.\n"
            "Upgrade to send unlimited confessions! ⭐",
            parse_mode="Markdown",
            reply_markup=upgrade_keyboard(),
        )
        return

    message_text = update.message.text
    confession_id = save_confession(tg.id, target_id, message_text)
    charge_send(tg.id)
    context.user_data.pop("target")

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"💌 *You received an anonymous confession:*\n\n_{message_text}_",
            parse_mode="Markdown",
            reply_markup=reveal_keyboard(confession_id),
        )
        await update.message.reply_text("✅ Confession sent anonymously!")
    except Exception:
        await update.message.reply_text(
            "❌ Delivery failed – the recipient must start the bot first."
        )


# ── Callback handler ──────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg   = query.from_user
    data = query.data

    if data == "buy_premium":
        await context.bot.send_invoice(
            chat_id=tg.id,
            title="⭐ Secret Crush Premium",
            description="Unlimited confessions + priority delivery for 30 days.",
            payload="premium_monthly",
            currency="XTR",
            prices=[LabeledPrice("Premium – 30 days", PREMIUM_PRICE)],
        )

    elif data == "buy_boost":
        await context.bot.send_invoice(
            chat_id=tg.id,
            title="🚀 Confession Boost Pack",
            description="10 extra confession sends – never expire.",
            payload="boost_pack",
            currency="XTR",
            prices=[LabeledPrice("Boost Pack – 10 sends", BOOST_PRICE)],
        )

    elif data.startswith("reveal_"):
        confession_id = int(data.split("_", 1)[1])
        confession = fetch_confession(confession_id)
        if not confession or confession["recipient_id"] != tg.id:
            await context.bot.send_message(tg.id, "This confession is not yours.")
            return
        await context.bot.send_invoice(
            chat_id=tg.id,
            title="🔍 Reveal Confession Sender",
            description="Pay to find out who sent you this anonymous confession.",
            payload=f"reveal_{confession_id}",
            currency="XTR",
            prices=[LabeledPrice("Reveal Sender", REVEAL_PRICE)],
        )

    elif data == "my_stats":
        reset_daily_if_needed(tg.id)
        u = fetch_user(tg.id)
        premium_ok = is_premium(u)
        expiry_str = u["premium_until"][:10] if u["premium_until"] else "—"
        with get_db() as conn:
            sent     = conn.execute("SELECT COUNT(*) FROM confessions WHERE sender_id=?",    (tg.id,)).fetchone()[0]
            received = conn.execute("SELECT COUNT(*) FROM confessions WHERE recipient_id=?", (tg.id,)).fetchone()[0]
        await context.bot.send_message(
            chat_id=tg.id,
            text=(
                f"📊 *Your Stats*\n\n"
                f"Premium: {'✅ until ' + expiry_str if premium_ok else '❌ None'}\n"
                f"Extra sends: {u['extra_sends']}\n"
                f"Sends today: {u['sends_today']}/{FREE_DAILY}\n"
                f"Total sent: {sent} | Received: {received}\n"
                f"Stars spent: {u['stars_spent']} ⭐"
            ),
            parse_mode="Markdown",
        )


# ── Payment handlers ──────────────────────────────────────────────────────────

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def payment_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    tg      = update.effective_user
    payload = payment.invoice_payload
    stars   = payment.total_amount

    if payload == "premium_monthly":
        expiry = (datetime.now() + timedelta(days=30)).isoformat()
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET is_premium=1, premium_until=? WHERE user_id=?",
                (expiry, tg.id),
            )
        log_payment(tg.id, "premium", stars)
        await update.message.reply_text(
            "🎉 *Premium activated!* Unlimited confessions for 30 days.\n\nThank you! ⭐",
            parse_mode="Markdown",
        )

    elif payload == "boost_pack":
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET extra_sends=extra_sends+10 WHERE user_id=?", (tg.id,)
            )
        log_payment(tg.id, "boost", stars)
        await update.message.reply_text(
            "🚀 *10 extra sends added!* They never expire. 💌",
            parse_mode="Markdown",
        )

    elif payload.startswith("reveal_"):
        confession_id = int(payload.split("_", 1)[1])
        confession    = fetch_confession(confession_id)
        log_payment(tg.id, "reveal", stars)
        if confession:
            sender = fetch_user(confession["sender_id"])
            if sender:
                name     = sender.get("first_name") or "Unknown"
                username = f"@{sender['username']}" if sender.get("username") else "(no username)"
                await update.message.reply_text(
                    f"🔍 *Sender Revealed!*\n\nName: *{name}*\nUsername: {username}",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text("Sender info not found (may have deleted their account).")
        else:
            await update.message.reply_text("Confession not found.")


# ── App bootstrap ─────────────────────────────────────────────────────────────

init_db()
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start",   cmd_start))
app.add_handler(CommandHandler("premium", cmd_premium))
app.add_handler(CommandHandler("boost",   cmd_boost))
app.add_handler(CommandHandler("stats",   cmd_stats))
app.add_handler(CommandHandler("admin",   cmd_admin))
app.add_handler(CallbackQueryHandler(handle_callback))
app.add_handler(PreCheckoutQueryHandler(precheckout))
app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("Bot running...")
asyncio.set_event_loop(asyncio.new_event_loop())
app.run_polling()
