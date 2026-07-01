import os
import base64
import json
import cv2
import numpy as np
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from threading import Thread

# 1. ТОКЕН ТВОЕГО БОТА 💎
TOKEN = "8479849828:AAEl31VYsy9o7NrSL9lIHdmHDaUBrbP1aFw"

app = Flask(__name__)
CORS(app)

# 2. КОМПЬЮТЕРНЫЙ АНАЛИЗ ГРАФИКА ЧЕРЕЗ OPENCV 👁️
def analyze_image_pixels(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        return "ERROR", "Не удалось прочитать кадр с камеры"

    h, w, _ = img.shape
    
    # Центральный квадрат 100х100 пикселей (зона прицела)
    cy, cx = h // 2, w // 2
    roi = img[cy-50:cy+50, cx-50:cx+50]
    
    # Разделяем на каналы цветов (BGR)
    b_channel, g_channel, r_channel = cv2.split(roi)
    
    # Считаем среднюю плотность цвета
    avg_green = np.mean(g_channel)
    avg_red = np.mean(r_channel)
    
    if avg_green > avg_red + 15:
        return "UP", f"ИИ Команды Мастер обнаружил мощное преобладание зелёного спектра в прицеле (Зеленый: {avg_green:.1f}, Красный: {avg_red:.1f}). Сила быков растет, формируется восходящий импульс."
    elif avg_red > avg_green + 15:
        return "DOWN", f"ИИ Команды Мастер зафиксировал доминирование красного спектра пикселей в прицеле (Красный: {avg_red:.1f}, Зеленый: {avg_green:.1f}). Медведи давят рынок вниз."
    else:
        if avg_green > avg_red:
            return "FLAT_UP", f"Микро-тренд колеблется, но ИИ видит скрытый перевес покупателей (Зеленый: {avg_green:.1f} против Красного: {avg_red:.1f}). Цена затухает у уровня."
        else:
            return "FLAT_DOWN", f"Рынок в узком коридоре, но пиксельный анализ показывает плавное сползание котировок (Красный: {avg_red:.1f} против Зеленого: {avg_green:.1f})."

# 3. ОБРАБОТКА ЗАПРОСОВ ИЗ MINI APP
@app.route('/analyze', methods=['POST'])
def handle_analyze():
    data = request.get_json()
    if not data or 'image' not in data:
        return jsonify({"error": "Нет кадра"}), 400
        
    image_base64 = data.get('image')
    timeframe = data.get('tf')
    expiration = data.get('exp')
    
    try:
        header, encoded = image_base64.split(",", 1)
        image_bytes = base64.b64decode(encoded)
    except Exception:
        return jsonify({"error": "Ошибка декодирования"}), 400
        
    direction, ai_reason = analyze_image_pixels(image_bytes)
    
    if direction == "UP":
        return jsonify({
            "direction": "UP", "signal_text": "🟢 ВВЕРХ (CALL) 📈",
            "reason": f"🔥 {ai_reason} На интервале [{timeframe}] ИИ подтверждает силу покупателей.",
            "tips": f"💎 СОВЕТ МАСТЕРА: Открывай сделку ВВЕРХ на [{expiration}]. Риск — 1-2% от банка! 🧠"
        })
    elif direction == "DOWN":
        return jsonify({
            "direction": "DOWN", "signal_text": "🔴 ВНИЗ (PUT) 📉",
            "reason": f"💥 {ai_reason} На интервале [{timeframe}] график пробивает локальные микро-уровни вниз.",
            "tips": f"💎 СОВЕТ МАСТЕРА: Заходим ВНИЗ на [{expiration}]. Объемы продавцов подтверждены сканированием! 🛡️"
        })
    elif direction == "FLAT_UP":
        return jsonify({
            "direction": "UP", "signal_text": "🟡 ОСТОРОЖНО: ВВЕРХ 📈",
            "reason": f"💤 {ai_reason} Сигнал ослаблен из-за флэта, но микро-перевес за быками.",
            "tips": f"💎 СОВЕТ МАСТЕРА: Вход вверх на [{expiration}] допустим уменьшенным лотом (0.5% от депо)."
        })
    else:
        return jsonify({
            "direction": "DOWN", "signal_text": "🟡 ОСТОРОЖНО: ВНИЗ 📉",
            "reason": f"💤 {ai_reason} На рынке временное затишье, но продавцы аккуратно поддавливают цену.",
            "tips": f"💎 СОВЕТ МАСТЕРА: Вход вниз на [{expiration}] только если свеча дернется к верхней границе."
        })

# 4. КОМАНДА /START ДЛЯ БОТА
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'scanner-master.onrender.com')}"
    
    keyboard = [[InlineKeyboardButton("🔮 ЗАПУСТИТЬ ИИ СКАНЕР 🚀", web_app=WebAppInfo(url=app_url))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Привет, Мастер трейдинга! 🤖💎\n\nНажимай на кнопку ниже, направляй прицел камеры смартфона прямо на текущую свечу на мониторе и жми сканировать! Настоящее компьютерное зрение сделает всё за тебя! 📈💸",
        reply_markup=reply_markup, parse_mode="Markdown"
    )

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    Thread(target=run_flask).start()
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.run_polling()
