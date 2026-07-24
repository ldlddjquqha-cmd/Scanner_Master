import os
from flask import Flask, render_template_string, request, jsonify
import requests

app = Flask(__name__)

# Настройки для Telegram (можете заменить на свои или передать через переменные окружения)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', 'YOUR_CHAT_ID')

@app.route('/')
def index():
    # Автоматически ищет index.html в текущей папке или в папке templates
    if os.path.exists('templates/index.html'):
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            return f.read()
    elif os.path.exists('index.html'):
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()
    return "Шаблон index.html не найден! Пожалуйста, разместите его в папке templates/ или рядом с main.py."

@app.route('/api/log_signal', methods=['POST'])
def log_signal():
    try:
        asset = request.form.get('asset', 'EUR/USD OTC')
        direction = request.form.get('direction', 'CALL')
        timeframe = request.form.get('timeframe', 'M1')
        is_hedge = request.form.get('is_hedge', 'false') == 'true'

        hedge_text = " ⚠️ [ПЕРЕКРЫТИЕ / ДОГОН]" if is_hedge else ""
        text = f"🎯 <b>НОВЫЙ СИГНАЛ ТЕРМИНАЛА</b>{hedge_text}\n" \
               f"📊 Актив: <b>{asset}</b>\n" \
               f"📈 Направление: <b>{direction}</b>\n" \
               f"⏳ Время: <b>{timeframe}</b>"

        # Отправка в Telegram канал/чат (если токен настроен)
        if TELEGRAM_BOT_TOKEN != 'YOUR_BOT_TOKEN':
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            }
            requests.post(url, json=payload, timeout=5)

        return jsonify({"status": "success", "message": "Signal logged and broadcasted"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/ai_chat', methods=['POST'])
def ai_chat():
    try:
        user_message = request.form.get('message', '')
        
        # Интеллектуальный логический блок ответов для трейдеров
        msg_lower = user_message.lower()
        if "мартин" in msg_lower or "догон" in msg_lower:
            reply = "📈 Стратегия Мартингейла (догон) требует строгого контроля рисков. Рекомендуется использовать не более 1 перекрытия с увеличением суммы в 2.2 раза."
        elif "анализ" in msg_lower or "рынок" in msg_lower:
            reply = "🔍 Текущая волатильность на OTC-рынках умеренная. Рекомендуем торговать по трендовым паттернам на таймфреймах M1-M5."
        elif "скачать" in msg_lower or "приложение" in msg_lower:
            reply = "📱 Наш терминал работает прямо в браузере и оптимизирован для мобильных устройств. Добавьте его на главный экран телефона для удобства!"
        else:
            reply = f"🤖 ИИ-ассистент Team Master VIP проанализировал ваш запрос ('{user_message}'). Следите за сигналами терминала и соблюдайте правила риск-менеджмента!"

        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": "Произошла ошибка обработки запроса."}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
