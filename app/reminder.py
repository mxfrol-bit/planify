import asyncio
import logging
from datetime import datetime, date, timedelta
import pytz

logger = logging.getLogger(__name__)
MOSCOW_TZ = pytz.timezone("Europe/Moscow")


async def send_reminder(bot_app, task: dict):
    from app.database import db
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    emoji = task.get("emoji") or "📌"
    title = task.get("title", "")
    time_str = task.get("reminder_time", "")

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Понял, еду!", callback_data=f"rem_ok:{task['id']}"),
            InlineKeyboardButton("⏰ +30 мин", callback_data=f"rem_snooze30:{task['id']}"),
        ],
        [
            InlineKeyboardButton("🔕 Отложить на час", callback_data=f"rem_snooze60:{task['id']}"),
            InlineKeyboardButton("🗑 Отменить задачу", callback_data=f"rem_cancel:{task['id']}"),
        ],
    ])

    await bot_app.bot.send_message(
        chat_id=task["user_id"],
        text=(
            f"⏰ *Напоминание!*\n\n"
            f"{emoji} *{title}*\n"
            f"📅 Сегодня в *{time_str}*\n\n"
            f"_Через час_"
        ),
        parse_mode="Markdown",
        reply_markup=kb
    )
    db.mark_reminded(task["id"])
    logger.info(f"Reminder sent: {title} at {time_str}")


async def check_reminders(bot_app):
    from app.database import db

    now = datetime.now(MOSCOW_TZ)
    today = now.date().isoformat()

    try:
        tasks = db.get_tasks_for_reminder(today)
        for task in tasks:
            if not task.get("reminder_time"):
                continue
            try:
                task_time = datetime.strptime(
                    f"{task['deadline']} {task['reminder_time']}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=MOSCOW_TZ)
            except Exception:
                continue

            diff = (task_time - now).total_seconds() / 60
            logger.info(f"Task '{task['title']}': diff={diff:.1f} min, now={now.strftime('%H:%M')}")

            if 55 <= diff <= 65:
                await send_reminder(bot_app, task)

    except Exception as e:
        logger.error(f"Reminder check error: {e}")


async def reminder_loop(bot_app):
    logger.info("Reminder loop started")
    while True:
        await check_reminders(bot_app)
        await asyncio.sleep(300)
