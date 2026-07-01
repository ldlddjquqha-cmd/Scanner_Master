import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler
import asyncio

# Твой токен
TOKEN = "8479849828:AAEl31VYsy9o7NrSL9lIHdmHDaUBrbP1aFw"
# Твой домен на Render (проверь точность!)
URL = "https://scanner-master.onrender.com"

app = Flask(__name__)
bot = Bot(token=TOKEN)
# Современный способ инициализации
app_bot = ApplicationBuilder().token(TOKEN).build()

async def start(update, context):
    await update.message.reply_text("Бот запущен!")

app_bot.add_handler(CommandHandler("start", start))

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, bot)
    asyncio.run(app_bot.process_update(update))
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return "OK", 200

if __name__ == "__main__":
    # Устанавливаем связь с серверами Telegram
    asyncio.run(bot.set_webhook(url=f"{URL}/{TOKEN}"))
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
