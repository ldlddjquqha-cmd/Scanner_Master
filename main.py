import hashlib
import aiohttp
from flask import Flask, request, jsonify

app = Flask(__name__)

# Твои данные из партнерки
API_TOKEN = "Zc4X9zu0EMrqbPuLy3tN"
PARTNER_ID = "850173"

@app.route('/check-user', methods=['POST'])
async def check_user():
    data = request.get_json()
    user_id = str(data.get("user_id", "")).strip()
    
    # Проверяем, что ввели только цифры
    if not user_id.isdigit():
        return jsonify({"success": False, "error": "Неверный формат ID"})
    
    # Формируем хэш строго по инструкции API
    raw_hash_string = f"{user_id}:{PARTNER_ID}:{API_TOKEN}"
    api_hash = hashlib.md5(raw_hash_string.encode('utf-8')).hexdigest()
    
    url = f"https://affiliate.pocketoption.com/api/user-info/{user_id}/{PARTNER_ID}/{api_hash}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as response:
                if response.status != 200:
                    return jsonify({"success": False, "error": "Ошибка связи с сервером брокера"})
                
                api_data = await response.json()
                print(f"Ответ API для ID {user_id}: {api_data}")
                
                # Если сервер вернул ошибку или пустой ответ — отсекаем
                if not api_data or "error" in api_data or api_data.get("status") == "error":
                    return jsonify({"success": False, "error": "ID не найден или нет депозита"})
                
                # Всё ок, доступ разрешен
                return jsonify({"success": True, "message": "Доступ разрешен"})
                
    except Exception as e:
        print(f"Ошибка запроса: {e}")
        return jsonify({"success": False, "error": "Ошибка сервера проверки"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
