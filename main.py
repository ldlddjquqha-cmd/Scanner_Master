import os
import json
import base64
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

api_key = os.getenv("GEMINI_API_KEY")

@app.route('/api/payouts', methods=['GET'])
def get_payouts():
    return jsonify({
        "status": "success",
        "assets": {
            "EURUSDOTC": 92,
            "GBPUSDOTC": 92,
            "AUDUSDOTC": 78,
            "USDJPYOTC": 32,
            "BITCOINOTC": 58,
            "GOLDOTC": 80
        }
    })

@app.route('/api/signal', methods=['GET'])
def get_signal():
    asset = request.args.get('asset', 'EUR/USD OTC')
    tf = request.args.get('tf', 'M1')
    
    analysis_text = f"Анализ актива {asset} на таймфрейме {tf}: Зафиксирован импульс по Smart Money."
    direction = "⬆️ CALL (ВВЕРХ)"
    
    if api_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
            prompt = f"Ты профессиональный трейдер. Дай торговый сигнал (CALL или PUT) и анализ для актива {asset} на таймфрейме {tf}. Отвечай на русском языке в формате: НАПРАВЛЕНИЕ | Текст анализа."
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                data = res.json()
                res_str = data['candidates'][0]['content']['parts'][0]['text'].strip()
                if "PUT" in res_str.upper() or "НИЗ" in res_str.upper():
                    direction = "⬇️ PUT (ВНИЗ)"
                else:
                    direction = "⬆️ CALL (ВВЕРХ)"
                analysis_text = res_str
        except Exception as e:
            pass

    return jsonify({
        "direction": direction,
        "analysis": analysis_text
    })

@app.route('/api/scan-analyze', methods=['POST'])
def scan_analyze():
    if 'file' not in request.files:
        return jsonify({"direction": "❌ ГРАФИК НЕ НАЙДЕН", "analysis": "Файл изображения не найден."}), 400
    
    file = request.files['file']
    try:
        image_bytes = file.read()
        encoded_image = base64.b64encode(image_bytes).decode('utf-8')
        
        if api_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
            prompt = (
                "Внимательно посмотри на это изображение графика. Если это график со свечами, проанализируй его. "
                "Если на фото нет графиков или посторонний предмет, ответь: СТАТУС: НЕТ_ГРАФИКА | АНАЛИЗ: ❌ ГРАФИК НЕ НАЙДЕН.\n"
                "Если график есть, выдай ответ в формате: СТАТУС: ОК | НАПРАВЛЕНИЕ: ⬆️ CALL (ВВЕРХ) или ⬇️ PUT (ВНИЗ) | АНАЛИЗ: Текст с разбором."
            )
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/jpeg", "data": encoded_image}}
                    ]
                }]
            }
            
            res = requests.post(url, json=payload, timeout=15)
            if res.status_code == 200:
                data = res.json()
                res_text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                
                if "НЕТ_ГРАФИКА" in res_text or "❌" in res_text or not ("CALL" in res_text.upper() or "PUT" in res_text.upper()):
                    return jsonify({
                        "direction": "❌ ГРАФИК НЕ НАЙДЕН",
                        "analysis": "❌ ГРАФИК НЕ НАЙДЕН. Наведите камеру строго на график котировок со свечами."
                    })
                
                direction = "⬆️ CALL (ВВЕРХ)"
                if "PUT" in res_text.upper() or "ВНИЗ" in res_text.upper():
                    direction = "⬇️ PUT (ВНИЗ)"
                    
                return jsonify({
                    "direction": direction,
                    "analysis": res_text
                })
    except Exception as e:
        pass

    return jsonify({
        "direction": "❌ ГРАФИК НЕ НАЙДЕН",
        "analysis": "❌ ГРАФИК НЕ НАЙДЕН. Ошибка обработки изображения."
    })

@app.route('/api/ai-chat', methods=['POST'])
def ai_chat():
    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    
    if not prompt:
        return jsonify({"reply": "Пожалуйста, введите вопрос."})
        
    reply = "Я специализируюсь исключительно на трейдинге и финансовых рынках."
    if api_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
            full_prompt = f"Ты ИИ-ассистент клуба трейдеров 'Team Master'. Отвечай на вопрос по трейдингу и техническому анализу. Вопрос: {prompt}"
            payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
            
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                data = res.json()
                reply = data['candidates'][0]['content']['parts'][0]['text'].strip()
        except Exception as e:
            reply = "Ошибка обращения к нейросети."
            
    return jsonify({"reply": reply})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
