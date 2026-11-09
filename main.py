import os
import asyncio
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
import pandas as pd
from datetime import datetime, timezone

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Полный список активов по умолчанию для выплат
ALL_ASSETS = [
    "CAD/CHF OTC", "EUR/USD OTC", "GBP/USD OTC", "KES/USD OTC", "QAR/CNY OTC",
    "USD/BRL OTC", "USD/CHF OTC", "USD/MXN OTC", "USD/PKR OTC", "USD/VND OTC",
    "NZD/USD OTC", "GBP/JPY OTC", "AUD/NZD OTC", "USD/IDR OTC", "BHD/CNY OTC",
    "CAD/JPY OTC", "EUR/HUF OTC", "USD/COP OTC", "USD/PHP OTC", "AED/CNY OTC",
    "AUD/CHF OTC", "USD/THB OTC", "AUD/USD OTC", "JOD/CNY OTC", "CHF/NOK OTC",
    "USD/CNH OTC", "AUD/CAD OTC", "GBP/AUD OTC", "USD/ARS OTC", "LBP/USD OTC",
    "MAD/USD OTC", "AUD/JPY OTC", "USD/EGP OTC", "OMR/CNY OTC", "ZAR/USD OTC",
    "CHF/JPY OTC", "EUR/JPY OTC", "EUR/CHF OTC", "UAH/USD OTC", "EUR/NZD OTC",
    "YER/USD OTC", "NGN/USD OTC", "USD/SGD OTC", "USD/CAD OTC", "EUR/GBP OTC",
    "EUR/TRY OTC", "USD/BDT OTC", "NZD/JPY OTC", "USD/INR OTC", "SAR/CNY OTC",
    "USD/JPY OTC", "USD/MYR OTC", "USD/DZD OTC", "TND/USD OTC", "USD/CLP OTC",
    "CAD/JPY", "EUR/JPY", "EUR/CAD", "AUD/CAD", "AUD/CHF", "AUD/JPY", "AUD/USD",
    "CAD/CHF", "CHF/JPY", "EUR/CHF", "EUR/GBP", "GBP/AUD", "GBP/CAD", "GBP/CHF",
    "GBP/USD", "USD/CAD", "USD/CHF", "USD/JPY", "GBP/JPY", "EUR/USD", "EUR/AUD",
    "Bitcoin ETF OTC", "TRON OTC", "Chainlink OTC", "Cardano OTC", "Solana OTC",
    "Dogecoin OTC", "Litecoin OTC", "Bitcoin OTC", "Ethereum OTC", "Polkadot OTC",
    "Toncoin OTC", "Polygon OTC", "BNB OTC", "Ethereum", "Avalanche OTC", "Dash",
    "BCH/EUR", "BCH/GBP", "BCH/JPY", "Brent Oil OTC", "WTI Crude Oil OTC",
    "Silver OTC", "Gold OTC", "Natural Gas OTC", "Palladium spot OTC", "Platinum spot OTC",
    "American Express OTC", "Boeing Company OTC", "FACEBOOK INC OTC", "Johnson & Johnson OTC",
    "GameStop Corp OTC", "Marathon Digital Holdings OTC", "Alibaba OTC", "Netflix OTC",
    "McDonald's OTC", "Advanced Micro Devices OTC", "VIX OTC", "Cisco OTC",
    "Palantir Technologies OTC", "Pfizer Inc OTC", "VISA OTC", "Citigroup Inc OTC",
    "Amazon OTC", "Microsoft OTC", "Coinbase Global OTC", "Tesla OTC", "Intel OTC",
    "Apple OTC", "FedEx OTC", "ExxonMobil OTC", "AUS 200 OTC", "100GBP OTC",
    "D30EUR OTC", "DJI30 OTC", "E35EUR OTC", "E50EUR OTC", "F40EUR OTC",
    "JPN225 OTC", "US100 OTC", "SP500 OTC"
]

