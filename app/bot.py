import os
import logging
from datetime import date, timedelta
from app.ai_parser import parse_task, ai_available
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from app.database import db

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

WAITING_HABIT_NAME, WAITING_TASK_TITLE, WAITING_TASK_DEADLINE, WAITING_TASK_PRIORITY = range(4)
PRIORITY_MAP = {"urgent": "🔴 Срочно", "high": "🟠 Высокий", "medium": "🟡 Средний", "low": "🟢 Низкий"}

def get_today(): return date.today().isoformat()
def get_week_start():
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()
def ensure_user(update):
    u = update.effective_user
    db.create_user(u.id, u.username, u.first_name)

# ── AI parser → см. app/ai_parser.py ────────────────────────────────────

# ── Free text handler ─────────────────────────────────────────────────────

async def handle_free_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = update.message.text.strip()
    uid = update.effective_user.id

    if ai_available():
        msg = await update.message.reply_text("⏳ Анализирую...")
        parsed = await parse_task(text)
        if parsed and parsed.get("is_task"):
            task = db.create_task(uid, parsed.get("title", text), parsed.get("emoji", "📌"),
                                  parsed.get("deadline"), parsed.get("priority", "medium"), parsed.get("category", "personal"))
            dl = f"\n📅 {parsed['deadline']}" if parsed.get("deadline") else ""
            pr = PRIORITY_MAP.get(parsed.get("priority", "medium"), "")
            kb = [[InlineKeyboardButton("✅ Окей", callback_data="ai_ok"),
                   InlineKeyboardButton("🗑 Удалить", callback_data=f"task_del:{task['id']}")]]
            await msg.edit_text(f"📌 Записал:\n\n*{parsed['emoji']} {parsed['title']}*{dl}\n{pr}",
                                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await msg.edit_text("Не распознал как задачу 🤔\n\nПопробуй:\n• _«Купить молоко до пятницы»_\n• _«Встреча с Олегом завтра, срочно»_\n\nИли /addtask для ручного ввода.", parse_mode="Markdown")
    else:
        emoji, title = "📌", text
        if len(text) > 1 and not text[0].isalnum():
            emoji, title = text[0], text[1:].strip()
        db.create_task(uid, title, emoji)
        kb = [[InlineKeyboardButton("📋 Задачи", callback_data="cmd:tasks")]]
        await update.message.reply_text(f"📌 Записал: *{title}*\n\n_Добавь ANTHROPIC\\_API\\_KEY для умного распознавания_",
                                        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ── /start ────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    name = update.effective_user.first_name
    ai = "🤖 AI-режим активен — пиши задачи в свободной форме!" if ai_available() else "💡 Пиши задачи — я запишу их автоматически"
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n{ai}\n\n"
        "Примеры:\n_«Купить молоко до пятницы»_\n_«Встреча с клиентом завтра срочно»_\n\n"
        "📋 *Команды:*\n/habits — привычки\n/tasks — задачи\n/addhabit — новая привычка\n/progress — прогресс\n/web — дашборд",
        parse_mode="Markdown")

# ── /web ─────────────────────────────────────────────────────────────────

async def web_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    token = db.get_web_token(update.effective_user.id)
    kb = [[InlineKeyboardButton("🌐 Открыть дашборд", url=f"{WEBHOOK_URL}?token={token}")]]
    await update.message.reply_text("Ваш персональный дашборд:", reply_markup=InlineKeyboardMarkup(kb))

# ── /habits ───────────────────────────────────────────────────────────────

async def habits_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    today = get_today()
    habits = db.get_habits(uid)
    logs = db.get_today_logs(uid, today)
    done_ids = {l["habit_id"] for l in logs}
    if not habits:
        await update.message.reply_text("Привычек нет. Добавьте командой /addhabit")
        return
    done = sum(1 for h in habits if h["id"] in done_ids)
    pct = round(done / len(habits) * 100)
    buttons = [[InlineKeyboardButton(
        f"{'✅' if h['id'] in done_ids else '⬜'} {h['emoji']} {h['name']}",
        callback_data=f"toggle_habit:{h['id']}:{today}")] for h in habits]
    buttons.append([InlineKeyboardButton("➕ Добавить привычку", callback_data="cmd:addhabit")])
    await update.message.reply_text(f"📅 *Привычки на сегодня* — {pct}%\n", parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(buttons))

async def toggle_habit_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, habit_id, date_str = query.data.split(":", 2)
    uid = update.effective_user.id
    db.toggle_habit(habit_id, uid, date_str)
    habits = db.get_habits(uid)
    logs = db.get_today_logs(uid, date_str)
    done_ids = {l["habit_id"] for l in logs}
    done = sum(1 for h in habits if h["id"] in done_ids)
    pct = round(done / len(habits) * 100) if habits else 0
    buttons = [[InlineKeyboardButton(
        f"{'✅' if h['id'] in done_ids else '⬜'} {h['emoji']} {h['name']}",
        callback_data=f"toggle_habit:{h['id']}:{date_str}")] for h in habits]
    buttons.append([InlineKeyboardButton("➕ Добавить привычку", callback_data="cmd:addhabit")])
    await query.edit_message_text(f"📅 *Привычки на сегодня* — {pct}%\n", parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup(buttons))

# ── /tasks ────────────────────────────────────────────────────────────────

async def tasks_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    tasks = db.get_tasks(uid, completed=False)
    if not tasks:
        await update.message.reply_text("Задач нет! Просто напиши мне что нужно сделать 💬")
        return
    today = date.today()
    buttons = []
    for t in tasks:
        days_str = ""
        if t["deadline"]:
            delta = (date.fromisoformat(t["deadline"]) - today).days
            days_str = f" · {delta}д" if delta >= 0 else " · ⚠️"
        icon = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(t["priority"], "⚪")
        buttons.append([InlineKeyboardButton(f"{icon} {t['emoji']} {t['title']}{days_str}"[:55],
                                             callback_data=f"task_done:{t['id']}"),
                        InlineKeyboardButton("🗑", callback_data=f"task_del:{t['id']}")])
    await update.message.reply_text(f"📋 *Задачи* ({len(tasks)})\n", parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(buttons))

async def task_action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if query.data.startswith("task_done:"):
        db.toggle_task(query.data.split(":")[1], uid)
        await query.edit_message_text("✅ Задача выполнена! 🎉")
    elif query.data.startswith("task_del:"):
        db.delete_task(query.data.split(":")[1], uid)
        await query.edit_message_text("🗑 Удалено.")
    elif query.data == "ai_ok":
        await query.edit_message_reply_markup(None)

# ── /addhabit conversation ─────────────────────────────────────────────────

async def addhabit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    msg = update.callback_query.message if update.callback_query else update.message
    if update.callback_query: await update.callback_query.answer()
    await msg.reply_text("Введите название привычки:\n_(например: 🏃 Пробежка 30 мин)_", parse_mode="Markdown")
    return WAITING_HABIT_NAME

async def addhabit_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    emoji, name = ("✅", text) if not text or text[0].isalnum() else (text[0], text[1:].strip())
    db.create_habit(update.effective_user.id, name, emoji)
    await update.message.reply_text(f"✅ Привычка *{emoji} {name}* добавлена!", parse_mode="Markdown")
    return ConversationHandler.END

# ── /addtask conversation ──────────────────────────────────────────────────

async def addtask_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    msg = update.callback_query.message if update.callback_query else update.message
    if update.callback_query: await update.callback_query.answer()
    await msg.reply_text("📝 Название задачи:")
    return WAITING_TASK_TITLE

async def addtask_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    emoji, title = ("📌", text) if not text or text[0].isalnum() else (text[0], text[1:].strip())
    ctx.user_data["task"] = {"title": title, "emoji": emoji}
    kb = [[InlineKeyboardButton("Сегодня", callback_data="dl:today"), InlineKeyboardButton("Завтра", callback_data="dl:tomorrow")],
          [InlineKeyboardButton("Через 3 дня", callback_data="dl:3"), InlineKeyboardButton("Через неделю", callback_data="dl:7")],
          [InlineKeyboardButton("Без дедлайна", callback_data="dl:none")]]
    await update.message.reply_text("📅 Дедлайн:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_TASK_DEADLINE

async def addtask_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    val = query.data.split(":")[1]
    today = date.today()
    deadline = {"today": today.isoformat(), "tomorrow": (today + timedelta(1)).isoformat()}.get(val)
    if val.isdigit(): deadline = (today + timedelta(int(val))).isoformat()
    ctx.user_data["task"]["deadline"] = deadline
    kb = [[InlineKeyboardButton("🔴 Срочно", callback_data="pr:urgent"), InlineKeyboardButton("🟠 Высокий", callback_data="pr:high")],
          [InlineKeyboardButton("🟡 Средний", callback_data="pr:medium"), InlineKeyboardButton("🟢 Низкий", callback_data="pr:low")]]
    await query.edit_message_text("🎯 Приоритет:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_TASK_PRIORITY

async def addtask_priority(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task = ctx.user_data["task"]
    task["priority"] = query.data.split(":")[1]
    db.create_task(update.effective_user.id, task["title"], task["emoji"], task["deadline"], task["priority"])
    dl = f"\n📅 {task['deadline']}" if task["deadline"] else ""
    await query.edit_message_text(f"✅ Задача добавлена!\n\n*{task['emoji']} {task['title']}*{dl}\n{PRIORITY_MAP.get(task['priority'], '')}",
                                  parse_mode="Markdown")
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ── /progress ─────────────────────────────────────────────────────────────

async def progress(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    stats = db.get_stats(uid, get_today(), get_week_start())
    bar = lambda p: "█" * (p // 10) + "░" * (10 - p // 10)
    await update.message.reply_text(
        f"📊 *Прогресс*\n\n"
        f"*Сегодня:* {stats['today_pct']}%\n`{bar(stats['today_pct'])}`\n"
        f"{stats['habits_done_today']}/{stats['habits_total']} привычек\n\n"
        f"*Неделя:* {stats['week_pct']}%\n`{bar(stats['week_pct'])}`\n\n"
        f"*Задачи:*\n⏳ {stats['tasks_pending']} в работе · ✅ {stats['tasks_done']} выполнено",
        parse_mode="Markdown")

# ── Callbacks ─────────────────────────────────────────────────────────────

async def cmd_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd = query.data.split(":")[1]
    if cmd == "habits": await habits_today(update, ctx)
    elif cmd == "tasks": await tasks_list(update, ctx)

# ── Build app ─────────────────────────────────────────────────────────────

def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    habit_conv = ConversationHandler(
        entry_points=[CommandHandler("addhabit", addhabit_start), CallbackQueryHandler(addhabit_start, pattern="^cmd:addhabit$")],
        states={WAITING_HABIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addhabit_name)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    task_conv = ConversationHandler(
        entry_points=[CommandHandler("addtask", addtask_start), CallbackQueryHandler(addtask_start, pattern="^cmd:addtask$")],
        states={
            WAITING_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_title)],
            WAITING_TASK_DEADLINE: [CallbackQueryHandler(addtask_deadline, pattern="^dl:")],
            WAITING_TASK_PRIORITY: [CallbackQueryHandler(addtask_priority, pattern="^pr:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("habits", habits_today))
    app.add_handler(CommandHandler("tasks", tasks_list))
    app.add_handler(CommandHandler("progress", progress))
    app.add_handler(CommandHandler("web", web_link))
    app.add_handler(habit_conv)
    app.add_handler(task_conv)
    app.add_handler(CallbackQueryHandler(toggle_habit_callback, pattern="^toggle_habit:"))
    app.add_handler(CallbackQueryHandler(task_action_callback, pattern="^(task_done|task_del|ai_ok):?"))
    app.add_handler(CallbackQueryHandler(cmd_callback, pattern="^cmd:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))

    return app
