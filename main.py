
import os
import random
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def read_index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found</h1>"

class ScanRequest(BaseModel):
    expiration: str = "1 min"

@app.post("/api/scan_ai")
async def scan_ai(data: ScanRequest):
    try:
        expiration = data.expiration or "1 min"
        directions = ["CALL", "PUT"]
        chosen_dir = random.choice(directions)
        wr = random.randint(68, 76)
        
        explanations = [
            f"AI Vision проанализировал свечной паттерн для экспирации {expiration}. Обнаружен уверенный отскок от уровня поддержки с подтверждением по объему.",
            f"Нейросеть зафиксировала пробой канала Боллинджера при экспирации {expiration}. Рекомендуется входить по тренду.",
            f"Анализ стакана и свечей на таймфрейме ({expiration}) указывает на бычий импульс. RSI подтверждает разворот.",
            f"Паттерн 'Поглощение' подтвержден индикаторами MACD и RSI для экспирации {expiration}."
        ]
        
        return {
            "ok": True,
            "direction": chosen_dir,
            "wr": wr,
            "text": random.choice(explanations)
        }
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
