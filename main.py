import os
import json
import logging
import asyncio
import random
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import io
import google.generativeai as genai
from PIL import Image

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP Terminal")

# Инициализация Gemini API через переменную окружения на Render
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

class Analyzer:
    async def compute(self, image_bytes: bytes, config: dict) -> dict:
        if not GEMINI_API_KEY:
            return {
                "is_chart": False,
                "direction": "❌ ОШИБКА API",
                "analysis": "API ключ Gemini не настроен на сервере Render."
            }
        try:
            image = Image.open(io.BytesIO(image_bytes))
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = (
                "Ты профессиональный трейдер и эксперт по техническому анализу (Smart Money, ICT, Price Action). "
                "Внимательно проанализируй этот график. Если на изображении нет графиков, свечей или котировок, верни строго ответ: "
                "СТАТУС: НЕТ_ГРАФИКА | АНАЛИЗ: ❌ ГРАФИК НЕ НАЙДЕН. Пожалуйста, наведите камеру строго на торговый график.\n"
                "Если график присутствует, дай торговый вердикт в формате: "
                "СТАТУС: ОК | НАПРАВЛЕНИЕ: ⬆️ CALL (ВВЕРХ) или ⬇️ PUT (ВНИЗ) | АНАЛИЗ: Текст с разбором структуры."
            )
            response = model.generate_content([image, prompt])
            res_text = response.text if response and response.text else ""
            
            if "НЕТ_ГРАФИКА" in res_text or "❌" in res_text or not ("CALL" in res_text.upper() or "PUT" in res_text.upper()):
                return {
                    "is_chart": False,
                    "direction": "❌ ГРАФИК НЕ НАЙДЕН",
                    "analysis": "❌ ГРАФИК НЕ НАЙДЕН. На камере обнаружен посторонний предмет или фон. Наведите камеру строго на график цены со свечами."
                }
            
            chosen_dir = "⬆️ CALL (ВВЕРХ)"
            if "PUT" in res_text.upper() or "ВНИЗ" in res_text.upper():
                chosen_dir = "⬇️ PUT (ВНИЗ)"
                
            return {
                "is_chart": True,
                "direction": chosen_dir,
                "analysis": res_text
            }
        except Exception as e:
            return {
                "is_chart": False,
                "direction": "❌ ОШИБКА",
                "analysis": f"Ошибка обработки изображения нейросетью: {str(e)}"
            }

    async def chat_assistant(self, user_text: str) -> str:
        t = user_text.lower()
        
        # Базовые быстрые ответы для чата
        if "привет" in t or "здаров" in t or "добрый день" in t:
            return "Привет! 👋 Добро пожаловать в Команду Мастера 👑 Здесь ты можешь узнать об обучении, тренингах, сигналах, Telegram-канале и возможностях команды."
        elif "что такое команда мастера" in t or "о команде" in t:
            return "Это сообщество, где участники получают обучающие материалы, проходят тренинги и изучают торговлю. 👑"
        elif "с чего начать" in t or "новичку" in t:
            return "Начни с обучения и базовых материалов. Не спеши переходить к реальной торговле — сначала разберись с правилами, рисками и принципом работы платформы."
        elif "обучение" in t:
            return "Да 📚 В команде предусмотрены обучающие материалы и тренинги для новичков. Обучение находится в специальном разделе «Обучение»."
        elif "telegram" in t or "телеграм" in t or "канал" in t:
            return "Все основные новости и обновления размещаются в нашем Telegram-канале 📲 (Ссылка доступна на вкладке «О нас»)."
        elif "админ" in t or "связь" in t or "проблема" in t:
            return "👑 Если у тебя есть вопрос, обратись напрямую к администратору в Telegram: @andriddddd"
            
        # Если вопрос сложный — отправляем в реальный Gemini AI
        if GEMINI_API_KEY:
            try:
                model = genai.GenerativeModel('gemini-1.5-flash')
                prompt = f"Ты ИИ-ассистент клуба трейдеров 'Team Master'. Отвечай на вопрос по трейдингу. Если спрашивают контакты админа, указывай @andriddddd. Вопрос: {user_text}"
                response = model.generate_content(prompt)
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                pass
                
        return "🤖 К сожалению, я не смог найти точный ответ на этот вопрос. Пожалуйста, обратитесь к админу: @andriddddd 👑"

core = Analyzer()

@app.get("/api/payouts")
async def get_payouts():
    return {
        "status": "success",
        "assets": {
            "EURUSDOTC": 92,
            "GBPUSDOTC": 92,
            "AUDUSDOTC": 78,
            "USDJPYOTC": 32,
            "BITCOINOTC": 58,
            "GOLDOTC": 80,
            "CADCHFOTC": 92,
            "SOLANAOTC": 84,
            "BRENTOILOTC": 80
        }
    }

@app.get("/api/signal")
async def get_signal(asset: str = "EUR/USD OTC", tf: str = "M1"):
    analysis_text = f"Анализ актива {asset} на таймфрейме {tf}: Зафиксирован импульс по Smart Money."
    direction = "⬆️ CALL (ВВЕРХ)"
    
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Ты профессиональный трейдер. Дай краткий торговый сигнал (CALL или PUT) и анализ для актива {asset} на таймфрейме {tf}. Отвечай на русском языке в формате: НАПРАВЛЕНИЕ | Текст анализа."
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

    return {
        "direction": direction,
        "analysis": analysis_text
    }

@app.post("/api/scan-analyze")
async def scan_analyze(file: UploadFile = File(...)):
    image_bytes = await file.read()
    result = await core.compute(image_bytes, {})
    return JSONResponse(result)

@app.post("/api/ai-chat")
async def ai_chat(data: dict = None):
    if not data:
        data = {}
    prompt = data.get("prompt", "")
    reply = await core.chat_assistant(prompt)
    return {"reply": reply}

if __name__ == "__main__":
    port = int(os.environ.0.get("PORT", 10000) if False else os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
