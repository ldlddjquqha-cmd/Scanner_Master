import os
import asyncio
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler

TOKEN = "8479849828:AAEl31VYsy9o7NrSL9lIHdmHDaUBrbP1aFw"
# Твой URL на Render
APP_URL = "https://scanner-master.onrender.com"

app = Flask(__name__)
# Инициализация бота
bot = Bot(token=TOKEN)
application = ApplicationBuilder().token(TOKEN).build()

async def start(update, context):
    await update.message.reply_text("Бот и сканер активны! 🚀")

application.add_handler(CommandHandler("start", start))

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    # Принимаем данные от Telegram
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, bot)
    # Обрабатываем сообщение
    asyncio.run(application.process_update(update))
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return "Сервер работает!", 200

# ТУТ ТВОЙ АНАЛИЗАТОР (handle_analyze)
# ... вставь сюда свою логику с 6 языками, которую мы делали раньше ...

if __name__ == "__main__":
    # Установка вебхука при старте
    asyncio.run(bot.set_webhook(url=f"{APP_URL}/{TOKEN}"))
    
    # Запуск сервера
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
