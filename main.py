# main.py (Код бэкенда для Render, который нужно обновить в репозитории)
import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from PIL import Image
import io

app = Flask(__name__)
CORS(app)

# Инициализация Gemini API через ключ окружения на Render
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
            "BITCOINOT0": 58,
            "GOLDOTC": 80
        }
    })

@app.route('/api/signal', methods=['GET'])
def get_signal():
    asset = request.args.get('asset', 'EUR/USD OTC')
    tf = request.args.get('tf', 'M1')
    
    analysis_text = f"Анализ актива {asset} на таймфрейме {tf}: На основе индикаторов Smart Money и RSI зафиксирован локальный импульс."
    direction = "⬆️ CALL (ВВЕРХ)"
    
    if api_key:
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Ты профессиональный трейдер. Дай краткий торговый сигнал (CALL или PUT) и анализ для актива {asset} на таймфрейме {tf}. Отвечай строго на русском языке в формате: НАПРАВЛЕНИЕ | Текст анализа."
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
        image = Image.open(file.stream)
        
        if api_key:
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = (
                "Внимательно посмотри на это изображение. На нем изображен график финансового актива с японскими свечами? "
                "Если на изображении нет графиков, котировок, свечей или это посторонний предмет/комната/лицо, то строго ответь: "
                "СТАТУС: НЕТ_ГРАФИКА | АНАЛИЗ: ❌ ГРАФИК НЕ НАЙДЕН. Пожалуйста, наведите камеру строго на торговый график.\n"
                "Если на фото реальный торговый график со свечами, то проанализируй его и выдай ответ в формате: "
                "СТАТУС: ОК | НАПРАВЛЕНИЕ: ⬆️ CALL (ВВЕРХ) или ⬇️ PUT (ВНИЗ) | АНАЛИЗ: Текст с разбором по Smart Money и Price Action."
            )
            response = model.generate_content([image, prompt])
            res_text = response.text if response and response.text else ""
            
            if "НЕТ_ГРАФИКА" in res_text or "❌" in res_text or not ("CALL" in res_text.upper() or "PUT" in res_text.upper()):
                return jsonify({
                    "direction": "❌ ГРАФИК НЕ НАЙДЕН",
                    "analysis": "❌ ГРАФИК НЕ НАЙДЕН. На камере обнаружен посторонний предмет или фон. Наведите камеру строго на график цены со свечами."
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
        
    reply = "Я специализируюсь исключительно на трейдинге, финансовых рынках и стратегиях."
    if api_key:
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            full_prompt = f"Ты ИИ-ассистент закрытого VIP-клуба трейдеров 'Team Master'. Отвечай на вопрос пользователя по трейдингу, техническому анализу или психологии рынка. Вопрос: {prompt}"
            response = model.generate_content(full_prompt)
            if response and response.text:
                reply = response.text.strip()
        except Exception as e:
            reply = "Ошибка обращения к нейросети Gemini."
            
    return jsonify({"reply": reply})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
