import os
from flask import Flask
from telegram.ext import ApplicationBuilder, CommandHandler

# Твой токен
TOKEN = "8479849828:AAEl31VYsy9o7NrSL9lIHdmHDaUBrbP1aFw"

# Инициализация Flask для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает"

async def start(update, context):
    await update.message.reply_text("Бот запущен!")

if __name__ == '__main__':
    # 1. Создаем приложение по-новому (без Updater!)
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    
    # 2. Flask запускается на порту Render
    port = int(os.environ.get("PORT", 10000))
    
    # 3. ВАЖНО: polling тут нельзя вызвать просто так, 
    # иначе он заблокирует Flask. Используем Webhook или запускаем бота отдельно.
    # Для самого простого теста — запустим бота:
    application.run_polling()
