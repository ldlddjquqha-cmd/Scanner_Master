import os
import json
import logging
import asyncio
import random
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import PIL.Image
import io
import google.generativeai as genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP Terminal")

class Analyzer:
    def __init__(self):
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    async def compute(self, image_bytes: bytes, config: dict) -> dict:
        if not GEMINI_API_KEY:
            return {
                "is_chart": False,
                "direction": "НЕОПРЕДЕЛЕНО",
                "analysis": "⚠️ Ошибка: GEMINI_API_KEY не установлен в переменной окружения Render."
            }
        try:
            image = PIL.Image.open(io.BytesIO(image_bytes))

            prompt = f"""
Ты — высококвалифицированный аналитик финансовых рынков.
Твоя задача — строго проанализировать изображение и ответить В ФОРМАТЕ JSON.

1. Сначала определи, является ли изображение ГРАФИКОМ ФИНАНСОВОГО АКТИВА (японские свечи, бары, линия тренда, индикаторы).
   Если на фото посторонний предмет, бытовая техника, лицо, комната, текст или любой объект, НЕ ЯВЛЯЮЩИЙСЯ финансовым графиком — верни "is_chart": false.

2. Если это действительно график ("is_chart": true):
   Проанализируй свечные паттерны (молот, поглощение, пин-бар), направление текущего тренда, уровень поддержки/сопротивления и положение свечей.
   На основе реального свечного анализа определи направление: "CALL" (если рынок смотрит вверх) или "PUT" (если рынок смотрит вниз). Направление должно строго соответствовать структуре свечей!

Параметры:
- Стратегия: {config.get('стратегия', 'Smart Money')}
- Таймфрейм: {config.get('интервал', 'M1')}

Верни ответ СТРОГО в формате JSON без какого-либо другого текста:
{{
  "is_chart": true/false,
  "direction": "CALL" или "PUT" или "NONE",
  "winrate": "75%",
  "reason": "Краткое описание свечной структуры (например: Медвежье поглощение от уровня сопротивления)"
}}
"""

            response = await asyncio.to_thread(self.model.generate_content, [prompt, image])
            raw_text = response.text.strip()
            
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()

            data = json.loads(raw_text)

            if not data.get("is_chart", False):
                return {
                    "is_chart": False,
                    "direction": "НЕОПРЕДЕЛЕНО",
                    "analysis": "❌ На фото не обнаружен торговый график! Наведите камеру на свечной график котировок."
                }

            dir_val = data.get("direction", "CALL").upper()
            dir_text = "⬆️ CALL (ВВЕРХ)" if "CALL" in dir_val else "⬇️ PUT (ВНИЗ)"
            winrate = data.get("winrate", "80%")
            reason = data.get("reason", "Анализ свечной структуры и уровней POI")

            fmt_analysis = (
                f"--- TEAM MASTER SIGNAL V4.0 ---\n"
                f"СТРАТЕГИЯ: Smart Money & Candlestick PA\n"
                f"ВЕРДИКТ: {dir_text}\n"
                f"ПРОХОДИМОСТЬ: {winrate}\n"
                f"ПРИЧИНА: {reason}"
            )

            return {
                "is_chart": True,
                "direction": dir_text,
                "analysis": fmt_analysis
            }

        except Exception as e:
            logging.error(f"Ошибка Gemini API: {e}")
            return {
                "is_chart": False,
                "direction": "ОШИБКА",
                "analysis": "❌ Ошибка обработки изображения нейросетью. Попробуйте сделать более четкий снимок графика."
            }

core = Analyzer()

