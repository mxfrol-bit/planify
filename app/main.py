import os
import asyncio
import logging
from datetime import date, timedelta
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from telegram import Update

from app.database import db
from app.models import HabitCreate, HabitToggle, TaskCreate
from app.bot import build_application
from app.reminder import reminder_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
bot_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app
    bot_app = build_application()
    await bot_app.initialize()
    await bot_app.start()

    if WEBHOOK_URL:
        webhook_path = f"{WEBHOOK_URL}/webhook"
        await bot_app.bot.set_webhook(url=webhook_path)
        logger.info(f"Webhook set to {webhook_path}")

    # Запускаем планировщик напоминаний
    reminder_task = asyncio.create_task(reminder_loop(bot_app))

    yield

    reminder_task.cancel()
    await bot_app.stop()
    await bot_app.shutdown()


app = FastAPI(title="PlanifyBot API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth helper ──────────────────────────────────────────────────────────

def get_current_user(x_token: str = Header(None)):
    if not x_token:
        raise HTTPException(401, "Token required")
    user = db.get_user_by_token(x_token)
    if not user:
        raise HTTPException(401, "Invalid token")
    return user


# ── Telegram Webhook ─────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}


# ── User ─────────────────────────────────────────────────────────────────

@app.get("/api/me")
def get_me(token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401, "Invalid token")
    return user


# ── Habits ───────────────────────────────────────────────────────────────

@app.get("/api/habits")
def get_habits(token: str, date_str: Optional[str] = None):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)

    today = date_str or date.today().isoformat()
    habits = db.get_habits(user["id"])
    logs = db.get_today_logs(user["id"], today)
    done_ids = {l["habit_id"] for l in logs}

    return [
        {**h, "done_today": h["id"] in done_ids}
        for h in habits
    ]


