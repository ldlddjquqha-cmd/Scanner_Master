import os
import hashlib
from flask import Flask, render_template_string, request, jsonify
import requests
from groq import Groq

app = Flask(__name__)

# ==================== НАСТРОЙКИ И ДАННЫЕ БОССА ====================
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '7960762468:AAEu1rItSoIL9Q7cHtY-zA5kCr3UmlDWSLQ')
BOSS_TELEGRAM_ID = os.environ.get('BOSS_TELEGRAM_ID', '109386966')

# Настройки партнерки Pocket Option
POCKET_PARTNER_ID = os.environ.get('POCKET_PARTNER_ID', '109386966')  # Ваш Partner ID
POCKET_API_TOKEN = os.environ.get('POCKET_API_TOKEN', 'Zc4X9zu0EMrqbPuLy3tN')
REF_LINK = os.environ.get('REF_LINK', 'https://pocketoption.com/register?utm_source=team_master_vip')

GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Временное хранилище заблокированных пользователей в памяти
BLOCKED_USERS = set()
# ===================================================================


def check_partner_trader(user_id):
    """ Проверка трейдера через официальный API партнерки Pocket Option """
    try:
        # Формируем MD5 хеш: md5("{user_id}:{partner_id}:{api_token}")
        raw_hash_string = f"{user_id}:{POCKET_PARTNER_ID}:{POCKET_API_TOKEN}"
        hash_md5 = hashlib.md5(raw_hash_string.encode('utf-8')).hexdigest()

        url = f"https://affiliate.pocketoption.com/api/user-info/{user_id}/{POCKET_PARTNER_ID}/{hash_md5}"
        response = requests.get(url, timeout=7)
        
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Ошибка проверки API партнерки: {e}")
        return None


@app.route('/')
def index():
    if os.path.exists('templates/index.html'):
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            return f.read()
    elif os.path.exists('index.html'):
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()
    return "Шаблон index.html не найден!"


# --- ПРОВЕРКА ТРЕЙДЕРА И ОТПРАВКА УВЕДОМЛЕНИЯ БОССУ С КНОПКАМИ ---
@app.route('/api/verify_trader', methods=['POST'])
def verify_trader():
    try:
        trader_id = request.form.get('trader_id', '').strip()
        if not trader_id:
            return jsonify({"status": "error", "message": "Укажите ID трейдера"}), 400

        if trader_id in BLOCKED_USERS:
            return jsonify({"status": "blocked", "message": "Этот ID заблокирован в системе."}), 403

        # Запрос к Pocket Option API
        trader_data = check_partner_trader(trader_id)

        # Сообщение с кнопками блокировки для Босса в Telegram
        if TELEGRAM_BOT_TOKEN:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            
            status_text = f"✅ <b>Найден в партнерке</b>" if trader_data else "⚠️ <b>Не найден / Ошибка API</b>"
            
            message_text = (
                f"👑 <b>БОСС, НОВЫЙ ТРЕЙДЕР ЗАПРОСИЛ ДОСТУП!</b>\n\n"
                f"🆔 ID Трейдера: <code>{trader_id}</code>\n"
                f"📊 Статус партнерки: {status_text}\n"
            )

            # Интерактивные inline-кнопки
            inline_keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "⛔ Заблокировать НАВСЕГДА", "callback_data": f"block_{trader_id}"},
                        {"text": "✅ Разблокировать", "callback_data": f"unblock_{trader_id}"}
                    ]
                ]
            }

            payload = {
                "chat_id": BOSS_TELEGRAM_ID,
                "text": message_text,
                "parse_mode": "HTML",
                "reply_markup": inline_keyboard
            }
            requests.post(url, json=payload, timeout=5)

        return jsonify({
            "status": "success",
            "trader_id": trader_id,
            "partner_info": trader_data
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- ВЕБХУК TELEGRAM ДЛЯ ОБРАБОТКИ НАЖАТИЙ НА КНОПКИ ---
@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        if "callback_query" in data:
            callback = data["callback_query"]
            callback_id = callback["id"]
            action_data = callback["data"]
            chat_id = callback["message"]["chat"]["id"]
            message_id = callback["message"]["message_id"]

            # Проверяем, что кнопил именно Босс
            if str(chat_id) == str(BOSS_TELEGRAM_ID):
                if action_data.startswith("block_"):
                    target_id = action_data.split("block_")[1]
                    BLOCKED_USERS.add(target_id)
                    res_text = f"⛔ Трейдер ID {target_id} ЗАБЛОКИРОВАН НАВСЕГДА!"
                elif action_data.startswith("unblock_"):
                    target_id = action_data.split("unblock_")[1]
                    BLOCKED_USERS.discard(target_id)
                    res_text = f"✅ Трейдер ID {target_id} РАЗБЛОКИРОВАН!"

                # Всплывающее уведомление в Telegram
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={
                    "callback_query_id": callback_id,
                    "text": res_text,
                    "show_alert": True
                })

                # Обновляем текст сообщения у Босса
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"👑 <b>Управление доступом</b>\n\n{res_text}",
                    "parse_mode": "HTML"
                })

        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({"status": "error"}), 500


# --- ЛОГИРОВАНИЕ И ОТПРАВКА СИГНАЛОВ ---
@app.route('/api/log_signal', methods=['POST'])
def log_signal():
    try:
        asset = request.form.get('asset', 'EUR/USD OTC')
        direction = request.form.get('direction', 'CALL')
        timeframe = request.form.get('timeframe', 'M1')
        is_hedge = request.form.get('is_hedge', 'false') == 'true'

        hedge_text = " ⚠️ [ПЕРЕКРЫТИЕ / ДОГОН]" if is_hedge else ""
        
        text = f"🎯 <b>НОВЫЙ СИГНАЛ VIP ТЕРМИНАЛА</b>{hedge_text}\n\n" \
               f"📊 Актив: <b>{asset}</b>\n" \
               f"📈 Направление: <b>{direction}</b>\n" \
               f"⏳ Время: <b>{timeframe}</b>\n\n" \
               f"🔗 <a href='{REF_LINK}'>👉 ОТКРЫТЬ СДЕЛКУ НА POCKET OPTION</a>"

        if TELEGRAM_BOT_TOKEN:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": BOSS_TELEGRAM_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            requests.post(url, json=payload, timeout=5)

        return jsonify({"status": "success", "message": "Signal logged"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- ЧАТ С ИИ GROQ ---
@app.route('/ai_chat', methods=['POST'])
def ai_chat():
    try:
        user_message = request.form.get('message', '')
        if not user_message:
            return jsonify({"reply": "Введите сообщение."}), 400

        if groq_client:
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": f"Ты ИИ-ассистент терминала TEAM MASTER VIP. Отвечай кратко и профессионально по трейдингу. Рекомендуй регистрацию по партнерке: {REF_LINK}"
                    },
                    {"role": "user", "content": user_message}
                ],
                temperature=0.6,
                max_tokens=350
            )
            reply = completion.choices[0].message.content
        else:
            reply = f"🤖 Для торговли регистрируйтесь по нашей партнерке: {REF_LINK}"

        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": f"⚠️ Регистрируйтесь по ссылке: {REF_LINK}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
