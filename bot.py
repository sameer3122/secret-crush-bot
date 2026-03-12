import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")

users = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    users[user_id] = user_id
    link = f"https://t.me/Secretcrushconfessionbot?start={user_id}"
    
    await update.message.reply_text(
        f"👀 Secret Crush Confession Bot\n\n"
        f"Share this link to receive anonymous confessions:\n\n{link}"
    )

async def start_with_param(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        target_id = int(context.args[0])
        context.user_data["target"] = target_id
        await update.message.reply_text("💌 Send your anonymous confession now.")
    else:
        await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "target" in context.user_data:
        target = context.user_data["target"]
        message = update.message.text

        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"💌 You received an anonymous confession:\n\n{message}"
            )
            await update.message.reply_text("✅ Confession sent anonymously.")
        except:
            await update.message.reply_text("User must start the bot first.")

        context.user_data.pop("target")
    else:
        await update.message.reply_text("Use a confession link to send a message.")

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start_with_param))
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

print("Bot running...")
app.run_polling()
