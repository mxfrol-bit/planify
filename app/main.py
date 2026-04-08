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


# ── Serve Frontend ────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
