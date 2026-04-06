import asyncio
import logging
from datetime import datetime, date, timedelta
import pytz

logger = logging.getLogger(__name__)

MOSCOW_TZ = pytz.timezone("Europe/Moscow")


async def check_reminders(bot_app):
    """Проверяет задачи и отправляет напоминания за час до времени."""
    from app.database import db

    now = datetime.now(MOSCOW_TZ)
    today = now.date().isoformat()

    try:
        # Берём все невыполненные задачи с временем на сегодня
        tasks = db.get_tasks_for_reminder(today)

        for task in tasks:
            if not task.get("reminder_time") or task.get("reminded"):
                continue

            # Парсим время задачи
            try:
                task_time = datetime.strptime(
                    f"{task['deadline']} {task['reminder_time']}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=MOSCOW_TZ)
            except Exception:
                continue

            # Проверяем — до задачи от 55 до 65 минут
            diff = (task_time - now).total_seconds() / 60
            logger.info(f"Task '{task['title']}': time={task['reminder_time']}, diff={diff:.1f} min, now={now.strftime('%H:%M')}")
            if 55 <= diff <= 65:
                try:
                    await bot_app.bot.send_message(
                        chat_id=task["user_id"],
                        text=(
                            f"⏰ *Напоминание!*\n\n"
                            f"{task['emoji']} *{task['title']}*\n"
                            f"📅 Сегодня в {task['reminder_time']}\n\n"
                            f"_Через час_"
                        ),
                        parse_mode="Markdown"
                    )
                    # Помечаем как напомненное
                    db.mark_reminded(task["id"])
                    logger.info(f"Reminder sent for task {task['id']} to user {task['user_id']}")
                except Exception as e:
                    logger.error(f"Failed to send reminder: {e}")

    except Exception as e:
        logger.error(f"Reminder check error: {e}")


async def reminder_loop(bot_app):
    """Запускает проверку каждые 5 минут."""
    logger.info("Reminder loop started")
    while True:
        await check_reminders(bot_app)
        await asyncio.sleep(300)  # каждые 5 минут