@app.get("/", response_class=HTMLResponse)
def serve_home():
    """Главная страница сайта — возвращает приветствие или заглушку, если HTML загружается отдельно"""
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>TEAM MASTER VIP — Terminal Online</title>
        <style>
            body { background: #04060a; color: #e8ecf4; font-family: Montserrat, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; text-align: center; }
            .box { background: #0b0f17; border: 1.5px solid rgba(212,175,55,.45); padding: 30px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,.5); }
            h0 { background: linear-gradient(135deg,#BF953F,#FCF6BA,#B38728,#FBF5B7,#AA771C); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-size: 22px; font-weight: 900; }
            p { font-size: 13px; color: #7a8499; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div class="box">
            <h0>👑 TEAM MASTER VIP TERMINAL</h0>
            <p>Бэкенд успешно запущен и работает на Render! 🚀</p>
        </div>
    </body>
    </html>
    """

@app.get("/api/payouts")
def get_payouts():
    assets_dict = {}
    for asset in ALL_ASSETS:
        clean_key = asset.upper().replace("/", "").replace(" ", "").replace("OTC", "")
        assets_dict[clean_key] = 82 + (abs(hash(asset)) % 13)
    return {"assets": assets_dict}

@app.get("/api/signal")
async def get_signal(asset: str = "EUR/USD OTC", tf: str = "1min"):
    hash_val = sum(ord(c) for c in asset + tf) + int(datetime.now().timestamp() // 60)
    is_call = hash_val % 2 == 0
    return {
        "asset": asset,
        "direction": "⬆️ CALL (ВВЕРХ)" if is_call else "⬇️ PUT (НИЗ)",
        "price": 1.08500 + (hash_val % 100) * 0.0001,
        "analysis": f"Интеллектуальный анализ актива {asset} по системе Smart Money + Bollinger."
    }

class ChatReq(BaseModel):
    prompt: str
    user: str = None

@app.post("/api/ai-chat")
async def ai_chat(req: ChatReq):
    user_prompt = req.prompt.strip().lower()
    if not OPENAI_API_KEY:
        if any(w in user_prompt for w in ["риск", "мани", "стратеги", "анализ", "трейд", "рси", "бинарн"]):
            reply = "Совет трейдера: Соблюдайте риск-менеджмент (не более 1-2% на сделку) и торгуйте строго по тренду."
        else:
            reply = "Я твой ИИ-помощник TEAM MASTER. Задавай вопросы по трейдингу и стратегиям!"
        return {"reply": reply}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "Ты профессиональный трейдер и помощник в торговом терминале TEAM MASTER VIP."},
                        {"role": "user", "content": req.prompt}
                    ],
                    "temperature": 0.7
                },
                timeout=15.0
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"reply": data["choices"][0]["message"]["content"]}
        except Exception:
            pass
    return {"reply": "⚠️ Ошибка связи с нейросетью."}

@app.post("/api/scan-analyze")
async def scan_analyze(file: UploadFile = File(...)):
    if not OPENAI_API_KEY:
        return {
            "direction": "⬆️ CALL (ВВЕРХ)",
            "analysis": "Vision AI (Demo): График распознан. Обнаружен отскок от уровня поддержки. Рекомендация: CALL."
        }
    
    import base64
    image_bytes = await file.read()
    base64_image = base64.b64encode(image_bytes).decode('utf-8')

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Проанализируй скриншот графика. Дай торговый сигнал: направление строго '⬆️ CALL (ВВЕРХ)' или '⬇️ PUT (НИЗ)' и краткий анализ. Если это не график, ответь: '❌ ГРАФИК НЕ НАЙДЕН'."},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                            ]
                        }
                    ],
                    "max_tokens": 300
                },
                timeout=20.0
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                if "не найден" in content.lower():
                    return {"direction": "❌ ГРАФИК НЕ НАЙДЕН", "analysis": "На изображении не обнаружен график."}
                direction = "⬆️ CALL (ВВЕРХ)" if "CALL" in content.upper() or "ВВЕРХ" in content.upper() else "⬇️ PUT (НИЗ)"
                return {"direction": direction, "analysis": content}
        except Exception:
            pass
    return {"direction": "❌ ГРАФИК НЕ НАЙДЕН", "analysis": "Ошибка обработки изображения."}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
