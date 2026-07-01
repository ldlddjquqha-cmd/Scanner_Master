import os
import asyncio
from flask import Flask
from telegram.ext import ApplicationBuilder, CommandHandler

TOKEN = "8479849828:AAEl31VYsy9o7NrSL9lIHdmHDaUBrbP1aFw"

app = Flask(__name__)

# Обработчик команды /start
async def start(update, context):
    await update.message.reply_text("Бот запущен!")

@app.route('/')
def home():
    return "Бот работает"

if __name__ == "__main__":
    # 1. Инициализация через ApplicationBuilder
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    
    # 2. Запуск бота в фоне
    import threading
    threading.Thread(target=lambda: application.run_polling(), daemon=True).start()
    
    # 3. Запуск Flask (Render требует порт)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
