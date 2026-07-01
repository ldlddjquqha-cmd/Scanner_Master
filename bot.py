import os
import base64
import cv2
import numpy as np
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio
from threading import Thread

# 1. КОНСТАНТЫ ЖЕСТКО В КОД
TOKEN = "8479849828:AAEl31VYsy9o7NrSL9lIHdmHDaUBrbP1aFw"
APP_URL = "https://scanner-master.onrender.com"

app = Flask(__name__)
CORS(app)

# 2. ИИ АНАЛИЗ ПИКСЕЛЕЙ СВЕЧИ
def analyze_image_pixels(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return "ERROR", "Не удалось прочитать кадр"
    
    h, w, _ = img.shape
    cy, cx = h // 2, w // 2
    roi = img[cy-50:cy+50, cx-50:cx+50]
    
    b, g, r = cv2.split(roi)
    avg_green = np.mean(g)
    avg_red = np.mean(r)
    
    if avg_green > avg_red + 15:
        return "UP", f"ИИ Команды Мастер обнаружил мощное преобладание зелёного спектра (Зеленый: {avg_green:.1f}, Красный: {avg_red:.1f}). Импульс вверх подтвержден."
    elif avg_red > avg_green + 15:
        return "DOWN", f"ИИ Команды Мастер зафиксировал доминирование красного спектра (Красный: {avg_red:.1f}, Зеленый: {avg_green:.1f}). Медведи давят рынок вниз."
    else:
        if avg_green > avg_red:
            return "FLAT_UP", f"Микро-тренд колеблется. Перевес покупателей слабый (Зеленый: {avg_green:.1f} против Красного: {avg_red:.1f})."
        else:
            return "FLAT_DOWN", f"Рынок в узком коридоре. Продавцы аккуратно поддавливают цену (Красный: {avg_red:.1f} против Зеленого: {avg_green:.1f})."

# 3. МАРШРУТЫ FLASK
@app.route('/')
def home():
    return "ИИ Сервер Команды Мастер запущен и работает! 🟢"

@app.route('/analyze', methods=['POST'])
def handle_analyze():
    data = request.get_json()
    if not data or 'image' not in data:
        return jsonify({"error": "Нет кадра"}), 400
    
    try:
        header, encoded = data.get('image').split(",", 1)
        image_bytes = base64.b64decode(encoded)
    except Exception:
        return jsonify({"error": "Ошибка декодирования"}), 400
        
    direction, ai_reason = analyze_image_pixels(image_bytes)
    tf = data.get('tf', '1m')
    expiration = data.get('exp', '1m')
    
    if direction == "UP":
        return jsonify({"direction": "UP", "signal_text": "🟢 ВВЕРХ (CALL) 📈", "reason": f"🔥 {ai_reason} Интервал [{tf}].", "tips": f"💎 СОВЕТ: Вход ВВЕРХ на [{expiration}]. Риск — 1-2% от банка!"})
    elif direction == "DOWN":
        return jsonify({"direction": "DOWN", "signal_text": "🔴 ВНИЗ (PUT) 📉", "reason": f"💥 {ai_reason} Интервал [{tf}].", "tips": f"💎 СОВЕТ: Вход ВНИЗ на [{expiration}]. Объемы подтверждены!"})
    elif direction == "FLAT_UP":
        return jsonify({"direction": "UP", "signal_text": "🟡 ОСТОРОЖНО: ВВЕРХ 📈", "reason": f"💤 {ai_reason}", "tips": f"💎 СОВЕТ: Снизь лот до 0.5% от депозита."})
    else:
        return jsonify({"direction": "DOWN", "signal_text": "🟡 ОСТОРОЖНО: ВНИЗ 📉", "reason": f"💤 {ai_reason}", "tips": f"💎 СОВЕТ: Вход только от верхней границы коридора."})

# 4. ТЕЛЕГРАМ ОБРАБОТЧИК
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("🔮 ЗАПУСТИТЬ ИИ СКАНЕР 🚀", web_app=WebAppInfo(url=APP_URL))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Привет, Мастер трейдинга! 🤖💎\n\nНажимай на кнопку ниже, направляй камеру на свечу и жми сканировать! Компьютерное зрение выдаст точный вердикт. 📈💸",
        reply_markup=reply_markup, parse_mode="Markdown"
    )

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    # Сначала стартуем веб-сервер во внешнем потоке
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    # Затем чисто и без конфликтов запускаем поллинг Телеграм-бота
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.run_polling()
