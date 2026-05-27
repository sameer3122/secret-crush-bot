import asyncio
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

TOKEN    = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB_PATH = "food_bot.db"

COUNTRIES = ["🇦🇪 Dubai / UAE", "🇶🇦 Qatar / Doha"]

AREAS = {
    "🇦🇪 Dubai / UAE": [
        "Dubai Marina", "Downtown Dubai", "JLT",
        "Deira", "Bur Dubai", "Business Bay",
        "JBR", "Palm Jumeirah", "Al Barsha", "Sharjah"
    ],
    "🇶🇦 Qatar / Doha": [
        "The Pearl", "West Bay", "Lusail",
        "Al Waab", "Msheireb", "Al Rayyan",
        "Katara", "Souq Waqif", "Al Sadd", "Mesaieed"
    ]
}

CUISINES = [
    "Arabic", "Indian", "Chinese", "Italian",
    "Filipino", "Pakistani", "Lebanese", "American",
    "Japanese", "Mexican"
]

# ── Database ──────────────────────────────────────────────────────────────────

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
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                joined_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS restaurants (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                area        TEXT NOT NULL,
                cuisine     TEXT NOT NULL,
                description TEXT,
                contact     TEXT,
                location    TEXT,
                is_featured INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1,
                added_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS deals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                restaurant_id INTEGER,
                title         TEXT NOT NULL,
                description   TEXT,
                discount      TEXT,
                valid_until   TEXT,
                is_active     INTEGER DEFAULT 1,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
            );
        """)
        # Add sample data if empty
        count = conn.execute("SELECT COUNT(*) FROM restaurants").fetchone()[0]
        if count == 0:
            _seed_sample_data(conn)


def _seed_sample_data(conn):
    restaurants = [
        ("Al Faham Restaurant", "Dubai Marina", "Arabic",
         "Authentic grilled chicken & shawarma", "+971 4 123 4567",
         "Dubai Marina Walk", 1),
        ("Spice Garden", "Downtown Dubai", "Indian",
         "Best butter chicken in Dubai!", "+971 4 234 5678",
         "Near Dubai Mall", 1),
        ("Golden Dragon", "JLT", "Chinese",
         "Dim sum and authentic Chinese cuisine", "+971 4 345 6789",
         "JLT Cluster A", 0),
        ("Pizza Roma", "JBR", "Italian",
         "Wood fired pizzas with sea view", "+971 4 456 7890",
         "The Walk, JBR", 1),
        ("Karachi Darbar", "Deira", "Pakistani",
         "Famous biryani and karahi since 1985", "+971 4 567 8901",
         "Deira City Centre area", 0),
        # Qatar restaurants
        ("Parisa Souq Waqif", "Souq Waqif", "Arabic",
         "Traditional Qatari cuisine in heritage setting", "+974 4433 2211",
         "Souq Waqif, Doha", 1),
        ("Al Mourjan", "West Bay", "Lebanese",
         "Fine dining with stunning Doha skyline views", "+974 4422 1100",
         "West Bay Lagoon, Doha", 1),
        ("Katara Beach Club", "Katara", "American",
         "Burgers and grills by the beach", "+974 4408 8000",
         "Katara Cultural Village, Doha", 0),
        ("Spice Market Lusail", "Lusail", "Indian",
         "Best biryani and curry in Lusail City", "+974 5512 3456",
         "Lusail Marina, Doha", 0),
    ]
    for r in restaurants:
        conn.execute(
            "INSERT INTO restaurants (name, area, cuisine, description, contact, location, is_featured)"
            " VALUES (?,?,?,?,?,?,?)", r
        )

    deals = [
        (1, "20% OFF All Meals", "Show this bot message to get 20% off!", "20%", "2026-12-31"),
        (2, "Buy 1 Get 1 Free", "Buy any main course get one free!", "BOGO", "2026-12-31"),
        (3, "Free Delivery", "Free delivery on orders above 50 AED", "Free Delivery", "2026-12-31"),
        (4, "30% OFF Weekdays", "30% discount Monday to Thursday", "30%", "2026-12-31"),
        (5, "Family Meal Deal", "Family meal for 4 only 99 AED!", "Special Price", "2026-12-31"),
    ]
    for d in deals:
        conn.execute(
            "INSERT INTO deals (restaurant_id, title, description, discount, valid_until)"
            " VALUES (?,?,?,?,?)", d
        )


def ensure_user(user_id, username=None, first_name=None):
    with get_db() as conn:
        exists = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (user_id, username, first_name) VALUES (?,?,?)",
                (user_id, username, first_name)
            )
            return True
    return False


# ── Keyboards ─────────────────────────────────────────────────────────────────

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Today's Hot Deals", callback_data="all_deals")],
        [InlineKeyboardButton("🇦🇪 Dubai / UAE", callback_data="country_🇦🇪 Dubai / UAE"),
         InlineKeyboardButton("🇶🇦 Qatar / Doha", callback_data="country_🇶🇦 Qatar / Doha")],
        [InlineKeyboardButton("🍽️ Browse by Cuisine", callback_data="by_cuisine")],
        [InlineKeyboardButton("⭐ Featured Restaurants", callback_data="featured")],
        [InlineKeyboardButton("📢 List Your Restaurant", callback_data="list_restaurant")],
    ])


def areas_keyboard(country):
    buttons = []
    row = []
    for i, area in enumerate(AREAS.get(country, [])):
        row.append(InlineKeyboardButton(area, callback_data=f"area_{area}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="home")])
    return InlineKeyboardMarkup(buttons)


def cuisines_keyboard():
    buttons = []
    row = []
    for i, cuisine in enumerate(CUISINES):
        row.append(InlineKeyboardButton(cuisine, callback_data=f"cuisine_{cuisine}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="home")])
    return InlineKeyboardMarkup(buttons)


def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home")]
    ])


# ── Formatters ────────────────────────────────────────────────────────────────

def format_restaurant(r, deals=None):
    star = "⭐ FEATURED | " if r["is_featured"] else ""
    text = (
        f"{star}🍽️ *{r['name']}*\n"
        f"📍 {r['area']} | 🥘 {r['cuisine']}\n"
        f"📝 {r['description']}\n"
        f"📞 {r['contact']}\n"
        f"🗺️ {r['location']}\n"
    )
    if deals:
        text += "\n🎉 *Current Deals:*\n"
        for d in deals:
            text += f"  • {d['title']} — _{d['description']}_\n"
    return text


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    is_new = ensure_user(tg.id, tg.username, tg.first_name)

    greeting = f"Welcome back, *{tg.first_name}*! 👋" if not is_new else f"Welcome, *{tg.first_name}*! 🎉"

    await update.message.reply_text(
        f"🍕 *Gulf Food Deals Bot*\n\n"
        f"{greeting}\n\n"
        f"Discover the *best food deals* in Dubai & Qatar!\n\n"
        f"🇦🇪 Dubai / UAE areas\n"
        f"🇶🇦 Qatar / Doha areas\n"
        f"🔥 Hot deals updated daily\n"
        f"🍽️ Browse by cuisine\n"
        f"⭐ Featured restaurants\n\n"
        f"What are you looking for today?",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    with get_db() as conn:
        total_users  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_rest   = conn.execute("SELECT COUNT(*) FROM restaurants WHERE is_active=1").fetchone()[0]
        featured     = conn.execute("SELECT COUNT(*) FROM restaurants WHERE is_featured=1 AND is_active=1").fetchone()[0]
        total_deals  = conn.execute("SELECT COUNT(*) FROM deals WHERE is_active=1").fetchone()[0]

    revenue = featured * 100
    await update.message.reply_text(
        f"📊 *Admin Dashboard*\n\n"
        f"👥 Total users: {total_users}\n"
        f"🍽️ Active restaurants: {total_rest}\n"
        f"⭐ Featured restaurants: {featured}\n"
        f"🎉 Active deals: {total_deals}\n\n"
        f"💰 *Monthly Revenue*\n"
        f"Featured listings: {featured} × $100 = *${revenue}/month*\n\n"
        f"📋 *Admin Commands:*\n"
        f"/addrest - Add restaurant\n"
        f"/adddeal - Add deal\n"
        f"/broadcast - Message all users\n"
        f"/users - Total user count",
        parse_mode="Markdown",
    )


async def cmd_addrest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data["mode"] = "add_restaurant"
    context.user_data["rest_step"] = "name"
    context.user_data["rest_data"] = {}
    await update.message.reply_text(
        "➕ *Add New Restaurant*\n\nStep 1/6: Enter restaurant *name*:",
        parse_mode="Markdown"
    )


async def cmd_adddeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    with get_db() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE is_active=1"
        ).fetchall()
    if not restaurants:
        await update.message.reply_text("No restaurants found. Add a restaurant first.")
        return
    buttons = [[InlineKeyboardButton(r["name"], callback_data=f"adddeal_{r['id']}")] for r in restaurants]
    await update.message.reply_text(
        "➕ *Add Deal — Select Restaurant:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data["mode"] = "broadcast"
    await update.message.reply_text("📢 Type the message to broadcast to all users:")


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    await update.message.reply_text(f"👥 Total users: *{count}*", parse_mode="Markdown")


# ── Callback handler ──────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg   = query.from_user
    data = query.data

    if data == "home":
        await context.bot.send_message(
            chat_id=tg.id,
            text="🍕 *Dubai Food Deals Bot*\n\nWhat are you looking for today?",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    elif data == "all_deals":
        with get_db() as conn:
            deals = conn.execute("""
                SELECT d.*, r.name as rest_name, r.area, r.contact
                FROM deals d
                JOIN restaurants r ON d.restaurant_id = r.id
                WHERE d.is_active=1 AND r.is_active=1
                ORDER BY r.is_featured DESC
                LIMIT 10
            """).fetchall()

        if not deals:
            await context.bot.send_message(tg.id, "No deals available right now. Check back soon!")
            return

        text = "🔥 *Today's Hot Deals in Dubai!*\n\n"
        for d in deals:
            text += (
                f"🎉 *{d['title']}*\n"
                f"🍽️ {d['rest_name']} | 📍 {d['area']}\n"
                f"_{d['description']}_\n"
                f"📞 {d['contact']}\n"
                f"⏰ Valid until: {d['valid_until']}\n\n"
            )
        await context.bot.send_message(
            tg.id, text, parse_mode="Markdown", reply_markup=back_keyboard()
        )

    elif data.startswith("country_"):
        country = data.replace("country_", "")
        await context.bot.send_message(
            tg.id,
            f"📍 *Select your area in {country}:*",
            parse_mode="Markdown",
            reply_markup=areas_keyboard(country),
        )

    elif data == "by_cuisine":
        await context.bot.send_message(
            tg.id,
            "🍽️ *Select cuisine type:*",
            parse_mode="Markdown",
            reply_markup=cuisines_keyboard(),
        )

    elif data == "featured":
        with get_db() as conn:
            restaurants = conn.execute(
                "SELECT * FROM restaurants WHERE is_featured=1 AND is_active=1"
            ).fetchall()
        if not restaurants:
            await context.bot.send_message(tg.id, "No featured restaurants yet.")
            return
        text = "⭐ *Featured Restaurants in Dubai*\n\n"
        for r in restaurants:
            with get_db() as conn:
                deals = conn.execute(
                    "SELECT * FROM deals WHERE restaurant_id=? AND is_active=1", (r["id"],)
                ).fetchall()
            text += format_restaurant(r, deals) + "\n"
        await context.bot.send_message(
            tg.id, text, parse_mode="Markdown", reply_markup=back_keyboard()
        )

    elif data == "list_restaurant":
        await context.bot.send_message(
            tg.id,
            "📢 *List Your Restaurant on Dubai Food Deals Bot!*\n\n"
            "✅ Basic listing: *FREE*\n"
            "⭐ Featured listing: *$100/month*\n\n"
            "Featured benefits:\n"
            "• Appear at the top of all searches\n"
            "• Highlighted with ⭐ star badge\n"
            "• Shown in 'Featured' section\n"
            "• Daily deal promotions\n\n"
            "📩 Contact admin to get listed:\n"
            "@YourUsername",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

    elif data.startswith("area_"):
        area = data.replace("area_", "")
        with get_db() as conn:
            restaurants = conn.execute(
                "SELECT * FROM restaurants WHERE area=? AND is_active=1 ORDER BY is_featured DESC",
                (area,)
            ).fetchall()
        if not restaurants:
            await context.bot.send_message(
                tg.id, f"No restaurants found in {area} yet. Check back soon!",
                reply_markup=back_keyboard()
            )
            return
        text = f"📍 *Restaurants in {area}*\n\n"
        for r in restaurants:
            with get_db() as conn:
                deals = conn.execute(
                    "SELECT * FROM deals WHERE restaurant_id=? AND is_active=1", (r["id"],)
                ).fetchall()
            text += format_restaurant(r, deals) + "\n"
        await context.bot.send_message(
            tg.id, text, parse_mode="Markdown", reply_markup=back_keyboard()
        )

    elif data.startswith("cuisine_"):
        cuisine = data.replace("cuisine_", "")
        with get_db() as conn:
            restaurants = conn.execute(
                "SELECT * FROM restaurants WHERE cuisine=? AND is_active=1 ORDER BY is_featured DESC",
                (cuisine,)
            ).fetchall()
        if not restaurants:
            await context.bot.send_message(
                tg.id, f"No {cuisine} restaurants found yet. Check back soon!",
                reply_markup=back_keyboard()
            )
            return
        text = f"🥘 *{cuisine} Restaurants in Dubai*\n\n"
        for r in restaurants:
            with get_db() as conn:
                deals = conn.execute(
                    "SELECT * FROM deals WHERE restaurant_id=? AND is_active=1", (r["id"],)
                ).fetchall()
            text += format_restaurant(r, deals) + "\n"
        await context.bot.send_message(
            tg.id, text, parse_mode="Markdown", reply_markup=back_keyboard()
        )

    elif data.startswith("adddeal_"):
        rest_id = int(data.split("_")[1])
        context.user_data["mode"] = "add_deal"
        context.user_data["deal_rest_id"] = rest_id
        context.user_data["deal_step"] = "title"
        context.user_data["deal_data"] = {}
        await context.bot.send_message(
            tg.id,
            "➕ *Add Deal*\n\nStep 1/3: Enter deal *title*:\n_(e.g. 20% OFF All Meals)_",
            parse_mode="Markdown"
        )


# ── Message handler ───────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg   = update.effective_user
    text = update.message.text
    mode = context.user_data.get("mode")

    # ── Broadcast mode ──
    if mode == "broadcast" and tg.id == ADMIN_ID:
        with get_db() as conn:
            users = conn.execute("SELECT user_id FROM users").fetchall()
        sent = 0
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=f"📢 *Message from Dubai Food Deals Bot:*\n\n{text}",
                    parse_mode="Markdown",
                    reply_markup=main_keyboard(),
                )
                sent += 1
            except Exception:
                pass
        context.user_data.pop("mode", None)
        await update.message.reply_text(f"✅ Broadcast sent to {sent} users!")
        return

    # ── Add restaurant flow ──
    if mode == "add_restaurant" and tg.id == ADMIN_ID:
        step = context.user_data.get("rest_step")
        data = context.user_data.get("rest_data", {})

        steps = {
            "name":        ("area",        "Step 2/6: Enter *area*:\n_(e.g. Dubai Marina)_"),
            "area":        ("cuisine",     "Step 3/6: Enter *cuisine type*:\n_(e.g. Arabic, Indian)_"),
            "cuisine":     ("description", "Step 4/6: Enter *description*:"),
            "contact":     ("location",    "Step 5/6: Enter *location/address*:"),
            "description": ("contact",     "Step 5/6: Enter *contact number*:"),
            "location":    (None,          None),
        }

        data[step] = text
        context.user_data["rest_data"] = data

        if step == "location":
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO restaurants (name, area, cuisine, description, contact, location)"
                    " VALUES (?,?,?,?,?,?)",
                    (data.get("name"), data.get("area"), data.get("cuisine"),
                     data.get("description"), data.get("contact"), data.get("location"))
                )
            context.user_data.pop("mode", None)
            context.user_data.pop("rest_step", None)
            context.user_data.pop("rest_data", None)
            await update.message.reply_text(
                f"✅ *Restaurant added successfully!*\n\n"
                f"🍽️ {data.get('name')} in {data.get('area')}",
                parse_mode="Markdown"
            )
        else:
            next_step, next_msg = steps[step]
            context.user_data["rest_step"] = next_step
            await update.message.reply_text(next_msg, parse_mode="Markdown")
        return

    # ── Add deal flow ──
    if mode == "add_deal" and tg.id == ADMIN_ID:
        step     = context.user_data.get("deal_step")
        deal_data = context.user_data.get("deal_data", {})
        rest_id  = context.user_data.get("deal_rest_id")

        deal_data[step] = text
        context.user_data["deal_data"] = deal_data

        if step == "title":
            context.user_data["deal_step"] = "description"
            await update.message.reply_text(
                "Step 2/3: Enter deal *description*:", parse_mode="Markdown"
            )
        elif step == "description":
            context.user_data["deal_step"] = "valid_until"
            await update.message.reply_text(
                "Step 3/3: Enter *valid until* date:\n_(e.g. 2026-12-31)_",
                parse_mode="Markdown"
            )
        elif step == "valid_until":
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO deals (restaurant_id, title, description, valid_until)"
                    " VALUES (?,?,?,?)",
                    (rest_id, deal_data.get("title"),
                     deal_data.get("description"), deal_data.get("valid_until"))
                )
            context.user_data.pop("mode", None)
            context.user_data.pop("deal_step", None)
            context.user_data.pop("deal_data", None)
            context.user_data.pop("deal_rest_id", None)
            await update.message.reply_text("✅ *Deal added successfully!*", parse_mode="Markdown")
        return

    # ── Default ──
    ensure_user(tg.id, tg.username, tg.first_name)
    await update.message.reply_text(
        "🍕 *Dubai Food Deals Bot*\n\nWhat are you looking for today?",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ── App bootstrap ─────────────────────────────────────────────────────────────

init_db()
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start",     cmd_start))
app.add_handler(CommandHandler("admin",     cmd_admin))
app.add_handler(CommandHandler("addrest",   cmd_addrest))
app.add_handler(CommandHandler("adddeal",   cmd_adddeal))
app.add_handler(CommandHandler("broadcast", cmd_broadcast))
app.add_handler(CommandHandler("users",     cmd_users))
app.add_handler(CallbackQueryHandler(handle_callback))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("Dubai Food Deals Bot running...")
asyncio.set_event_loop(asyncio.new_event_loop())
app.run_polling()
