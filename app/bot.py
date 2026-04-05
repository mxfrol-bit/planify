import os
import logging
from datetime import date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from app.database import db

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-app.railway.app
PORT = int(os.getenv("PORT", 8000))

# ConversationHandler states
WAITING_HABIT_NAME, WAITING_TASK_TITLE, WAITING_TASK_DEADLINE, WAITING_TASK_PRIORITY = range(4)

PRIORITY_MAP = {"urgent": "🔴 Срочно", "high": "🟠 Высокий", "medium": "🟡 Средний", "low": "🟢 Низкий"}
CATEGORY_MAP = {"work": "💼 Работа", "personal": "👤 Личное", "health": "🏃 Здоровье", "learning": "📚 Учёба", "other": "📦 Другое"}


def get_today() -> str:
    return date.today().isoformat()

def get_week_start() -> str:
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()

def ensure_user(update: Update):
    u = update.effective_user
    db.create_user(u.id, u.username, u.first_name)


# ── /start ────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    name = update.effective_user.first_name
    text = (
        f"👋 Привет, {name}!\n\n"
        "Я твой личный планировщик. Помогу отслеживать привычки и задачи.\n\n"
        "📋 *Команды:*\n"
        "/habits — сегодняшние привычки\n"
        "/tasks — список задач\n"
        "/addhabit — добавить привычку\n"
        "/addtask — добавить задачу\n"
        "/progress — прогресс за неделю\n"
        "/web — открыть веб-версию\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /web ─────────────────────────────────────────────────────────────────

async def web_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    token = db.get_web_token(update.effective_user.id)
    web_url = f"{WEBHOOK_URL}?token={token}"
    kb = [[InlineKeyboardButton("🌐 Открыть дашборд", url=web_url)]]
    await update.message.reply_text(
        "Вот ваша персональная ссылка на веб-версию:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ── /habits ───────────────────────────────────────────────────────────────

async def habits_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    today = get_today()
    habits = db.get_habits(uid)
    logs = db.get_today_logs(uid, today)
    done_ids = {l["habit_id"] for l in logs}

    if not habits:
        await update.message.reply_text(
            "Привычек нет. Добавьте первую командой /addhabit"
        )
        return

    done = sum(1 for h in habits if h["id"] in done_ids)
    pct = round(done / len(habits) * 100)

    text = f"📅 *Привычки на сегодня* — {pct}% выполнено\n\n"
    buttons = []
    for h in habits:
        is_done = h["id"] in done_ids
        status = "✅" if is_done else "⬜"
        buttons.append([InlineKeyboardButton(
            f"{status} {h['emoji']} {h['name']}",
            callback_data=f"toggle_habit:{h['id']}:{today}"
        )])

    buttons.append([InlineKeyboardButton("➕ Добавить привычку", callback_data="cmd:addhabit")])
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def toggle_habit_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, habit_id, date_str = query.data.split(":", 2)
    uid = update.effective_user.id
    is_done = db.toggle_habit(habit_id, uid, date_str)

    habits = db.get_habits(uid)
    logs = db.get_today_logs(uid, date_str)
    done_ids = {l["habit_id"] for l in logs}
    done = sum(1 for h in habits if h["id"] in done_ids)
    pct = round(done / len(habits) * 100) if habits else 0

    text = f"📅 *Привычки на сегодня* — {pct}% выполнено\n\n"
    buttons = []
    for h in habits:
        status = "✅" if h["id"] in done_ids else "⬜"
        buttons.append([InlineKeyboardButton(
            f"{status} {h['emoji']} {h['name']}",
            callback_data=f"toggle_habit:{h['id']}:{date_str}"
        )])
    buttons.append([InlineKeyboardButton("➕ Добавить привычку", callback_data="cmd:addhabit")])

    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ── /tasks ────────────────────────────────────────────────────────────────

async def tasks_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    tasks = db.get_tasks(uid, completed=False)

    if not tasks:
        await update.message.reply_text("Задач нет! Добавьте командой /addtask")
        return

    today = date.today()
    text = f"📋 *Активные задачи* ({len(tasks)})\n\n"
    buttons = []

    for t in tasks:
        days_str = ""
        if t["deadline"]:
            delta = (date.fromisoformat(t["deadline"]) - today).days
            days_str = f" · {delta}д" if delta >= 0 else f" · ⚠️просрочено"

        priority_icon = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(t["priority"], "⚪")
        label = f"{priority_icon} {t['emoji']} {t['title']}{days_str}"[:55]

        buttons.append([
            InlineKeyboardButton(label, callback_data=f"task_done:{t['id']}"),
            InlineKeyboardButton("🗑", callback_data=f"task_del:{t['id']}")
        ])

    buttons.append([InlineKeyboardButton("➕ Новая задача", callback_data="cmd:addtask")])
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def task_action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id

    if query.data.startswith("task_done:"):
        task_id = query.data.split(":")[1]
        db.toggle_task(task_id, uid)
        await query.edit_message_text("✅ Задача выполнена! Отличная работа!")
    elif query.data.startswith("task_del:"):
        task_id = query.data.split(":")[1]
        db.delete_task(task_id, uid)
        await query.edit_message_text("🗑 Задача удалена.")


# ── /addhabit conversation ─────────────────────────────────────────────────

async def addhabit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            "Введите название привычки:\n_(например: 🏃 Пробежка 30 мин)_",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Введите название привычки:\n_(например: 🏃 Пробежка 30 мин)_",
            parse_mode="Markdown"
        )
    return WAITING_HABIT_NAME


async def addhabit_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    # Попытка вытащить эмодзи из начала
    emoji = "✅"
    name = text
    if text and len(text) > 1 and not text[0].isalnum():
        emoji = text[0]
        name = text[1:].strip()

    uid = update.effective_user.id
    db.create_habit(uid, name, emoji)

    kb = [[InlineKeyboardButton("📋 Посмотреть привычки", callback_data="cmd:habits")]]
    await update.message.reply_text(
        f"✅ Привычка *{emoji} {name}* добавлена!\n\nЗавтра с утра она появится в вашем списке.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ConversationHandler.END


# ── /addtask conversation ──────────────────────────────────────────────────

async def addtask_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if update.callback_query:
        await update.callback_query.answer()
        msg = update.callback_query.message
    else:
        msg = update.message

    await msg.reply_text(
        "📝 Введите название задачи:\n_(например: Подготовить отчёт)_",
        parse_mode="Markdown"
    )
    return WAITING_TASK_TITLE


async def addtask_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    emoji = "📌"
    title = text
    if text and len(text) > 1 and not text[0].isalnum():
        emoji = text[0]
        title = text[1:].strip()

    ctx.user_data["task"] = {"title": title, "emoji": emoji}

    kb = [
        [InlineKeyboardButton("Сегодня", callback_data="dl:today"),
         InlineKeyboardButton("Завтра", callback_data="dl:tomorrow")],
        [InlineKeyboardButton("Через 3 дня", callback_data="dl:3"),
         InlineKeyboardButton("Через неделю", callback_data="dl:7")],
        [InlineKeyboardButton("Без дедлайна", callback_data="dl:none")],
    ]
    await update.message.reply_text(
        "📅 Укажите дедлайн:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return WAITING_TASK_DEADLINE


async def addtask_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    val = query.data.split(":")[1]
    today = date.today()

    deadline = None
    if val == "today":
        deadline = today.isoformat()
    elif val == "tomorrow":
        deadline = (today + timedelta(days=1)).isoformat()
    elif val.isdigit():
        deadline = (today + timedelta(days=int(val))).isoformat()

    ctx.user_data["task"]["deadline"] = deadline

    kb = [
        [InlineKeyboardButton("🔴 Срочно", callback_data="pr:urgent"),
         InlineKeyboardButton("🟠 Высокий", callback_data="pr:high")],
        [InlineKeyboardButton("🟡 Средний", callback_data="pr:medium"),
         InlineKeyboardButton("🟢 Низкий", callback_data="pr:low")],
    ]
    await query.edit_message_text("🎯 Выберите приоритет:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_TASK_PRIORITY


async def addtask_priority(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    priority = query.data.split(":")[1]
    ctx.user_data["task"]["priority"] = priority

    task = ctx.user_data["task"]
    uid = update.effective_user.id
    db.create_task(uid, task["title"], task["emoji"], task["deadline"], task["priority"])

    dl_str = f"\n📅 Дедлайн: {task['deadline']}" if task["deadline"] else ""
    pr_str = PRIORITY_MAP.get(task["priority"], "")

    kb = [[InlineKeyboardButton("📋 Все задачи", callback_data="cmd:tasks")]]
    await query.edit_message_text(
        f"✅ Задача добавлена!\n\n"
        f"*{task['emoji']} {task['title']}*{dl_str}\n🎯 {pr_str}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# ── /progress ─────────────────────────────────────────────────────────────

async def progress(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    today = get_today()
    week_start = get_week_start()
    stats = db.get_stats(uid, today, week_start)

    bar_today = "█" * (stats["today_pct"] // 10) + "░" * (10 - stats["today_pct"] // 10)
    bar_week = "█" * (stats["week_pct"] // 10) + "░" * (10 - stats["week_pct"] // 10)

    text = (
        f"📊 *Прогресс*\n\n"
        f"*Сегодня:* {stats['today_pct']}%\n"
        f"`{bar_today}`\n"
        f"{stats['habits_done_today']}/{stats['habits_total']} привычек\n\n"
        f"*Неделя:* {stats['week_pct']}%\n"
        f"`{bar_week}`\n\n"
        f"*Задачи:*\n"
        f"⏳ В работе: {stats['tasks_pending']}\n"
        f"✅ Выполнено: {stats['tasks_done']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── Callback router ────────────────────────────────────────────────────────

async def cmd_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd = query.data.split(":")[1]
    if cmd == "habits":
        await habits_today(update, ctx)
    elif cmd == "tasks":
        await tasks_list(update, ctx)


# ── App factory ────────────────────────────────────────────────────────────

def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # Add habit conversation
    habit_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addhabit", addhabit_start),
            CallbackQueryHandler(addhabit_start, pattern="^cmd:addhabit$"),
        ],
        states={WAITING_HABIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addhabit_name)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Add task conversation
    task_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addtask", addtask_start),
            CallbackQueryHandler(addtask_start, pattern="^cmd:addtask$"),
        ],
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
    app.add_handler(CallbackQueryHandler(task_action_callback, pattern="^task_(done|del):"))
    app.add_handler(CallbackQueryHandler(cmd_callback, pattern="^cmd:"))

    return app