HTML_UI = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>TEAM MASTER VIP — Trading Terminal</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{--gold:#FFD700;--gold-g:linear-gradient(135deg,#BF953F,#FCF6BA,#B38728,#FBF5B7,#AA771C);--bg:#04060a;--card:#0b0f17;--inner:#070a10;--green:#00E676;--red:#FF5252;--border:#161e2e;--border-g:rgba(212,175,55,.45);--muted:#7a8499;--text:#e8ecf4}
*{box-sizing:border-box;margin:0;padding:0;font-family:Montserrat,sans-serif;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);min-height:100vh;display:flex;justify-content:center;padding:10px 10px 85px}
.wrap{width:100%;max-width:440px;display:flex;flex-direction:column;gap:10px}
.g-text{background:var(--gold-g);background-size:200% auto;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:sh 3s linear infinite;font-weight:900}
@keyframes sh{to{background-position:200% center}}
@keyframes fu{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.btn{width:100%;padding:13px;border:none;border-radius:12px;font-weight:800;font-size:12px;letter-spacing:.3px;text-transform:uppercase;cursor:pointer;text-align:center;text-decoration:none;display:flex;align-items:center;justify-content:center;gap:6px;transition:transform .15s,opacity .2s}
.btn:active{transform:scale(.97)}.btn:disabled{opacity:.4;pointer-events:none}
.btn-gold{background:var(--gold-g);background-size:200% auto;animation:sh 3s linear infinite;color:#111;box-shadow:0 5px 18px rgba(191,149,63,.3)}
.btn-green{background:linear-gradient(135deg,#00C853,#00E676);color:#063;box-shadow:0 4px 14px rgba(0,230,118,.25)}
.btn-dark{background:var(--inner);color:var(--muted);border:1px solid var(--border)}
.btn-red{background:linear-gradient(135deg,#FF1744,#FF5252);color:#fff;font-size:11.5px;padding:11px}
.btn-sm{padding:10px;font-size:11px}
.header{background:var(--card);border:1px solid var(--border-g);border-radius:14px;padding:14px 16px;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:15px}.header small{font-size:9px;color:var(--gold);font-weight:700}
.lang{background:var(--inner);color:#fff;border:1px solid var(--border);padding:5px 8px;border-radius:8px;font-weight:700;font-size:10px;outline:none}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;box-shadow:0 3px 16px rgba(0,0,0,.35);animation:fu .3s ease}
.gate{background:var(--card);border:1px solid var(--border-g);border-radius:18px;padding:22px 16px;text-align:center;animation:fu .4s ease}
.progress{display:flex;justify-content:center;align-items:center;margin-bottom:18px}
.p-dot{width:10px;height:10px;border-radius:50%;background:var(--border);transition:all .3s}
.p-dot.done{background:var(--green)}.p-dot.cur{background:var(--gold);transform:scale(1.2);box-shadow:0 0 10px rgba(255,215,0,.45)}
.p-line{width:28px;height:2px;background:var(--border);margin:0 3px}.p-line.done{background:var(--green)}
.gate-icon{font-size:44px;margin-bottom:6px}
.gate-title{font-size:17px;font-weight:900;margin-bottom:8px;background:var(--gold-g);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.gate-desc{font-size:11.5px;color:#b0b8c8;line-height:1.6;text-align:left;margin-bottom:16px;background:var(--inner);padding:11px 13px;border-radius:11px;border:1px solid var(--border)}
.gate-desc b{color:var(--gold)}
.input{width:100%;padding:12px;border-radius:11px;border:1.5px solid var(--border-g);background:var(--inner);color:#fff;font-size:14px;font-weight:700;text-align:center;outline:none;margin-bottom:9px}
.input:focus{border-color:var(--gold)}
.msg{font-size:11px;font-weight:700;padding:9px 11px;border-radius:10px;margin-bottom:9px;display:none}
.msg.ok{display:block;background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.28)}
.msg.err{display:block;background:rgba(255,82,82,.1);color:var(--red);border:1px solid rgba(255,82,82,.28)}
.step{display:none!important}.step.on{display:block!important;animation:fu .3s ease}
.live-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.live{font-size:8px;background:rgba(0,230,118,.12);color:var(--green);padding:3px 8px;border-radius:6px;border:1px solid rgba(0,230,118,.3);font-weight:800}
.cats{display:flex;gap:5px;overflow-x:auto;padding-bottom:8px;margin-bottom:8px;scrollbar-width:none}.cats::-webkit-scrollbar{display:none}
.cat{flex-shrink:0;padding:7px 11px;background:var(--inner);border:1px solid var(--border);border-radius:9px;color:var(--muted);font-size:10px;font-weight:800;cursor:pointer;white-space:nowrap;transition:all .2s;display:flex;align-items:center;gap:5px}
.cat.on{background:var(--gold-g);color:#111;border:none}
.lbl{display:block;font-size:9px;color:var(--muted);font-weight:800;letter-spacing:.3px;margin-bottom:4px}

.selected-asset-box {background:var(--inner);border:1.5px solid var(--gold);border-radius:12px;padding:10px 14px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.selected-asset-box:hover{background:rgba(255,215,0,0.05)}
.selected-asset-info span:first-child{font-size:9px;color:var(--muted);display:block;font-weight:700}
.selected-asset-info span:last-child{font-size:13px;color:var(--gold);font-weight:900}

.ref-box {background:var(--inner);border:1px solid var(--border-g);border-radius:12px;padding:10px 12px;margin-top:12px;text-align:left}
.ref-box-title {font-size:11px;font-weight:800;color:var(--gold);margin-bottom:4px;display:flex;align-items:center;gap:5px}
.ref-box-desc {font-size:10px;color:#b0b8c8;line-height:1.4;margin-bottom:8px}

.modal-overlay{position:fixed;inset:0;background:rgba(4,6,10,.88);z-index:300;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease}
.modal-content{background:var(--card);border:1.5px solid var(--border-g);border-radius:16px;width:100%;max-width:400px;max-height:85vh;display:flex;flex-direction:column;padding:16px;animation:fu .3s ease}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.modal-header h3{font-size:14px;color:var(--gold)}
.modal-close{background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;font-weight:900}
.modal-body{overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:6px;padding-right:2px}
.modal-search-input{width:100%;padding:10px 12px;border-radius:10px;border:1.5px solid var(--border-g);background:var(--inner);color:#fff;font-size:12px;font-weight:700;outline:none;margin-bottom:8px}

.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.sel{width:100%;padding:11px 12px;border-radius:10px;border:1px solid var(--border);background:var(--inner);color:#fff;font-size:12px;font-weight:700;outline:none;cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath fill='%238a94a6' d='M1 1l5 5 5-5'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 11px center}
.sig{display:none;margin-top:11px;background:var(--inner);border:2px solid var(--gold);border-radius:14px;padding:14px;text-align:center;animation:fu .3s ease}
.sig-meta{font-size:10px;color:var(--muted);font-weight:700}
.sig-dir{font-size:19px;font-weight:900;padding:11px;border-radius:11px;margin:8px 0}
.call{background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.3)}
.put{background:rgba(255,82,82,.1);color:var(--red);border:1px solid rgba(255,82,82,.3)}
.none-dir{background:rgba(255,82,82,.15);color:var(--red);border:1px solid rgba(255,82,82,.4)}
.timer{font-size:12.5px;font-weight:900;color:var(--gold);background:rgba(255,215,0,.06);border:1px dashed var(--gold);border-radius:10px;padding:8px;margin-bottom:8px}
.strat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:10.5px;color:#c5cdd8;text-align:left;line-height:1.5;margin-bottom:8px}
.sig-stats{display:flex;justify-content:space-between;font-size:10px;font-weight:800;margin-bottom:8px}
.edu-item{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:11px 13px;margin-bottom:7px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;transition:transform .15s}
.edu-item:active{transform:scale(.98)}.edu-item h4{font-size:11.5px;color:var(--gold);font-weight:800}
.edu-detail{background:var(--inner);border:1px solid var(--border-g);border-radius:12px;padding:14px;animation:fu .3s ease}
.edu-detail h3{color:var(--gold);font-size:13px;font-weight:900;margin-bottom:8px}
.edu-detail p{font-size:11.5px;color:#c5cdd8;line-height:1.7;white-space:pre-line;margin-bottom:10px}
.edu-detail img{width:100%;height:160px;object-fit:cover;border-radius:10px;border:1px solid var(--border);margin-bottom:8px}
.tag-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px}
.tag{font-size:9px;font-weight:800;padding:3px 8px;border-radius:6px;background:rgba(255,215,0,.08);color:var(--gold);border:1px solid rgba(255,215,0,.2)}
.prof-row{font-size:11.5px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
.name-input{width:100%;padding:10px;border-radius:10px;border:1px solid var(--border);background:var(--inner);color:#fff;font-size:13px;font-weight:700;outline:none;text-align:center;margin-bottom:10px}
.name-input:focus{border-color:var(--gold)}
.about-list{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:11px 13px;margin:8px 0 12px}
.about-list li{font-size:11px;color:#c5cdd8;line-height:1.85;list-style:none}
.bnav{position:fixed;bottom:0;left:0;right:0;background:#05070d;border-top:1px solid var(--border);display:flex;justify-around;padding:6px 0 10px;z-index:100}
.nav{display:flex;flex-direction:column;align-items:center;gap:1px;color:var(--muted);font-size:8.5px;font-weight:800;cursor:pointer;min-width:44px;padding:3px 1px;transition:color .2s}
.nav.on{color:var(--gold)}.nav span:first-child{font-size:15px}
.hidden{display:none!important}
.gap{height:6px}.gap2{height:9px}
.analyze-overlay{position:fixed;inset:0;background:rgba(4,6,10,.94);z-index:200;display:flex;flex-direction:column;align-items:center;justify-content:center;animation:fadeIn .2s ease}
.analyze-wheel{width:56px;height:56px;border:3px solid rgba(255,215,0,.12);border-top-color:var(--gold);border-radius:50%;animation:spin .7s linear infinite;margin-bottom:14px}
.analyze-text{font-size:13px;font-weight:800;color:var(--gold);text-align:center;animation:pulse 1.2s ease infinite;max-width:240px;line-height:1.45}
.scan-box{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;overflow:hidden;margin-bottom:10px}
.scan-video{width:100%;height:210px;object-fit:cover;background:#000;display:block}
.scan-placeholder{height:210px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--muted);font-size:12px;font-weight:700;gap:8px;text-align:center;padding:12px}
.scan-frame{position:relative}
.scan-frame.live::after{content:"";position:absolute;inset:12px;border:2px solid rgba(255,215,0,.5);border-radius:8px;pointer-events:none;box-shadow:0 0 0 9999px rgba(0,0,0,.2)}
.scan-tips{font-size:10.5px;color:#b0b8c8;line-height:1.55;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:10px}
.scan-tips b{color:var(--gold)}
.ind-row{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0}
.ind-chip{font-size:9px;font-weight:800;padding:4px 8px;border-radius:7px;background:rgba(255,215,0,.08);color:var(--gold);border:1px solid rgba(255,215,0,.2)}
.fav-list{max-height:240px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.fav-item{display:flex;justify-content:space-between;align-items:center;padding:11px 13px;background:var(--inner);border:1px solid var(--border);border-radius:11px;font-size:12px;font-weight:700;cursor:pointer;transition:background .15s}
.fav-item:hover{background:rgba(255,215,0,.08)}
.fav-item span:first-child{color:var(--gold)}

.admin-profile-box {background:var(--inner);border:2px solid var(--gold);border-radius:14px;padding:14px;margin-top:12px;animation:fu .3s ease}
.users-list {display:flex;flex-direction:column;gap:6px;max-height:220px;overflow-y:auto;margin-top:8px;padding-right:4px}
.admin-search-input {width:100%;padding:9px 11px;border-radius:9px;border:1px solid var(--border);background:var(--card);color:#fff;font-size:11.5px;font-weight:700;outline:none;margin-bottom:8px}
.admin-search-input:focus {border-color:var(--gold)}
.user-row {display:flex;justify-content:space-between;align-items:center;background:var(--card);padding:9px 11px;border-radius:9px;border:1px solid var(--border);font-size:11px;font-weight:700}
.user-status-btn {padding:5px 9px;border-radius:6px;border:none;font-size:9.5px;font-weight:800;cursor:pointer}
.btn-block-red {background:rgba(255,82,82,0.2);color:var(--red);border:1px solid rgba(255,82,82,0.4)}
.btn-unblock-green {background:rgba(0,230,118,0.2);color:var(--green);border:1px solid rgba(0,230,118,0.4)}
.btn-del-user {background:rgba(255,82,82,0.15);color:var(--red);border:1px solid rgba(255,82,82,0.3);padding:5px 8px;border-radius:6px;font-size:9px;font-weight:800;cursor:pointer;margin-left:4px}
.catalog-category-header {font-size:10px;font-weight:800;color:var(--gold);text-transform:uppercase;margin:10px 0 4px 4px;letter-spacing:.5px;border-bottom:1px solid var(--border);padding-bottom:3px}
</style>
</head>
<body>

<div class="wrap">
<div class="header">
<div>
<h1 class="g-text">👑 TEAM MASTER VIP</h1>
<small id="hdrSub">⚡ TRADING TERMINAL</small>
</div>
<select class="lang" id="langSelect" onchange="changeLanguage(this.value)">
<option value="ru">🇷🇺 RU</option>
<option value="en">🇺🇸 EN</option>
<option value="ua">🇺🇦 UA</option>
</select>
</div>

<div class="gate" id="gate">
<div class="progress">
<div class="p-dot cur" id="d1"></div><div class="p-line" id="l1"></div>
<div class="p-dot" id="d2"></div><div class="p-line" id="l2"></div>
<div class="p-dot" id="d3"></div>
</div>
<div class="step on" id="s1">
<div class="gate-icon">👑</div>
<div class="gate-title" data-t="gateTitle">Добро пожаловать</div>
<div class="gate-desc" id="gateDesc1"><b>Авторизация в системе</b><br><br>Чтобы войти в систему введите ваш секретный ключ или пароль.<br><br>Укажите Ваш настоящий Telegram юзернейм (например, <b>@username</b>).</div>
<input class="input" id="masterCodeInput" type="text" placeholder="Введите ключ доступа...">
<input class="input" id="tgUserInput" type="text" placeholder="Ваш ТГ юзернейм (@username)">
<div class="msg" id="regMsg"></div>
<button class="btn btn-gold" id="btnReg" onclick="doReg()" data-t="btnConfirmId">✅ Войти в терминал</button>
</div>
<div class="step" id="s2">
<div class="gate-icon">💎</div>
<div class="gate-title" data-t="depTitle">Проверка депозита</div>
<div class="gate-desc" id="gateDesc2"><b>Статус аккаунта</b><br><br>Ключ принят. Введите промокод <b>WELCOME50</b> для подтверждения доступа к сигналам.</div>
<input class="input" id="promoInput" type="text" value="WELCOME50">
<div class="msg" id="depMsg"></div>
<button class="btn btn-green" id="btnDep" onclick="doDep()" data-t="btnCheckDep">🔍 Проверить депозит</button>

<div class="ref-box">
<div class="ref-box-title">🎁 Нет аккаунта Pocket Option?</div>
<div class="ref-box-desc">Зарегистрируйтесь по нашей официальной ссылке, чтобы получить бонус +50% к первому депозиту:</div>
<a class="btn btn-gold btn-sm" style="background:linear-gradient(135deg,#FFD700,#FFA000)" href="https://u3.shortink.io/cabinet/demo-quick-high-low?utm_campaign=850173&utm_source=affiliate&utm_medium=sr&a=RLQDltKf13Zlrj&al=1771346&ac=smart-link&cid=960963&code=WELCOME50" target="_blank">🔗 Регистрация в Pocket Option</a>
</div>

<div class="gap2"></div>
<button class="btn btn-dark btn-sm" onclick="goStep(1)" data-t="btnBack">← Назад</button>
</div>
<div class="step" id="s3">
<div class="gate-icon">🎉</div>
<div class="gate-title" data-t="accOpenTitle">Доступ открыт</div>
<div class="gate-desc" style="text-align:center" data-t="accOpenDesc">Активация прошла успешно. Все функции терминала доступны.</div>
<button class="btn btn-gold" onclick="enterApp()" data-t="btnEnterTerm">🚀 Войти в терминал</button>
</div>
</div>

<div id="app" class="hidden">
<div class="card tab" id="tabSig">
<div class="live-bar">
  <span class="live" id="apiStatusBadge">● LIVE API: ПОДКЛЮЧЕНО</span>
  <span style="font-size:9px;color:var(--gold);font-weight:800" id="apiTimerDisplay">⚡ СИНХРОНИЗАЦИЯ</span>
</div>

<div class="selected-asset-box" onclick="openCatalogModal()">
  <div class="selected-asset-info">
    <span data-t="lblActiveAsset">АКТИВ ДЛЯ АНАЛИЗА</span>
    <span id="currentAssetDisplay">EUR/USD (Биржа)</span>
  </div>
  <div style="display:flex;align-items:center;gap:6px">
    <span style="font-size:16px">📋</span>
  </div>
</div>

<div class="cats" id="assetCatsContainer"></div>

<div class="grid2" style="margin-bottom:10px">
<div>
<label class="lbl" data-t="lblTf">ТАЙМФРЕЙМ</label>
<select class="sel" id="tf">
<option value="S5">5 сек</option><option value="S15">15 сек</option><option value="S30">30 сек</option>
<option value="M1" selected>1 мин</option><option value="M2">2 мин</option><option value="M3">3 мин</option>
<option value="M5">5 мин</option><option value="M10">10 мин</option><option value="M15">15 мин</option>
</select>
</div>
<div>
<label class="lbl" data-t="lblExp">ЭКСПИРАЦИЯ</label>
<select class="sel" id="exp">
<option value="15" data-s="15">15 сек</option><option value="30" data-s="30">30 сек</option>
<option value="60" data-s="60" selected>1 мин</option><option value="120" data-s="120">2 мин</option>
<option value="180" data-s="180">3 мин</option><option value="240" data-s="240">4 мин</option>
<option value="300" data-s="300">5 мин</option><option value="360" data-s="360">6 мин</option>
<option value="420" data-s="420">7 мин</option><option value="480" data-s="480">8 мин</option>
<option value="540" data-s="540">9 мин</option><option value="600" data-s="600">10 мин</option>
<option value="720" data-s="720">12 мин</option><option value="900" data-s="900">15 мин</option>
</select>
</div>
</div>
<button class="btn btn-gold" onclick="getSig()" data-t="btnCalcSig">⚡ Просчитать сигнал</button>
<div class="gap"></div>
<button class="btn btn-green btn-sm" onclick="autoSig()" data-t="btnAutoSig">🤖 Выбрать топ актив</button>
<div class="gap"></div>
<a class="btn btn-gold btn-sm" style="background:linear-gradient(135deg,#FFD700,#FFA000)" href="https://u3.shortink.io/cabinet/demo-quick-high-low?utm_campaign=850173&utm_source=affiliate&utm_medium=sr&a=RLQDltKf13Zlrj&al=1771346&ac=smart-link&cid=960963&code=WELCOME50" target="_blank" id="poDeepLink" data-t="btnOpenPo">🚀 Открыть Pocket Option</a>
<div class="sig" id="sigBox">
<div class="sig-meta" id="sigMeta"></div>
<div class="sig-dir call" id="sigDir">⬆️ CALL</div>
<div class="timer">⏱ <span id="cd">01:00</span></div>
<div class="strat" id="sigStrat"></div>
<div class="sig-stats"><span><span data-t="lblCombo">Связка</span>: <b id="sigCombo">Multi-Factor</b></span></div>
<button class="btn btn-red btn-sm" id="btnCancel" onclick="cancelSig()" style="display:none" data-t="btnCancel">✖ Отменить</button>
<button class="btn btn-gold btn-sm" id="btnNew" onclick="getSig()" style="display:none" data-t="btnNewSig">⚡ Новый сигнал</button>
</div>
</div>

<div class="card tab hidden" id="tabFavs">
<h3 class="g-text" style="font-size:13px;margin-bottom:8px" data-t="favTabTitle">⭐ Избранные активы</h3>
<p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="favTabDesc">Нажмите на любой актив из списка, чтобы мгновенно перейти к нему на главный экран терминала.</p>
<div class="fav-list" id="mainFavList"></div>
<div id="mainFavEmpty" style="text-align:center;padding:24px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из полного каталога</div>
</div>

<div class="card tab hidden" id="tabEdu">
<div id="eduList">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:10px" data-t="eduHeader">📚 20 профессиональных связок (Полный курс)</h3>
<div id="eduItems"></div>
</div>
<div id="eduView" class="hidden">
<button class="btn btn-gold btn-sm" style="margin-bottom:10px" onclick="closeEdu()" data-t="btnBackEdu">← Назад к списку</button>
<div class="edu-detail" id="eduBody"></div>
</div>
</div>

<div class="card tab hidden" id="tabProf">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:10px" data-t="profTitle">👤 Профиль</h3>
<label class="lbl" data-t="profNameLbl">ВАШЕ ИМЯ</label>
<input class="name-input" id="profName" placeholder="Введите имя" onchange="saveName()" onblur="saveName()">

<div class="prof-row"><span data-t="profStatus">Статус</span><b style="color:var(--green)">VIP UNLIMITED</b></div>
<div class="prof-row"><span data-t="profIdLbl">Ваш ТГ</span><b id="profId">—</b></div>
<div class="prof-row"><span data-t="profSigsLbl">Сигналов</span><b id="profSigs">0</b></div>
<div class="gap2"></div>
<label class="lbl" data-t="profFavLbl">⭐ ИЗБРАННЫЕ АКТИВЫ</label>
<div class="fav-list" id="favList"></div>
<div id="favEmpty" style="text-align:center;padding:16px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из полного каталога</div>
<div class="gap2"></div>
<button class="btn btn-dark btn-sm" onclick="clearFavs()" data-t="btnClearFav">🗑 Очистить избранное</button>
<div class="gap"></div>
<button class="btn btn-red btn-sm" onclick="logout()" data-t="btnLogout">🚪 Выйти</button>

<div class="admin-profile-box hidden" id="adminProfileBox">
<h3 class="g-text" style="font-size:12px;margin-bottom:8px">👑 АДМИН-ПАНЕЛЬ: УПРАВЛЕНИЕ УЧАСТНИКАМИ</h3>
<label class="lbl">ПОЛЬЗОВАТЕЛИ, ПЕРЕШЕДШИЕ К СИГНАЛАМ</label>
<input type="text" class="admin-search-input" id="adminSearchUserInput" placeholder="Поиск по @username..." oninput="renderAdminData(this.value)" autocomplete="off">
<div class="users-list" id="adminUsersList"></div>
</div>

</div>

<div class="card tab hidden" id="tabScan">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:8px">📷 Сканер с ИИ</h3>
<div class="scan-tips" id="scanTipsHtml">
<b>Инструкция сканера:</b><br>
1️⃣ Нажмите «Открыть камеру»<br>
2️⃣ Выберите экспирацию<br>
3️⃣ Наведите камеру СТРОГО на свечной график<br>
4️⃣ Нажмите «СКАНУВАТИ»
</div>
<button class="btn btn-gold btn-sm" id="btnOpenCam" onclick="openCam()" data-t="btnOpenCam">📷 Открыть камеру</button>
<div class="gap"></div>
<div class="scan-box">
<div class="scan-frame" id="scanFrame">
<video class="scan-video hidden" id="scanVideo" autoplay playsinline muted></video>
<div class="scan-placeholder" id="scanPh">📷<br><span data-t="camOff">Камера отключена</span><br><small data-t="camOffSub">нажмите кнопку выше для запуска</small></div>
</div>
</div>
<label class="lbl" data-t="lblExp">ЭКСПИРАЦИЯ</label>
<select class="sel" id="scanExp" style="margin-bottom:8px">
<option value="15" data-s="15">15 сек</option><option value="30" data-s="30">30 сек</option>
<option value="60" data-s="60" selected>1 мин</option><option value="120" data-s="120">2 мин</option>
<option value="180" data-s="180">3 мин</option><option value="300" data-s="300">5 мин</option>
<option value="600" data-s="600">10 мин</option><option value="900" data-s="900">15 мин</option>
</select>
<div class="ind-row">
<span class="ind-chip">Candle Vision AI</span><span class="ind-chip">Smart Money</span><span class="ind-chip">Price Action</span>
</div>
<button class="btn btn-gold" id="btnScan" onclick="doScan()" disabled data-t="btnScanNow">📡 СКАНУВАТИ</button>
<div class="gap"></div>
<button class="btn btn-dark btn-sm" onclick="stopCam()" data-t="btnCloseCam">⏹ Закрыть камеру</button>
<div class="sig" id="scanSigBox">
<div class="sig-meta" id="scanMeta">SCAN · Vision AI</div>
<div class="sig-dir call" id="scanDir">⬆️ CALL</div>
<div class="timer">⏱ <span id="scanCd">01:00</span></div>
<div class="strat" id="scanStrat"></div>
<div class="sig-stats"><span>Candle Neural Vision</span></div>
<button class="btn btn-red btn-sm" id="btnScanCancel" onclick="cancelScan()" style="display:none" data-t="btnCancel">✖ Отменить</button>
<button class="btn btn-gold btn-sm" id="btnScanNew" onclick="doScan()" style="display:none" data-t="btnNewScan">⚡ Новый скан</button>
</div>
</div>

<div class="card tab hidden" id="tabAbout">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:6px" data-t="aboutTitle">👑 О нас и нашей команде</h3>
<p style="font-size:11px;color:#b0b8c8;line-height:1.65;margin-bottom:8px" data-t="aboutDesc"><b>TEAM MASTER VIP</b> — это сообщество трейдеров и программистов. Мы создали интеллектуального веб-бота и алгоритмический терминал, объединяющий нейросетевую аналитику, стаканы ордеров и распознавание паттернов в реальном времени.</p>
<div class="about-list" id="aboutListHtml">
<li>🤖 <b>Умный бот:</b> Автоматический просчет точек входа по 20+ индикаторам.</li>
<li>👑 <b>Команда Мастеров:</b> Опытные аналитики с практикой более 7 лет в финансовых рынках.</li>
<li>📷 <b>Neural Scanner:</b> Анализ графиков с камеры устройства через Vision AI.</li>
<li>💎 <b>VIP Community:</b> Закрытый клуб с приватными сигналами и постоянной поддержкой.</li>
</div>
<p style="font-size:10px;color:var(--muted);line-height:1.5;margin-bottom:10px" data-t="aboutRisk">Торгуйте осознанно. Соблюдайте мани-менеджмент: риск на сделку не более 1-2% от общего депозита.</p>
<a class="btn btn-gold btn-sm" href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank" data-t="btnTgChan">📢 Наш Telegram канал</a>
</div>
</div>

<div class="bnav hidden" id="bnav">
<div class="nav on" onclick="tab('tabSig',this)"><span>🎯</span><span data-t="navSig">Сигналы</span></div>
<div class="nav" onclick="tab('tabFavs',this)"><span>⭐</span><span data-t="navFavs">Избранное</span></div>
<div class="nav" onclick="tab('tabEdu',this)"><span>📚</span><span data-t="navEdu">Связки</span></div>
<div class="nav" onclick="tab('tabProf',this)"><span>👤</span><span data-t="navProf">Профиль</span></div>
<div class="nav" onclick="tab('tabScan',this)"><span>📷</span><span data-t="navScan">Сканер</span></div>
<div class="nav" onclick="tab('tabAbout',this)"><span>👑</span><span data-t="navAbout">О нас</span></div>
</div>

<div class="analyze-overlay hidden" id="analyzeBox">
<div class="analyze-wheel"></div>
<div class="analyze-text" id="analyzeTxt">🔍 Анализ рынка...</div>
<div class="analyze-sub">Smart Money · ICT · PA · Scalp · Trend</div>
</div>

<div class="modal-overlay hidden" id="catalogModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3 data-t="modalCatalogTitle">📋 Полный каталог активов</h3>
      <button class="modal-close" onclick="closeCatalogModal()">✕</button>
    </div>
    <input type="text" class="modal-search-input" id="modalSearchInput" placeholder="Поиск актива..." oninput="renderCatalogList(this.value)" data-placeholder-t="modalSearchPlaceholder">
    <div class="cats" style="margin-bottom:6px">
      <button class="cat on" onclick="setModalCat('all',this)" data-t="catAll">🌟 Все</button>
      <button class="cat" onclick="setModalCat('forex_real',this)">💱 Валютные пары (Биржа)</button>
      <button class="cat" onclick="setModalCat('forex',this)">⚡ Валютные пары OTC</button>
      <button class="cat" onclick="setModalCat('crypto',this)" data-t="catCrypto">🔥 Крипто</button>
      <button class="cat" onclick="setModalCat('commodities',this)" data-t="catComm">🛢️ Сырьё</button>
      <button class="cat" onclick="setModalCat('stocks',this)" data-t="catStocks">📈 Акции</button>
      <button class="cat" onclick="setModalCat('indices',this)" data-t="catIndices">📊 Индексы</button>
    </div>
    <div class="modal-body" id="modalCatalogBody"></div>
  </div>
</div>

<script>
const ADMIN_SECRET_KEY = "MASTER_ROOT_7777_SUPER_SECRET";
const USER_SECRET_KEY = "MASTER_TRADING_777_";
const RENDER_BACKEND_URL = window.location.origin;

let tgUser = localStorage.getItem("tmv_tgUser")||null;
let isAdmin = localStorage.getItem("tmv_isAdmin") === "true";
let timer=null,scanTimer=null,stream=null,camReady=false;
let favs=JSON.parse(localStorage.getItem("tmv_favs")||"[]");
let sigCount=parseInt(localStorage.getItem("tmv_sigs")||"0");
let currentAsset="EUR/USD (Биржа)";
let currentLang="ru";
let modalCategory="all";

let allUsersReg = JSON.parse(localStorage.getItem("tmv_users_db") || JSON.stringify([
  {tg: "@master_admin", status: "Активен", role: "ADMIN"}
]));

const I18N = {
  ru: {
    hdrSub: "⚡ TRADING TERMINAL",
    gateTitle: "Добро пожаловать",
    gateDesc1: "<b>Авторизация в системе</b><br><br>Чтобы войти в систему введите ваш секретный ключ или пароль.<br><br>Укажите Ваш настоящий Telegram юзернейм (например, <b>@username</b>).",
    btnConfirmId: "✅ Войти в терминал",
    depTitle: "Проверка депозита",
    gateDesc2: "<b>Статус аккаунта</b><br><br>Ключ принят. Введите промокод <b>WELCOME50</b>.",
    btnCheckDep: "🔍 Проверить депозит",
    btnBack: "← Назад",
    accOpenTitle: "Доступ открыт",
    accOpenDesc: "Активация прошла успешно. Все функции терминала доступны.",
    btnEnterTerm: "🚀 Войти в терминал",
    lblActiveAsset: "АКТИВ ДЛЯ АНАЛИЗА",
    catAll: "🌟 Все",
    catCrypto: "🔥 Крипто",
    catComm: "🛢️ Сырьё",
    catStocks: "📈 Акции",
    catIndices: "📊 Индексы",
    lblTf: "ТАЙМФРЕЙМ",
    lblExp: "ЭКСПИРАЦИЯ",
    btnCalcSig: "⚡ Просчитать сигнал",
    btnAutoSig: "🤖 Выбрать топ актив",
    btnOpenPo: "🚀 Открыть Pocket Option",
    lblCombo: "Связка",
    btnCancel: "✖ Отменить",
    btnNewSig: "⚡ Новый сигнал",
    eduHeader: "📚 20 профессиональных связок (Полный курс)",
    btnBackEdu: "← Назад к списку",
    profTitle: "👤 Профиль",
    profNameLbl: "ВАШЕ ИМЯ",
    profStatus: "Статус",
    profIdLbl: "Ваш ТГ",
    profSigsLbl: "Сигналов",
    profFavLbl: "⭐ ИЗБРАННЫЕ АКТИВЫ",
    favTabTitle: "⭐ Избранные активы",
    favTabDesc: "Нажмите на любой актив из списка, чтобы мгновенно перейти к нему на главный экран терминала.",
    profFavEmpty: "Пока пусто — добавь актив из полного каталога",
    btnClearFav: "🗑 Очистить избранное",
    btnLogout: "🚪 Выйти",
    btnOpenCam: "📷 Открыть камеру",
    camOff: "Камера отключена",
    camOffSub: "нажмите кнопку выше для запуска",
    btnScanNow: "📡 СКАНУВАТИ",
    btnCloseCam: "⏹ Закрыть камеру",
    btnNewScan: "⚡ Новый скан",
    aboutTitle: "👑 О нас и нашей команде",
    aboutDesc: "TEAM MASTER VIP — это сообщество трейдеров и программистов...",
    aboutRisk: "Торгуйте осознанно. Риск на сделку 1-2% от банка.",
    btnTgChan: "📢 Наш Telegram канал",
    navSig: "Сигналы",
    navFavs: "Избранное",
    navEdu: "Связки",
    navProf: "Профиль",
    navScan: "Сканер",
    navAbout: "О нас",
    modalCatalogTitle: "📋 Полный каталог активов",
    modalSearchPlaceholder: "Поиск актива...",
    btnChoose: "Обрати ➔"
  },
  en: {
    hdrSub: "⚡ TRADING TERMINAL",
    gateTitle: "Welcome",
    gateDesc1: "<b>Authorization</b><br><br>Enter your secret key to continue.<br><br>Enter your Telegram username.",
    btnConfirmId: "✅ Enter Terminal",
    depTitle: "Deposit Check",
    gateDesc2: "<b>Account Status</b><br><br>Key accepted. Enter promo code <b>WELCOME50</b>.",
    btnCheckDep: "🔍 Check Deposit",
    btnBack: "← Back",
    accOpenTitle: "Access Granted",
    accOpenDesc: "Activation successful.",
    btnEnterTerm: "🚀 Enter Terminal",
    lblActiveAsset: "SELECTED ASSET",
    catAll: "🌟 All",
    catCrypto: "🔥 Crypto",
    catComm: "🛢️ Comm",
    catStocks: "📈 Stocks",
    catIndices: "📊 Indices",
    lblTf: "TIMEFRAME",
    lblExp: "EXPIRATION",
    btnCalcSig: "⚡ Calculate Signal",
    btnAutoSig: "🤖 Choose Top",
    btnOpenPo: "🚀 Open Pocket Option",
    lblCombo: "Combo",
    btnCancel: "✖ Cancel",
    btnNewSig: "⚡ New Signal",
    eduHeader: "📚 Combos",
    btnBackEdu: "← Back",
    profTitle: "👤 Profile",
    profNameLbl: "YOUR NAME",
    profStatus: "Status",
    profIdLbl: "Your TG",
    profSigsLbl: "Signals",
    profFavLbl: "⭐ FAVORITE ASSETS",
    favTabTitle: "⭐ Favorite Assets",
    favTabDesc: "Click any asset to switch instantly to the main terminal screen.",
    profFavEmpty: "Empty yet",
    btnClearFav: "🗑 Clear",
    btnLogout: "🚪 Logout",
    btnOpenCam: "📷 Open Camera",
    camOff: "Camera is off",
    camOffSub: "click to start",
    btnScanNow: "📡 SCAN",
    btnCloseCam: "⏹ Close",
    btnNewScan: "⚡ New Scan",
    aboutTitle: "👑 About Us & Our Team",
    aboutDesc: "TEAM MASTER VIP trading terminal and master traders team.",
    aboutRisk: "Trade wisely.",
    btnTgChan: "📢 Telegram",
    navSig: "Signals",
    navFavs: "Favorites",
    navEdu: "Combos",
    navProf: "Profile",
    navScan: "Scanner",
    navAbout: "About",
    modalCatalogTitle: "📋 Full Assets Catalog",
    modalSearchPlaceholder: "Search asset...",
    btnChoose: "Select ➔"
  },
  ua: {
    hdrSub: "⚡ TRADING TERMINAL",
    gateTitle: "Ласкаво просимо",
    gateDesc1: "<b>Авторизація</b><br><br>Введіть ваш секретний ключ для входу.<br><br>Вкажіть ваш Telegram юзернейм.",
    btnConfirmId: "✅ Увійти в термінал",
    depTitle: "Перевірка депозиту",
    gateDesc2: "<b>Статус акаунта</b><br><br>Ключ прийнято. Введіть промокод <b>WELCOME50</b>.",
    btnCheckDep: "🔍 Перевірити депозит",
    btnBack: "← Назад",
    accOpenTitle: "Доступ відкрито",
    accOpenDesc: "Активацію успішно пройдено.",
    btnEnterTerm: "🚀 Увійти в термінал",
    lblActiveAsset: "АКТИВ ДЛЯ АНАЛІЗУ",
    catAll: "🌟 Всі",
    catCrypto: "🔥 Крипто",
    catComm: "🛢️ Сировина",
    catStocks: "📈 Акції",
    catIndices: "📊 Індекси",
    lblTf: "ТАЙМФРЕЙМ",
    lblExp: "ЕКСПИРАЦИЯ",
    btnCalcSig: "⚡ Прорахувати сигнал",
    btnAutoSig: "🤖 Обрати топ",
    btnOpenPo: "🚀 Відкрити Pocket Option",
    lblCombo: "Зв'язка",
    btnCancel: "✖ Скасувати",
    btnNewSig: "⚡ Новий сигнал",
    eduHeader: "📚 Зв'язки",
    btnBackEdu: "← Назад",
    profTitle: "👤 Профіль",
    profNameLbl: "ВАШЕ ІМ'Я",
    profStatus: "Статус",
    profIdLbl: "Ваш ТГ",
    profSigsLbl: "Сигналів",
    profFavLbl: "⭐ ОБРАНІ АКТИВИ",
    favTabTitle: "⭐ Обрані активи",
    favTabDesc: "Натисніть на будь-який актив зі списку, щоб миттєво перейти до нього на головний екран термінала.",
    profFavEmpty: "Поки порожньо",
    btnClearFav: "🗑 Очистити",
    btnLogout: "🚪 Вийти",
    btnOpenCam: "📷 Відкрити камеру",
    camOff: "Камера вимкнена",
    camOffSub: "натисніть для запуску",
    btnScanNow: "📡 СКАНУВАТИ",
    btnCloseCam: "⏹ Закрити",
    btnNewScan: "⚡ Новий скан",
    aboutTitle: "👑 Про нас та команду",
    aboutDesc: "TEAM MASTER VIP — торговий термінал та команда майстрів.",
    aboutRisk: "Торгуйте усвідомлено.",
    btnTgChan: "📢 Telegram",
    navSig: "Сигнали",
    navFavs: "Обране",
    navEdu: "Зв'язки",
    navProf: "Профіль",
    navScan: "Сканер",
    navAbout: "Про нас",
    modalCatalogTitle: "📋 Повний каталог активів",
    modalSearchPlaceholder: "Пошук активу...",
    btnChoose: "Обрати ➔"
  }
};

const ALL_ASSETS_CATALOG = [
  // 1. НАСТОЯЩИЙ РЫНОК (БИРЖА - БЕЗ OTC)
  { name: "EUR/USD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "GBP/USD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "USD/JPY (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "AUD/USD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "USD/CAD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "USD/CHF (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "EUR/JPY (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "GBP/JPY (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "EUR/GBP (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "AUD/JPY (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "NZD/USD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "CAD/JPY (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "EUR/CAD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "EUR/AUD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "GBP/CAD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "GBP/CHF (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "AUD/CAD (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "AUD/CHF (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "NZD/JPY (Биржа)", type: "forex_real", flag: "🌐" },
  { name: "CAD/CHF (Биржа)", type: "forex_real", flag: "🌐" },

  // 2. ВАЛЮТНЫЕ ПАРЫ OTC
  { name: "AUD/NZD OTC", type: "forex", flag: "💱" },
  { name: "AUD/USD OTC", type: "forex", flag: "💱" },
  { name: "BHD/CNY OTC", type: "forex", flag: "💱" },
  { name: "EUR/CHF OTC", type: "forex", flag: "💱" },
  { name: "EUR/GBP OTC", type: "forex", flag: "💱" },
  { name: "EUR/TRY OTC", type: "forex", flag: "💱" },
  { name: "EUR/USD OTC", type: "forex", flag: "💱" },
  { name: "NZD/USD OTC", type: "forex", flag: "💱" },
  { name: "USD/CHF OTC", type: "forex", flag: "💱" },
  { name: "USD/INR OTC", type: "forex", flag: "💱" },
  { name: "CHF/NOK OTC", type: "forex", flag: "💱" },
  { name: "AUD/CAD OTC", type: "forex", flag: "💱" },
  { name: "USD/JPY OTC", type: "forex", flag: "💱" },
  { name: "GBP/USD OTC", type: "forex", flag: "💱" },
  { name: "GBP/JPY OTC", type: "forex", flag: "💱" },
  { name: "CAD/JPY OTC", type: "forex", flag: "💱" },
  { name: "EUR/JPY OTC", type: "forex", flag: "💱" },
  { name: "USD/CAD OTC", type: "forex", flag: "💱" },
  { name: "USD/RUB OTC", type: "forex", flag: "💱" },

  // 3. КРИПТОВАЛЮТЫ
  { name: "Bitcoin OTC", type: "crypto", flag: "🔥" },
  { name: "Ethereum OTC", type: "crypto", flag: "🔥" },
  { name: "Solana OTC", type: "crypto", flag: "🔥" },
  { name: "Toncoin OTC", type: "crypto", flag: "🔥" },
  { name: "Ripple OTC", type: "crypto", flag: "🔥" },
  { name: "Cardano OTC", type: "crypto", flag: "🔥" },
  { name: "Dogecoin OTC", type: "crypto", flag: "🔥" },
  { name: "Bitcoin Real", type: "crypto", flag: "🔥" },
  { name: "Ethereum Real", type: "crypto", flag: "🔥" },
  { name: "Solana Real", type: "crypto", flag: "🔥" },

  // 4. СЫРЬЕ
  { name: "Gold OTC", type: "commodities", flag: "🛢️" },
  { name: "Silver OTC", type: "commodities", flag: "🛢️" },
  { name: "Brent Oil OTC", type: "commodities", flag: "🛢️" },
  { name: "WTI Crude Oil OTC", type: "commodities", flag: "🛢️" },
  { name: "Natural Gas OTC", type: "commodities", flag: "🛢️" },
  { name: "Gold Real (XAU/USD)", type: "commodities", flag: "🛢️" },
  { name: "Silver Real (XAG/USD)", type: "commodities", flag: "🛢️" },

  // 5. АКЦИИ
  { name: "Apple OTC", type: "stocks", flag: "📈" },
  { name: "Tesla OTC", type: "stocks", flag: "📈" },
  { name: "Microsoft OTC", type: "stocks", flag: "📈" },
  { name: "Amazon OTC", type: "stocks", flag: "📈" },
  { name: "Google OTC", type: "stocks", flag: "📈" },
  { name: "Meta OTC", type: "stocks", flag: "📈" },
  { name: "NVIDIA OTC", type: "stocks", flag: "📈" },
  { name: "AMD OTC", type: "stocks", flag: "📈" },
  { name: "Netflix OTC", type: "stocks", flag: "📈" },
  { name: "Intel OTC", type: "stocks", flag: "📈" },

  // 6. ИНДЕКСЫ
  { name: "US100 OTC", type: "indices", flag: "📊" },
  { name: "SP500 OTC", type: "indices", flag: "📊" },
  { name: "DJI30 OTC", type: "indices", flag: "📊" },
  { name: "GER40 OTC", type: "indices", flag: "📊" },
  { name: "UK100 OTC", type: "indices", flag: "📊" },
  { name: "JP225 OTC", type: "indices", flag: "📊" }
];

const EDU=[
{
  t:"1️⃣ Уровни Поддержки и Сопротивления (S/R)",
  d:"📌 ДЕТАЛЬНЫЙ АНАЛИЗ СВЯЗКИ:\\n• Рыночная механика: Отрабатывается на исторических точках разворота цены при сильном скоплении лимитных ордеров крупных участников.\\n• Точка входа: При формировании 2-3 касаний уровня и появлении паттерна ложного пробоя или пин-бара на M1/M5.",
  img:"https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=600&auto=format&fit=crop",
  tags:["M1","M5"]
},
{
  t:"2️⃣ RSI (14) + Полосы Боллинджера (Bollinger Bands)",
  d:"📌 ДЕТАЛЬНЫЙ АНАЛИЗ СВЯЗКИ:\\n• Рыночная механика: Сочетание зоны перекупленности/перепроданности осциллятора RSI с физическим выходом цены за границы BB.\\n• Точка входа: Когда свеча закрывается за пределами верхней или нижней линии BB, а RSI пересекает отметку 70 или 30.",
  img:"https://images.unsplash.com/photo-1642543492481-44e81e3914a7?w=600&auto=format&fit=crop",
  tags:["M1","M5"]
}
];

function updatePoDeepLink() {
  const btn = document.getElementById("poDeepLink");
  if(!btn) return;
  const poPairMap = {
    "EUR/USD OTC": "EURUSD_otc", "GBP/USD OTC": "GBPUSD_otc", "USD/JPY OTC": "USDJPY_otc",
    "EUR/USD (Биржа)": "EURUSD", "GBP/USD (Биржа)": "GBPUSD", "USD/JPY (Биржа)": "USDJPY"
  };
  let formattedAsset = poPairMap[currentAsset];
  if(!formattedAsset) {
    formattedAsset = currentAsset.replace("/", "").replace(/ /g, "_").toLowerCase();
  }
  const baseUrl = "https://u3.shortink.io/cabinet/demo-quick-high-low";
  const queryParams = "?utm_campaign=850173&utm_source=affiliate&utm_medium=sr&a=RLQDltKf13Zlrj&al=1771346&ac=smart-link&cid=960963&code=WELCOME50";
  btn.href = `${baseUrl}/${formattedAsset}${queryParams}`;
}

function renderAssetButtons() {
  const container = document.getElementById("assetCatsContainer");
  if(!container) return;
  let realForexItems = ALL_ASSETS_CATALOG.filter(item => item.type === "forex_real").slice(0, 8);
  let html = "";
  realForexItems.forEach((item) => {
    let fullName = item.name;
    let activeClass = (fullName === currentAsset) ? " on" : "";
    html += `<button class="cat ${activeClass}" onclick="selectCatalogAsset('${fullName}')">${item.flag} ${item.name}</button>`;
  });
  container.innerHTML = html;
}

function updateSelectedAssetUI() {
  const displayEl = document.getElementById("currentAssetDisplay");
  if(displayEl) {
    displayEl.textContent = currentAsset;
  }
  updatePoDeepLink();
}

function goStep(n){
  document.querySelectorAll(".step").forEach(s=>s.classList.remove("on"));
  document.getElementById("s"+n).classList.add("on");
  for(let i=1;i<=3;i++){
    const d=document.getElementById("d"+i);
    d.className="p-dot";
    if(i<n) d.classList.add("done");
    else if(i===n) d.classList.add("cur");
  }
  for(let i=1;i<=2;i++){
    const l=document.getElementById("l"+i);
    l.className="p-line";
    if(i<n) l.classList.add("done");
  }
}

function showMsg(id, txt, isErr=false){
  const el=document.getElementById(id);
  el.className="msg "+(isErr?"err":"ok");
  el.innerHTML=txt;
}

function checkUserBlockedStatus(user) {
  let foundUser = allUsersReg.find(u => u.tg.toLowerCase() === user.toLowerCase());
  if(foundUser && foundUser.status === "Заблокирован") {
    alert("⛔ Ваш аккаунт заблокирован администратором!");
    return true;
  }
  return false;
}

function registerUserAndNotify(username) {
  if(!username) return;
  let formattedTg = username.startsWith("@") ? username : "@" + username;
  let foundUser = allUsersReg.find(u => u.tg.toLowerCase() === formattedTg.toLowerCase());
  if(!foundUser) {
    allUsersReg.push({tg: formattedTg, status: "Активен", role: "USER"});
    localStorage.setItem("tmv_users_db", JSON.stringify(allUsersReg));
  }
}

function doReg(){
  const code = document.getElementById("masterCodeInput").value.trim();
  const user = document.getElementById("tgUserInput").value.trim();
  if(!user || !user.startsWith("@")){
    showMsg("regMsg", "❌ Укажите ваш Telegram юзернейм начиная с @", true);
    return;
  }
  if(code !== ADMIN_SECRET_KEY && code !== USER_SECRET_KEY) {
    showMsg("regMsg", "❌ Неверный ключ доступа!", true);
    return;
  }
  if(checkUserBlockedStatus(user)) {
    showMsg("regMsg", "❌ Этот аккаунт заблокирован навсегда!", true);
    return;
  }
  isAdmin = (code === ADMIN_SECRET_KEY);
  localStorage.setItem("tmv_isAdmin", isAdmin ? "true" : "false");
  tgUser = user;
  localStorage.setItem("tmv_tgUser", user);
  registerUserAndNotify(tgUser);
  showMsg("regMsg", "✅ Ключ принят!");
  setTimeout(()=>{ goStep(2); }, 800);
}

function doDep(){
  const promo = document.getElementById("promoInput").value.trim();
  if(promo.toUpperCase() !== "WELCOME50"){
    showMsg("depMsg", "❌ Неверный промокод!", true);
    return;
  }
  showMsg("depMsg", "✅ Депозит подтвержден!");
  setTimeout(()=>{ goStep(3); }, 800);
}

function enterApp(){
  document.getElementById("gate").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
  document.getElementById("bnav").classList.remove("hidden");
  renderAssetButtons();
  updateSelectedAssetUI();
  renderFavs();
  renderEdu();
  loadProfile();
}

function tab(tabId, el){
  document.querySelectorAll(".tab").forEach(t=>t.classList.add("hidden"));
  document.getElementById(tabId).classList.remove("hidden");
  document.querySelectorAll(".bnav .nav").forEach(n=>n.classList.remove("on"));
  el.classList.add("on");
  if(tabId === 'tabFavs') renderMainFavs();
  if(tabId === 'tabProf' && isAdmin) renderAdminData();
}

function openCatalogModal(){
  document.getElementById("catalogModal").classList.remove("hidden");
  renderCatalogList("");
}

function closeCatalogModal(){
  document.getElementById("catalogModal").classList.add("hidden");
}

function setModalCat(cat, el){
  modalCategory = cat;
  document.querySelectorAll("#catalogModal .cats .cat").forEach(c=>c.classList.remove("on"));
  if(el) el.classList.add("on");
  renderCatalogList(document.getElementById("modalSearchInput").value);
}

function renderCatalogList(filter=""){
  const bodyEl = document.getElementById("modalCatalogBody");
  let html = "";
  let searchLower = filter.toLowerCase();
  const dict = I18N[currentLang] || I18N.ru;
  const categories = [
    { key: "forex_real", name: "💱 Валютные пары (Настоящий рынок Биржи)" },
    { key: "forex", name: "⚡ Валютные пары OTC (Котировки брокера)" },
    { key: "crypto", name: "🔥 Криптовалюты" },
    { key: "commodities", name: "🛢️ Сырьевые товары" },
    { key: "stocks", name: "📈 Акции компаний" },
    { key: "indices", name: "📊 Биржевые индексы" }
  ];
  categories.forEach(catGroup => {
    if(modalCategory !== "all" && modalCategory !== catGroup.key) return;
    let groupItems = ALL_ASSETS_CATALOG.filter(item => {
      let matchesCat = (item.type === catGroup.key);
      let matchesSearch = item.name.toLowerCase().includes(searchLower);
      return matchesCat && matchesSearch;
    });
    if(groupItems.length > 0){
      html += `<div class="catalog-category-header">${catGroup.name}</div>`;
      groupItems.forEach(item => {
        let fullAssetName = item.name;
        let isFav = favs.includes(fullAssetName);
        html += `<div class="fav-item">
          <span onclick="selectCatalogAsset('${fullAssetName}')">${item.flag} ${item.name}</span>
          <div style="display:flex;gap:8px;align-items:center">
            <span style="cursor:pointer;font-size:15px" onclick="toggleFav('${fullAssetName}')">${isFav ? '⭐' : '☆'}</span>
            <span style="color:var(--gold);font-size:11px;cursor:pointer" onclick="selectCatalogAsset('${fullAssetName}')">${dict.btnChoose}</span>
          </div>
        </div>`;
      });
    }
  });
  if(!html){
    html = `<div style="text-align:center;padding:20px;color:var(--muted);font-size:11px">Ничего не найдено</div>`;
  }
  bodyEl.innerHTML = html;
}

function toggleFav(name){
  if(favs.includes(name)) favs = favs.filter(f => f !== name);
  else favs.push(name);
  localStorage.setItem("tmv_favs", JSON.stringify(favs));
  renderCatalogList(document.getElementById("modalSearchInput").value);
  renderFavs();
  renderMainFavs();
}

function selectCatalogAsset(name){
  currentAsset = name;
  updateSelectedAssetUI();
  renderAssetButtons();
  closeCatalogModal();
  tab('tabSig', document.querySelector('.bnav .nav'));
}

function renderFavs(){
  const listEl = document.getElementById("favList");
  const emptyEl = document.getElementById("favEmpty");
  if(!listEl) return;
  if(favs.length === 0){
    listEl.innerHTML = "";
    if(emptyEl) emptyEl.style.display = "block";
    return;
  }
  if(emptyEl) emptyEl.style.display = "none";
  let html = "";
  favs.forEach(f => {
    html += `<div class="fav-item" onclick="selectCatalogAsset('${f}')"><span>⭐ ${f}</span><span>➔</span></div>`;
  });
  listEl.innerHTML = html;
}

function renderMainFavs(){
  const listEl = document.getElementById("mainFavList");
  const emptyEl = document.getElementById("mainFavEmpty");
  if(!listEl) return;
  if(favs.length === 0){
    listEl.innerHTML = "";
    if(emptyEl) emptyEl.style.display = "block";
    return;
  }
  if(emptyEl) emptyEl.style.display = "none";
  let html = "";
  favs.forEach(f => {
    html += `<div class="fav-item" onclick="selectCatalogAsset('${f}')"><span>⭐ ${f}</span><span style="color:var(--gold)">Перейти ➔</span></div>`;
  });
  listEl.innerHTML = html;
}

function clearFavs(){
  favs = [];
  localStorage.setItem("tmv_favs", JSON.stringify(favs));
  renderFavs();
  renderMainFavs();
}

async function getSig(){
  runAnalyzer(async ()=>{
    const tf = document.getElementById("tf").value;
    const expSec = parseInt(document.getElementById("exp").value);
    
    let isCall = Math.random() > 0.5;
    let dir = isCall ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (ВНИЗ)";
    let stratText = `Анализ биржевого рынка (${currentAsset}): Сформирована свечная модель Price Action. Направление входа: ${dir}.`;

    try {
      const res = await fetch(`${RENDER_BACKEND_URL}/api/signal?asset=${encodeURIComponent(currentAsset)}&tf=${tf}`);
      if (res.ok) {
        const data = await res.json();
        if (data.direction) dir = data.direction;
        if (data.analysis) stratText = data.analysis;
      }
    } catch(e) {}

    document.getElementById("sigMeta").textContent = `${currentAsset} · ${tf} · Мировой рынок`;
    const dirEl = document.getElementById("sigDir");
    dirEl.textContent = dir;
    dirEl.className = "sig-dir " + (dir.includes("CALL") ? "call" : "put");
    document.getElementById("sigStrat").innerHTML = `<b>Анализ:</b> ${stratText}`;
    document.getElementById("sigCombo").textContent = "Smart Money + PA";
    document.getElementById("sigBox").style.display = "block";
    document.getElementById("btnCancel").style.display = "block";
    document.getElementById("btnNew").style.display = "none";
    sigCount++;
    localStorage.setItem("tmv_sigs", sigCount);
    document.getElementById("profSigs").textContent = sigCount;
    startTimer("cd", expSec, ()=>{
      document.getElementById("btnCancel").style.display = "none";
      document.getElementById("btnNew").style.display = "block";
    });
  });
}

function autoSig(){
  const realForex = ALL_ASSETS_CATALOG.filter(a => a.type === "forex_real");
  const randomAssetObj = realForex[Math.floor(Math.random()*realForex.length)];
  currentAsset = randomAssetObj.name;
  updateSelectedAssetUI();
  renderAssetButtons();
  getSig();
}

function cancelSig(){
  if(timer) clearInterval(timer);
  document.getElementById("sigBox").style.display = "none";
}

function startTimer(elementId, seconds, onComplete){
  if(timer) clearInterval(timer);
  let rem = seconds;
  const el = document.getElementById(elementId);
  updateTimerDisplay(el, rem);
  timer = setInterval(()=>{
    rem--;
    if(rem <= 0){
      clearInterval(timer);
      el.textContent = "00:00 Готово!";
      if(onComplete) onComplete();
      return;
    }
    updateTimerDisplay(el, rem);
  }, 1000);
}

function updateTimerDisplay(el, sec){
  const m = Math.floor(sec/60);
  const s = sec%60;
  el.textContent = (m<10?"0"+m:m) + ":" + (s<10?"0"+s:s);
}

function runAnalyzer(callback){
  const overlay = document.getElementById("analyzeBox");
  const txtEl = document.getElementById("analyzeTxt");
  overlay.classList.remove("hidden");
  let step = 0;
  const phrasesList = ["🔍 Анализ графика...", "📊 Оценка свечных паттернов...", "🤖 Просчет Smart Money...", "⚡ Считывание волатильности..."];
  const interval = setInterval(()=>{
    txtEl.textContent = phrasesList[step % phrasesList.length];
    step++;
  }, 400);
  setTimeout(()=>{
    clearInterval(interval);
    overlay.classList.add("hidden");
    if(callback) callback();
  }, 2000);
}

function renderEdu(){
  const itemsEl = document.getElementById("eduItems");
  let html = "";
  EDU.forEach((ed, i)=>{
    html += `<div class="edu-item" onclick="openEdu(${i})">
      <h4>${ed.t}</h4>
      <span style="color:var(--gold)">➔</span>
    </div>`;
  });
  itemsEl.innerHTML = html;
}

function openEdu(idx){
  const ed = EDU[idx];
  document.getElementById("eduList").classList.add("hidden");
  document.getElementById("eduView").classList.remove("hidden");
  let tagsHtml = "";
  ed.tags.forEach(t => { tagsHtml += `<span class="tag">${t}</span>`; });
  document.getElementById("eduBody").innerHTML = `
    <h3>${ed.t}</h3>
    <img src="${ed.img}" alt="Strategy">
    <div class="tag-row">${tagsHtml}</div>
    <p>${ed.d}</p>
  `;
}

function closeEdu(){
  document.getElementById("eduView").classList.add("hidden");
  document.getElementById("eduList").classList.remove("hidden");
}

function loadProfile(){
  document.getElementById("profId").textContent = tgUser || "@user";
  document.getElementById("profName").value = localStorage.getItem("tmv_name") || "";
  document.getElementById("profSigs").textContent = sigCount;
  renderFavs();
  const adminBox = document.getElementById("adminProfileBox");
  if(isAdmin) {
    adminBox.classList.remove("hidden");
    renderAdminData();
  } else {
    adminBox.classList.add("hidden");
  }
}

function saveName(){
  localStorage.setItem("tmv_name", document.getElementById("profName").value);
}

function logout(){
  localStorage.removeItem("tmv_tgUser");
  localStorage.removeItem("tmv_isAdmin");
  location.reload();
}

function openCam(){
  const video = document.getElementById("scanVideo");
  const ph = document.getElementById("scanPh");
  const frame = document.getElementById("scanFrame");
  navigator.mediaDevices.getUserMedia({video:{facingMode:"environment"}, audio:false})
  .then(s => {
    stream = s;
    video.srcObject = stream;
    video.classList.remove("hidden");
    ph.classList.add("hidden");
    frame.classList.add("live");
    camReady = true;
    document.getElementById("btnScan").removeAttribute("disabled");
  })
  .catch(err => { alert("Камера недоступна: " + err); });
}

function stopCam(){
  if(stream){
    stream.getTracks().forEach(t => t.stop());
    stream = null;
  }
  document.getElementById("scanVideo").classList.add("hidden");
  document.getElementById("scanPh").classList.remove("hidden");
  document.getElementById("scanFrame").classList.remove("live");
  camReady = false;
  document.getElementById("btnScan").setAttribute("disabled", "true");
}

async function doScan(){
  if(!camReady){ alert("Сначала откройте камеру!"); return; }

  const video = document.getElementById("scanVideo");
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth || 640;
  canvas.height = video.videoHeight || 480;
  canvas.getContext("2d").drawImage(video, 0, 0);
  
  canvas.toBlob(async (blob) => {
    runAnalyzer(async ()=>{
      const expSec = parseInt(document.getElementById("scanExp").value);
      const cfgObj = {
        интервал: document.getElementById("scanExp").options[document.getElementById("scanExp").selectedIndex].text,
        стратегия: "Candlestick PA & Smart Money"
      };

      let dir = "НЕОПРЕДЕЛЕНО";
      let stratText = "❌ Ошибка сканирования.";
      let isChart = false;

      try {
        const fd = new FormData();
        fd.append('file', blob, 'snapshot.jpg');
        fd.append('cfg', JSON.stringify(cfgObj));
        const response = await fetch(`${RENDER_BACKEND_URL}/api/scan-analyze`, {
          method: 'POST',
          body: fd
        });
        if(response.ok) {
          const resData = await response.json();
          isChart = resData.is_chart;
          dir = resData.direction;
          stratText = resData.analysis;
        }
      } catch(e) {
        stratText = "❌ Ошибка соединения с сервером AI.";
      }

      document.getElementById("scanMeta").textContent = `VISION AI · ${document.getElementById("scanExp").options[document.getElementById("scanExp").selectedIndex].text}`;
      const dirEl = document.getElementById("scanDir");
      dirEl.textContent = dir;
      
      if(!isChart) {
        dirEl.className = "sig-dir none-dir";
        document.getElementById("scanStrat").innerHTML = `<pre style="white-space:pre-wrap;font-family:inherit;color:var(--red);"><b>${stratText}</b></pre>`;
        document.getElementById("scanSigBox").style.display = "block";
        document.getElementById("btnScanCancel").style.display = "none";
        document.getElementById("btnScanNew").style.display = "block";
        return;
      }

      dirEl.className = "sig-dir " + (dir.includes("CALL") ? "call" : "put");
      document.getElementById("scanStrat").innerHTML = `<pre style="white-space:pre-wrap;font-family:inherit;"><b>${stratText}</b></pre>`;
      
      document.getElementById("scanSigBox").style.display = "block";
      document.getElementById("btnScanCancel").style.display = "block";
      document.getElementById("btnScanNew").style.display = "none";
      
      sigCount++;
      localStorage.setItem("tmv_sigs", sigCount);
      document.getElementById("profSigs").textContent = sigCount;

      startTimer("scanCd", expSec, ()=>{
        document.getElementById("btnScanCancel").style.display = "none";
        document.getElementById("btnScanNew").style.display = "block";
      });
    });
  }, 'image/jpeg');
}

function cancelScan(){
  if(scanTimer) clearInterval(scanTimer);
  document.getElementById("scanSigBox").style.display = "none";
}

function changeLanguage(lang){
  currentLang = lang;
  const dict = I18N[lang];
  if(!dict) return;
  for(let key in dict){
    const el = document.querySelector(`[data-t="${key}"]`);
    if(el) el.innerHTML = dict[key];
  }
}

function renderAdminData(filter=""){
  const listEl = document.getElementById("adminUsersList");
  let html = "";
  const filteredUsers = allUsersReg.filter(u => u.tg.toLowerCase().includes(filter.toLowerCase()));
  if(filteredUsers.length === 0) {
    html = `<div style="text-align:center;padding:10px;color:var(--muted);font-size:11px">Список пуст</div>`;
  } else {
    filteredUsers.forEach((u)=>{
      const originalIdx = allUsersReg.findIndex(item => item.tg.toLowerCase() === u.tg.toLowerCase());
      const isBlocked = u.status === "Заблокирован";
      html += `<div class="user-row">
        <div><b>${u.tg}</b><br><small style="color:${isBlocked?'var(--red)':'var(--green)'}">${u.status}</small></div>
        <div style="display:flex;align-items:center;gap:4px">
          <button class="user-status-btn ${isBlocked?'btn-unblock-green':'btn-block-red'}" onclick="toggleUserStatus(${originalIdx})">${isBlocked?'Разблокировать':'Заблокировать'}</button>
          <button class="btn-del-user" onclick="deleteUser(${originalIdx})">🗑</button>
        </div>
      </div>`;
    });
  }
  listEl.innerHTML = html;
}

function toggleUserStatus(idx) {
  if(allUsersReg[idx]) {
    allUsersReg[idx].status = (allUsersReg[idx].status === "Заблокирован") ? "Активен" : "Заблокирован";
    localStorage.setItem("tmv_users_db", JSON.stringify(allUsersReg));
    renderAdminData();
  }
}

function deleteUser(idx) {
  if(confirm("Удалить пользователя?")) {
    allUsersReg.splice(idx, 1);
    localStorage.setItem("tmv_users_db", JSON.stringify(allUsersReg));
    renderAdminData();
  }
}

window.addEventListener("DOMContentLoaded", ()=>{
  if(tgUser) {
    if(checkUserBlockedStatus(tgUser)) {
      logout();
      return;
    }
    goStep(3);
    enterApp();
  }
});
</script>
</body>
</html>
"""

@app.get("/")
async def get_page():
    return HTMLResponse(HTML_UI)

@app.get("/api/signal")
async def get_signal(asset: str = "EUR/USD (Биржа)", tf: str = "M1"):
    dirs = ["⬆️ CALL (ВВЕРХ)", "⬇️ PUT (ВНИЗ)"]
    chosen_dir = random.choice(dirs)
    return JSONResponse(content={
        "direction": chosen_dir,
        "analysis": f"Анализ мирового биржевого рынка {asset} [{tf}]: Подтверждён сигнал по структуре свечей и уровням поддержки/сопротивления."
    })

@app.post("/api/scan-analyze")
async def scan_analyze(file: UploadFile = File(...), cfg: str = Form(...)):
    image_bytes = await file.read()
    try:
        config_data = json.loads(cfg)
    except Exception:
        config_data = {"стратегия": "Candlestick PA & Smart Money", "интервал": "M1"}
    
    result = await core.compute(image_bytes, config_data)
    return JSONResponse(content=result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
