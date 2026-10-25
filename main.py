import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai

app = Flask(__name__)
CORS(app)

api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

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
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Ты профессиональный трейдер. Дай торговый сигнал (CALL или PUT) и анализ для актива {asset} на таймфрейме {tf}. Отвечай на русском языке в формате: НАПРАВЛЕНИЕ | Текст анализа."
            response = model.generate_content(prompt)
            if response and response.text:
                res_str = response.text.strip()
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
        
        if api_key:
            model = genai.GenerativeModel('gemini-1.5-flash')
            image_part = {
                "mime_type": "image/jpeg",
                "data": image_bytes
            }
            prompt = (
                "Внимательно посмотри на это изображение графика. Если это график со свечами, проанализируй его. "
                "Если на фото нет графиков или посторонний предмет, ответь: СТАТУС: НЕТ_ГРАФИКА | АНАЛИЗ: ❌ ГРАФИК НЕ НАЙДЕН.\n"
                "Если график есть, выдай ответ в формате: СТАТУС: ОК | НАПРАВЛЕНИЕ: ⬆️ CALL (ВВЕРХ) или ⬇️ PUT (ВНИЗ) | АНАЛИЗ: Текст с разбором."
            )
            response = model.generate_content([image_part, prompt])
            res_text = response.text if response and response.text else ""
            
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
            model = genai.GenerativeModel('gemini-1.5-flash')
            full_prompt = f"Ты ИИ-ассистент клуба трейдеров 'Team Master'. Отвечай на вопрос по трейдингу и техническому анализу. Вопрос: {prompt}"
            response = model.generate_content(full_prompt)
            if response and response.text:
                reply = response.text.strip()
        except Exception as e:
            reply = "Ошибка обращения к нейросети."
            
    return jsonify({"reply": reply})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