@app.post("/api/habits")
def create_habit(body: HabitCreate, token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    return db.create_habit(user["id"], body.name, body.emoji, body.frequency)


@app.post("/api/habits/{habit_id}/toggle")
def toggle_habit(habit_id: str, body: HabitToggle, token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    done = db.toggle_habit(habit_id, user["id"], body.date)
    return {"done": done}


@app.post("/api/habits/{habit_id}/settings")
def update_habit_settings(habit_id: str, token: str, body: dict):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    data = {}
    if "reminder_time" in body:
        data["reminder_time"] = body["reminder_time"]
    if "reminder_days" in body:
        data["reminder_days"] = body["reminder_days"]
    if data:
        db.update_habit(habit_id, user["id"], data)
    return {"ok": True}


@app.delete("/api/habits/{habit_id}")
def delete_habit(habit_id: str, token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    db.delete_habit(habit_id, user["id"])
    return {"ok": True}


# ── Tasks ─────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
def get_tasks(token: str, completed: Optional[bool] = None):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    tasks = db.get_tasks(user["id"], completed)

    today = date.today()
    result = []
    for t in tasks:
        days_left = None
        if t["deadline"]:
            days_left = (date.fromisoformat(t["deadline"]) - today).days
        result.append({**t, "days_left": days_left})
    return result


@app.post("/api/tasks")
def create_task(body: TaskCreate, token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    return db.create_task(
        user["id"], body.title, body.emoji,
        body.deadline, body.priority, body.category
    )


@app.post("/api/tasks/{task_id}/toggle")
def toggle_task(task_id: str, token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    done = db.toggle_task(task_id, user["id"])
    return {"completed": done}


@app.post("/api/tasks/{task_id}/update")
def update_task(task_id: str, token: str, body: dict):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    from app.models import TaskUpdate
    supabase_data = {k: v for k, v in body.items() if v is not None}
    from app.database import supabase
    supabase.table("tasks").update(supabase_data).eq("id", task_id).eq("user_id", user["id"]).execute()
    if body.get("reminder_time") and body.get("deadline"):
        db.set_reminder_time(task_id, user["id"], body["reminder_time"])
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    db.delete_task(task_id, user["id"])
    return {"ok": True}


# ── Stats ─────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats(token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)

    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    return db.get_stats(user["id"], today.isoformat(), week_start)


@app.get("/api/stats/week")
def get_week_stats(token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)

    today = date.today()
    habits = db.get_habits(user["id"])
    week_data = []

    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_str = day.isoformat()
        logs = db.get_today_logs(user["id"], day_str)
        done = len(logs)
        total = len(habits)
        week_data.append({
            "date": day_str,
            "weekday": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][day.weekday()],
            "done": done,
            "total": total,
            "pct": round(done / total * 100) if total else 0,
        })

    return week_data


# ── AI Chat proxy ─────────────────────────────────────────────────────────

@app.post("/api/ai/chat")
async def ai_chat(request: Request, token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    
    body = await request.json()
    messages = body.get("messages", [])
    system = body.get("system", "Ты дружелюбный ИИ-ассистент планировщика Planify. Отвечай кратко на русском языке.")
    
    import httpx, os
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    
    try:
        if openrouter_key:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openrouter_key}", "Content-Type": "application/json"},
                    json={"model": "google/gemini-2.0-flash-001", "max_tokens": 600,
                          "messages": [{"role": "system", "content": system}] + messages}
                )
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        elif api_key:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                    json={"model": "claude-haiku-4-5-20251001", "max_tokens": 600, "system": system, "messages": messages}
                )
            data = resp.json()
            text = data["content"][0]["text"]
        else:
            text = "AI не настроен. Добавьте OPENROUTER_API_KEY в Railway Variables."
        return {"text": text}
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        raise HTTPException(500, str(e))


# ── iCal Calendar feed ────────────────────────────────────────────────────

@app.get("/calendar/{token}.ics")
async def get_ical(token: str):
    from fastapi.responses import Response
    from datetime import date, datetime, timedelta
    import uuid
    
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(404)
    
    tasks = db.get_tasks(user["id"], completed=False)
    habits = db.get_habits(user["id"])
    name = user.get("first_name", "Planify")
    
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Planify//RU",
        f"X-WR-CALNAME:Planify — {name}",
        "X-WR-TIMEZONE:Europe/Moscow",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALDESC:Задачи и привычки из Planify",
    ]
    
    # Tasks as events
    for t in tasks:
        if not t.get("deadline"):
            continue
        dl = t["deadline"]
        uid_str = f"{t['id']}@planify"
        
        # Date/time
        if t.get("reminder_time"):
            try:
                h, m = map(int, t["reminder_time"].split(":"))
                # Convert Moscow to UTC (UTC+3)
                dt = datetime.strptime(dl, "%Y-%m-%d").replace(hour=h, minute=m)
                dt_utc = dt - timedelta(hours=3)
                dtstart = dt_utc.strftime("%Y%m%dT%H%M%SZ")
                dtend = (dt_utc + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
                alarm_dt = (dt_utc - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
                has_time = True
            except:
                has_time = False
        else:
            has_time = False
        
        priority_map = {"urgent": 1, "high": 3, "medium": 5, "low": 9}
        prio = priority_map.get(t.get("priority", "medium"), 5)
        emoji = t.get("emoji", "📌")
        title = f"{emoji} {t.get('title', '')}"
        
        lines += ["BEGIN:VEVENT", f"UID:{uid_str}", f"DTSTAMP:{now}"]
        
        if has_time:
            lines += [f"DTSTART:{dtstart}", f"DTEND:{dtend}"]
        else:
            dl_fmt = dl.replace("-", "")
            lines += [f"DTSTART;VALUE=DATE:{dl_fmt}",
                      f"DTEND;VALUE=DATE:{dl_fmt}"]
        
        lines += [
            f"SUMMARY:{title}",
            f"PRIORITY:{prio}",
            f"CATEGORIES:{t.get('category','personal').upper()}",
        ]
        
        if has_time:
            lines += [
                "BEGIN:VALARM",
                "TRIGGER:-PT60M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:Напоминание: {title}",
                "END:VALARM",
            ]
        
        lines.append("END:VEVENT")
    
    # Habits as recurring events (today only - simplified)
    today = date.today()
    for h in habits:
        if not h.get("reminder_time"):
            continue
        try:
            hr, mn = map(int, h["reminder_time"].split(":"))
            dt = datetime(today.year, today.month, today.day, hr, mn)
            dt_utc = dt - timedelta(hours=3)
            dtstart = dt_utc.strftime("%Y%m%dT%H%M%SZ")
            dtend = (dt_utc + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")
            emoji = h.get("emoji", "✅")
            title = f"{emoji} {h.get('name', '')}"
            uid_str = f"habit-{h['id']}@planify"
            
            days_map = {"0":"MO","1":"TU","2":"WE","3":"TH","4":"FR","5":"SA","6":"SU"}
            rd = h.get("reminder_days", "all")
            if rd == "all":
                rrule = "RRULE:FREQ=DAILY"
            else:
                byday = ",".join([days_map[d] for d in rd.split(",") if d in days_map])
                rrule = f"RRULE:FREQ=WEEKLY;BYDAY={byday}"
            
            lines += [
                "BEGIN:VEVENT",
                f"UID:{uid_str}",
                f"DTSTAMP:{now}",
                f"DTSTART:{dtstart}",
                f"DTEND:{dtend}",
                f"SUMMARY:{title}",
                rrule,
                "CATEGORIES:HABIT",
                "BEGIN:VALARM",
                "TRIGGER:-PT5M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{title}",
                "END:VALARM",
                "END:VEVENT",
            ]
        except:
            continue
    
    lines.append("END:VCALENDAR")
    ical_text = "
".join(lines) + "
"
    
    return Response(
        content=ical_text,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="planify.ics"'}
    )


# ── Google Calendar OAuth ──────────────────────────────────────────────────

@app.get("/api/google/auth-url")
async def google_auth_url(token: str):
    import os, urllib.parse
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(400, "Google OAuth не настроен")
    
    base_url = os.getenv("WEBHOOK_URL", "")
    redirect_uri = f"{base_url}/api/google/callback"
    
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/calendar",
        "access_type": "offline",
        "prompt": "consent",
        "state": token,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return {"url": url}


@app.get("/api/google/callback")
async def google_callback(code: str, state: str):
    import os, httpx
    from fastapi.responses import HTMLResponse
    
    user = db.get_user_by_token(state)
    if not user:
        return HTMLResponse("<script>window.close()</script>")
    
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    base_url = os.getenv("WEBHOOK_URL", "")
    redirect_uri = f"{base_url}/api/google/callback"
    
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        })
    
    tokens = resp.json()
    if "access_token" in tokens:
        from app.database import supabase
        supabase.table("users").update({
            "google_access_token": tokens.get("access_token"),
            "google_refresh_token": tokens.get("refresh_token"),
        }).eq("id", user["id"]).execute()
        return HTMLResponse("<html><body><script>window.opener.location.reload();window.close();</script><p>✅ Google Calendar подключён! Окно закроется автоматически.</p></body></html>")
    
    return HTMLResponse("<p>❌ Ошибка авторизации</p>")


@app.post("/api/google/sync")
async def google_sync(token: str):
    import os, httpx
    from datetime import date, datetime, timedelta
    
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    
    access_token = user.get("google_access_token")
    if not access_token:
        raise HTTPException(400, "Google Calendar не подключён")
    
    tasks = db.get_tasks(user["id"], completed=False)
    synced = 0
    errors = []
    
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    
    async with httpx.AsyncClient(timeout=15) as client:
        for t in tasks:
            if not t.get("deadline"):
                continue
            try:
                dl = t["deadline"]
                emoji = t.get("emoji", "📌")
                title = f"{emoji} {t.get('title', '')}"
                
                event = {
                    "summary": title,
                    "description": f"Создано в Planify. Приоритет: {t.get('priority','medium')}",
                    "colorId": {"urgent":"11","high":"6","medium":"5","low":"2"}.get(t.get("priority","medium"),"5"),
                }
                
                if t.get("reminder_time"):
                    h, m = map(int, t["reminder_time"].split(":"))
                    start_dt = f"{dl}T{h:02d}:{m:02d}:00"
                    end_dt_obj = datetime.strptime(start_dt, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=1)
                    end_dt = end_dt_obj.strftime("%Y-%m-%dT%H:%M:%S")
                    event["start"] = {"dateTime": start_dt, "timeZone": "Europe/Moscow"}
                    event["end"] = {"dateTime": end_dt, "timeZone": "Europe/Moscow"}
                    event["reminders"] = {"useDefault": False, "overrides": [{"method":"popup","minutes":60}]}
                else:
                    event["start"] = {"date": dl}
                    event["end"] = {"date": dl}
                
                resp = await client.post(
                    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                    headers=headers, json=event
                )
                if resp.status_code in [200, 201]:
                    synced += 1
                else:
                    errors.append(t.get("title",""))
            except Exception as e:
                errors.append(str(e))
    
    return {"synced": synced, "errors": errors, "total": len([t for t in tasks if t.get("deadline")])}


# ── Test call ─────────────────────────────────────────────────────────────

@app.post("/api/call/test")
async def test_call(request: Request, token: str):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401)
    
    phone = user.get("phone")
    if not phone:
        raise HTTPException(400, "Номер телефона не установлен. Используйте /setphone в боте.")
    
    # Собираем задачи на сегодня
    from datetime import date
    today = date.today().isoformat()
    tasks = db.get_tasks(user["id"], completed=False)
    habits = db.get_habits(user["id"])
    today_tasks = [t for t in tasks if t.get("deadline") == today]
    
    # Генерируем текст
    name = user.get("first_name", "")
    text_parts = [f"Добрый день, {name}!" if name else "Добрый день!"]
    
    if today_tasks:
        text_parts.append(f"На сегодня у вас {len(today_tasks)} задач.")
        for t in today_tasks[:3]:
            time_str = f"в {t['reminder_time']}" if t.get("reminder_time") else ""
            text_parts.append(f"{t['title']} {time_str}.")
    else:
        text_parts.append("На сегодня задач нет.")
    
    if habits:
        done_logs = db.get_today_logs(user["id"], today)
        done_ids = {l["habit_id"] for l in done_logs}
        not_done = [h for h in habits if h["id"] not in done_ids]
        if not_done:
            text_parts.append(f"Также не забудьте про привычки: {', '.join([h['name'] for h in not_done[:3]])}.")
    
    text_parts.append("Удачного дня!")
    call_text = " ".join(text_parts)
    
    # Звоним
    from app.caller import make_call
    task_mock = {"title": "Дайджест дня", "reminder_time": "", "emoji": "📞", "id": "test"}
    
    import httpx, os
    ZVONOK_API_KEY = os.getenv("ZVONOK_API_KEY", "")
    ZVONOK_CAMPAIGN_ID = os.getenv("ZVONOK_CAMPAIGN_ID", "")
    
    if not ZVONOK_API_KEY:
        return {"success": False, "error": "Zvonok не настроен", "text": call_text}
    
    phone_clean = "".join(filter(str.isdigit, phone))
    if phone_clean.startswith("8"):
        phone_clean = "7" + phone_clean[1:]
    if not phone_clean.startswith("7"):
        phone_clean = "7" + phone_clean
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://zvonok.com/manager/cabapi_external/api/v1/phones/call/",
                data={"public_key": ZVONOK_API_KEY, "campaign_id": ZVONOK_CAMPAIGN_ID,
                      "phone": phone_clean, "text": call_text}
            )
        result = resp.json()
        success = result.get("status") == "ok" or bool(result.get("call_id"))
        return {"success": success, "text": call_text, "phone": phone, "zvonok": result}
    except Exception as e:
        return {"success": False, "error": str(e), "text": call_text}


# ── Serve Frontend ────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
