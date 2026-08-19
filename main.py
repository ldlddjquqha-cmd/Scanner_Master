import os
import json
import logging
import asyncio
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse
import uvicorn
import PIL.Image
import io
import google.generativeai as genai

# Настройка API ключа Google Gemini
# Ключ можно задать в переменной окружения GEMINI_API_KEY или вставить прямо в код
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "ВАШ_GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TeamMaster Core")

class Analyzer:
    """Движок анализа скриншота графика с использованием Google Gemini AI"""
    def __init__(self):
        # Используем быстрый визуальный движок Gemini 1.5 Flash
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    async def compute(self, image_bytes: bytes, config: dict) -> str:
        try:
            # Открываем изображение из байтов
            image = PIL.Image.open(io.BytesIO(image_bytes))

            # Промпт для ИИ
            prompt = f"""
Ты — профессиональный аналитик финансовых рынков и бинарных опционов.
Проанализируй предоставленный график.

Параметры анализа:
- Стратегия: {config.get('стратегия', 'Smart Money')}
- Таймфрейм: {config.get('интервал', 'M2')}
- Экспирация: {config.get('экспирация', '5м')}

Дай четкий торговый сигнал в формате:
--- TEAM MASTER SIGNAL V4.0 ---
СТРАТЕГИЯ: {config.get('стратегия')}
ТАЙМФРЕЙМ: {config.get('интервал')}
ЭКСПИРАЦИЯ: {config.get('экспирация')}
-------------------------------
ВЕРДИКТ: [BUY (ВВЕРХ) или SELL (ВНИЗ)]
ПРОХОДИМОСТЬ: [Процент уверенности от 65% до 90%]%
ПРИЧИНА: [Краткое техническое обоснование по выбранной стратегии, например: Отработка зоны POI / Снятие ликвидности / Тренд]
ВХОД: Прямо сейчас.
"""

            # Отправляем запрос в Gemini
            response = await asyncio.to_thread(self.model.generate_content, [prompt, image])
            return response.text

        except Exception as e:
            logging.error(f"Ошибка Gemini API: {e}")
            return (
                f"--- TEAM MASTER SIGNAL V4.0 ---\n"
                f"ОШИБКА АНАЛИЗА: Не удалось обработать график.\n"
                f"Проверьте GEMINI_API_KEY или формат изображения."
            )

core = Analyzer()

# --- ФРОНТЕНД (ИНТЕРФЕЙС) ---
HTML_UI = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #050505; color: #fff; font-family: monospace; display: flex; justify-content: center; padding: 20px; }
        .panel { background: #111; width: 100%; max-width: 500px; padding: 20px; border: 1px solid #333; border-radius: 10px; }
        .label { font-size: 11px; color: #888; margin-top: 10px; }
        .btn-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 5px; margin: 10px 0; }
        .btn { background: #1a1a1a; border: 1px solid #444; color: #aaa; padding: 10px 0; cursor: pointer; font-size: 10px; text-align: center; }
        .btn.active { background: #0d47a1; color: white; border-color: #2979ff; }
        .cmd-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 20px; }
        #console { background: #000; color: #00ff00; padding: 15px; margin-top: 20px; border: 1px solid #222; height: 220px; overflow-y: auto; white-space: pre-wrap; font-size: 12px; }
    </style>
</head>
<body>
    <div class="panel">
        <div style="text-align:center; margin-bottom:20px; font-weight:bold;">TEAM MASTER PRO</div>
        
        <div class="label">ТАЙМФРЕЙМ</div>
        <div class="btn-grid" id="int-g">
            <script>
                ['S5','S10','S15','S30','M1','M2','M3','M5','M10','M15'].forEach(t => 
                    document.write(`<div class="btn ${t=='M2'?'active':''}" onclick="set(this,'интервал')">${t}</div>`));
            </script>
        </div>

        <div class="label">ЭКСПИРАЦИЯ</div>
        <div class="btn-grid" id="exp-g" style="grid-template-columns:repeat(6,1fr)">
            <script>
                ['30с','1м','2м','3м','4м','5м'].forEach(t => 
                    document.write(`<div class="btn ${t=='5м'?'active':''}" onclick="set(this,'экспирация')">${t}</div>`));
            </script>
        </div>

        <div class="label">СТРАТЕГИЯ</div>
        <div class="btn-grid" style="grid-template-columns:repeat(5,1fr)">
            <script>
                ['Smart Money','ICT','PA','Scalp','Trend'].forEach(t => 
                    document.write(`<div class="btn ${t=='Smart Money'?'active':''}" onclick="set(this,'стратегия')">${t}</div>`));
            </script>
        </div>

        <input type="file" id="f-up" accept="image/*" style="display:none">
        <div class="cmd-row">
            <button style="background:#1b5e20; color:white; border:none; padding:15px; cursor:pointer;" onclick="document.getElementById('f-up').click()">ЗАПУСК</button>
            <button style="background:#b71c1c; color:white; border:none; padding:15px; cursor:pointer;" onclick="location.reload()">СБРОС</button>
        </div>
        <div id="console">>> СИСТЕМА ИНИЦИАЛИЗИРОВАНА...</div>
    </div>

    <script>
        let cfg = {интервал: 'M2', экспирация: '5м', стратегия: 'Smart Money'};
        function set(el, type) {
            el.parentElement.querySelectorAll('.btn').forEach(b => b.classList.remove('active'));
            el.classList.add('active');
            cfg[type] = el.innerText;
        }
        document.getElementById('f-up').onchange = async (e) => {
            if (!e.target.files[0]) return;
            document.getElementById('console').innerText = ">> ОТПРАВКА СНИМКА В GEMINI AI...\\n>> АНАЛИЗ ГРАФИКА...";
            
            const fd = new FormData(); 
            fd.append('file', e.target.files[0]); 
            fd.append('cfg', JSON.stringify(cfg));
            
            try {
                const r = await fetch('/analyze', {method:'POST', body:fd});
                const j = await r.json(); 
                document.getElementById('console').innerText = j.text;
            } catch (err) {
                document.getElementById('console').innerText = ">> ОШИБКА ПОДКЛЮЧЕНИЯ К СЕРВЕРУ";
            }
        };
    </script>
</body>
</html>
"""

@app.get("/")
async def get_page(): 
    return HTMLResponse(HTML_UI)

@app.post("/analyze")
async def analyze(file: UploadFile = File(...), cfg: str = Form(...)):
    image_bytes = await file.read()
    config_data = json.loads(cfg)
    
    result_text = await core.compute(image_bytes, config_data)
    return {"text": result_text}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
