import os
import time
import hashlib
from flask import Flask, render_template, request, jsonify
import requests
from groq import Groq

app = Flask(__name__)

# ==================== НАСТРОЙКИ И ДАННЫЕ БОССА ====================
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '7960762468:AAEu1rItSoIL9Q7cHtY-zA5kCr3UmlDWSLQ')
BOSS_TELEGRAM_ID = os.environ.get('BOSS_TELEGRAM_ID', '109386966')

POCKET_PARTNER_ID = os.environ.get('POCKET_PARTNER_ID', '109386966')
POCKET_API_TOKEN = os.environ.get('POCKET_API_TOKEN', 'Zc4X9zu0EMrqbPuLy3tN')

# Актуальная реферальная ссылка
REF_LINK = "https://u3.shortink.io/cabinet/demo-quick-high-low?utm_campaign=850173&utm_source=affiliate&utm_medium=sr&a=RLQDltKf13Zlrj&al=1771346&ac=smart-link&cid=960963&code=WELCOME50"

GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

BLOCKED_USERS = set()
LAST_REQUESTS = {}

def is_rate_limited(ip_address):
    now = time.time()
    if ip_address in LAST_REQUESTS:
        if now - LAST_REQUESTS[ip_address] < 1.5:
            return True
    LAST_REQUESTS[ip_address] = now
    return False

def send_telegram_msg(text, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": BOSS_TELEGRAM_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"TG error: {e}")

def check_partner_trader(user_id):
    try:
        raw_hash_string = f"{user_id}:{POCKET_PARTNER_ID}:{POCKET_API_TOKEN}"
        hash_md5 = hashlib.md5(raw_hash_string.encode('utf-8')).hexdigest()
        url = f"https://affiliate.pocketoption.com/api/user-info/{user_id}/{POCKET_PARTNER_ID}/{hash_md5}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

@app.route('/')
def index():
    return render_template('index.html', ref_link=REF_LINK)

# --- УВЕДОМЛЕНИЕ БОССУ О ПЕРЕХОДЕ К СИГНАЛАМ ---
@app.route('/api/notify_signals_access', methods=['POST'])
def notify_signals_access():
    user_ip = request.remote_addr
    msg = f"🚀 <b>БОСС, ПОЛЬЗОВАТЕЛЬ ПЕРЕШЕЛ К СИГНАЛАМ!</b>\n\n🌐 IP: <code>{user_ip}</code>\n⏰ Время: {time.strftime('%H:%M:%S')}"
    send_telegram_msg(msg)
    return jsonify({"status": "ok"})

# --- ПРОВЕРКА ТРЕЙДЕРА С КНОПКАМИ БЛОКИРОВКИ ---
@app.route('/api/verify_trader', methods=['POST'])
def verify_trader():
    ip = request.remote_addr
    if is_rate_limited(ip):
        return jsonify({"status": "error", "message": "Запросы слишком частые!"}), 429

    trader_id = request.form.get('trader_id', '').strip()
    if not trader_id:
        return jsonify({"status": "error", "message": "Введите ваш ID Pocket Option"}), 400

    if trader_id in BLOCKED_USERS:
        return jsonify({"status": "blocked", "message": "⛔ Ваш ID заблокирован в системе."}), 403

    trader_data = check_partner_trader(trader_id)
    status_text = "✅ <b>Найден в партнерке</b>" if trader_data else "⚠️ <b>Не найден в партнерке</b>"

    message_text = (
        f"👑 <b>НОВЫЙ ТРЕЙДЕР В СИСТЕМЕ!</b>\n\n"
        f"🆔 ID Трейдера: <code>{trader_id}</code>\n"
        f"📊 Партнерка: {status_text}\n"
        f"🔗 Ссылка: <a href='{REF_LINK}'>Рефералка</a>"
    )

    inline_keyboard = {
        "inline_keyboard": [
            [
                {"text": "⛔ Заблокировать НАВСЕГДА", "callback_data": f"block_{trader_id}"},
                {"text": "✅ Разблокировать", "callback_data": f"unblock_{trader_id}"}
            ]
        ]
    }

    send_telegram_msg(message_text, inline_keyboard)
    return jsonify({"status": "success", "trader_id": trader_id, "partner_info": trader_data})

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        if "callback_query" in data:
            callback = data["callback_query"]
            chat_id = callback["message"]["chat"]["id"]
            action_data = callback["data"]
            message_id = callback["message"]["message_id"]

            if str(chat_id) == str(BOSS_TELEGRAM_ID):
                if action_data.startswith("block_"):
                    target_id = action_data.split("block_")[1]
                    BLOCKED_USERS.add(target_id)
                    res_text = f"⛔ Трейдер ID {target_id} ЗАБЛОКИРОВАН!"
                elif action_data.startswith("unblock_"):
                    target_id = action_data.split("unblock_")[1]
                    BLOCKED_USERS.discard(target_id)
                    res_text = f"✅ Трейдер ID {target_id} РАЗБЛОКИРОВАН!"

                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={
                    "callback_query_id": callback["id"], "text": res_text, "show_alert": True
                })
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json={
                    "chat_id": chat_id, "message_id": message_id,
                    "text": f"👑 <b>Панель Управления Босса</b>\n\n{res_text}",
                    "parse_mode": "HTML"
                })
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/log_signal', methods=['POST'])
def log_signal():
    try:
        asset = request.form.get('asset', 'EUR/USD OTC')
        direction = request.form.get('direction', 'CALL')
        timeframe = request.form.get('timeframe', 'M1')
        expiration = request.form.get('expiration', '1m')
        accuracy = request.form.get('accuracy', '92%')

        text = f"🎯 <b>НОВЫЙ СИГНАЛ СГЕНЕРИРОВАН!</b>\n\n" \
               f"📊 Актив: <b>{asset}</b>\n" \
               f"📈 Направление: <b>{direction}</b>\n" \
               f"⏳ Интервал: <b>{timeframe}</b> | Экспирация: <b>{expiration}</b>\n" \
               f"🎯 Вероятность: <b>{accuracy}</b>"

        send_telegram_msg(text)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- ИИ ПОМОЩНИК С МОДЕРАЦИЕЙ И БЕЗ РЕФЕРАЛКИ ---
@app.route('/ai_chat', methods=['POST'])
def ai_chat():
    user_message = request.form.get('message', '').strip()
    if not user_message:
        return jsonify({"reply": "Пожалуйста, введите ваш вопрос."}), 400

    if groq_client:
        try:
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты — вежливый и компетентный технический ИИ-консультант торгового веб-терминала TEAM MASTER VIP. "
                            "Твоя задача: помогать пользователям решать любые проблемы с работой сайта, объяснять торговые стратегии, "
                            "работу Pocket Option, таймфреймы, экспирацию и термины. "
                            "СТРОГИЕ ПРАВИЛА: "
                            "1. НЕ упоминай и НЕ вставляй никакие реферальные ссылки или промокоды! "
                            "2. Если пользователь задает нецензурные, вульгарные, оскорбительные или неприличные вопросы — отвечай корректно: "
                            "'Извините, я не отвечаю на неэтичные вопросы. Задайте вопрос по работе сайта или трейдингу.' "
                            "3. Отвечай кратко, чётко и по делу на русском языке."
                        )
                    },
                    {"role": "user", "content": user_message}
                ],
                temperature=0.4,
                max_tokens=350
            )
            return jsonify({"reply": completion.choices[0].message.content})
        except Exception:
            pass

    return jsonify({"reply": "Сервер обработки ИИ временно занят. Попробуйте сформулировать вопрос иначе."})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
