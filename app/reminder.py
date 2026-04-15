import asyncio
import logging
from datetime import datetime, date, timedelta
import pytz

logger = logging.getLogger(__name__)
MOSCOW_TZ = pytz.timezone("Europe/Moscow")


async def send_task_reminder(bot_app, task: dict):
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
            f"⏰ *Напоминание о задаче!*\n\n"
            f"{emoji} *{title}*\n"
            f"📅 Сегодня в *{time_str}*\n\n"
            f"_Через час_"
        ),
        parse_mode="Markdown",
        reply_markup=kb
    )
    db.mark_reminded(task["id"])
    logger.info(f"Task reminder sent: {title}")

    # Голосовой звонок с учётом настроек пользователя
    try:
        from app.caller import make_call, notify_call_result
        user = db.get_user(task["user_id"])
        phone = user.get("phone") if user else None
        call_enabled = user.get("call_enabled", True) if user else True
        if phone and call_enabled:
            call_result = await make_call(phone, task, user)
            await notify_call_result(bot_app, task["user_id"], task, call_result)
    except Exception as e:
        logger.error(f"Call error: {e}")


async def send_habit_reminder(bot_app, habit: dict):
    """Напоминание о невыполненной привычке"""
    from app.database import db

    emoji = habit.get("emoji") or "✅"
    name = habit.get("name", "")
    streak = habit.get("current_streak", 0)
    streak_text = f"\n🔥 Стрик: {streak} дней" if streak > 0 else ""

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Выполнено!", callback_data=f"toggle_habit:{habit['id']}:{date.today().isoformat()}"),
        InlineKeyboardButton("⏰ Позже", callback_data=f"habit_snooze:{habit['id']}"),
    ]])

    await bot_app.bot.send_message(
        chat_id=habit["user_id"],
        text=(
            f"🎯 *Не забудь про привычку!*\n\n"
            f"{emoji} *{name}*{streak_text}\n\n"
            f"_Отметь выполнение прямо здесь!_"
        ),
        parse_mode="Markdown",
        reply_markup=kb
    )
    logger.info(f"Habit reminder sent: {name}")


async def check_task_reminders(bot_app):
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
            logger.info(f"Task '{task['title']}': diff={diff:.1f} min")

            # Учитываем индивидуальные настройки звонка
            try:
                user = db.get_user(task["user_id"])
                call_before = user.get("call_before_minutes", 60) if user else 60
            except Exception:
                call_before = 60

            window_low = call_before - 5
            window_high = call_before + 5

            if window_low <= diff <= window_high:
                await send_task_reminder(bot_app, task)
    except Exception as e:
        logger.error(f"Task reminder error: {e}")


async def check_habit_reminders(bot_app):
    """Проверяет привычки с настроенным временем напоминания"""
    from app.database import db
    now = datetime.now(MOSCOW_TZ)
    today = now.date().isoformat()
    current_time = now.strftime("%H:%M")

    try:
        habits = db.get_habits_with_reminders()
        for habit in habits:
            rem_time = habit.get("reminder_time")
            if not rem_time:
                continue

            # Проверяем день недели если настроены конкретные дни
            rem_days = habit.get("reminder_days", "all")
            if rem_days and rem_days != "all":
                allowed = [int(d) for d in rem_days.split(",")]
                if now.weekday() not in allowed:
                    continue

            # Проверяем время (±3 минуты)
            try:
                h, m = map(int, rem_time.split(":"))
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = abs((now - target).total_seconds() / 60)
                if diff > 3:
                    continue
            except Exception:
                continue

            # Проверяем не выполнена ли уже сегодня
            logs = db.get_today_logs(habit["user_id"], today)
            done_ids = {l["habit_id"] for l in logs}
            if habit["id"] in done_ids:
                continue

            await send_habit_reminder(bot_app, habit)

    except Exception as e:
        logger.error(f"Habit reminder error: {e}")


async def send_morning_digest(bot_app):
    """Утренний дайджест в 9:00 — что запланировано на день"""
    from app.database import db
    now = datetime.now(MOSCOW_TZ)
    today = now.date().isoformat()

    try:
        users = db.supabase_get_all_users()
        for user in users:
            uid = user["id"]
            tasks = [t for t in db.get_tasks(uid, completed=False)
                     if t.get("deadline") == today]
            habits = db.get_habits(uid)

            if not tasks and not habits:
                continue

            text = f"🌅 *Доброе утро!*\n\n"
            if habits:
                text += f"🎯 Привычек на сегодня: *{len(habits)}*\n"
            if tasks:
                text += f"\n📋 *Задачи на сегодня:*\n"
                for t in tasks[:5]:
                    time_str = f" в {t['reminder_time']}" if t.get('reminder_time') else ""
                    text += f"• {t['emoji'] or '📌'} {t['title']}{time_str}\n"

            text += "\n_Удачного дня! 💪_"
            await bot_app.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Morning digest error: {e}")


async def send_evening_digest(bot_app):
    """Вечерний обзор в 21:00 — итоги дня и план на завтра"""
    from app.database import db
    from datetime import date, timedelta
    import pytz

    now = datetime.now(MOSCOW_TZ)
    today = now.date().isoformat()
    tomorrow = (now.date() + timedelta(1)).isoformat()

    try:
        users = db.supabase_get_all_users()
        for user in users:
            uid = user["id"]
            try:
                # Итоги сегодня
                all_tasks = db.get_tasks(uid, completed=False) + db.get_tasks(uid, completed=True)
                done_today = [t for t in all_tasks if t.get("completed") and t.get("deadline") == today]
                pending_today = [t for t in all_tasks if not t.get("completed") and t.get("deadline") == today]
                tomorrow_tasks = [t for t in all_tasks if not t.get("completed") and t.get("deadline") == tomorrow]

                habits = db.get_habits(uid)
                logs = db.get_today_logs(uid, today)
                done_ids = {l["habit_id"] for l in logs}
                done_habits = [h for h in habits if h["id"] in done_ids]
                missed_habits = [h for h in habits if h["id"] not in done_ids]

                if not habits and not all_tasks:
                    continue

                # Формируем текст
                lines = ["🌙 *Вечерний обзор дня*\n"]

                # Итоги
                lines.append("📊 *Итоги:*")
                lines.append(f"✅ Задач выполнено: *{len(done_today)}*")
                if pending_today:
                    lines.append(f"⏳ Не выполнено сегодня: *{len(pending_today)}*")
                    for t in pending_today[:3]:
                        lines.append(f"  • {t.get('emoji','📌')} {t['title']}")

                if done_habits:
                    lines.append(f"\n\U0001f3af Привычки выполнены: *{len(done_habits)}/{len(habits)}*")
                    lines.append("Пропущено: " + ", ".join([f"{h['emoji']} {h['name']}" for h in missed_habits[:3]]))

                # Стрик
                lines.append("")
                if len(done_habits) == len(habits) and habits:
                    lines.append("🔥 *Все привычки выполнены! Отличный день!*")

                # План на завтра
                if tomorrow_tasks:
                    lines.append(f"\n📅 *Завтра запланировано:*")
                    for t in tomorrow_tasks[:5]:
                        time_str = f" в {t['reminder_time']}" if t.get("reminder_time") else ""
                        lines.append(f"• {t.get('emoji','📌')} {t['title']}{time_str}")

                lines.append("\n_Хорошего вечера! 🌟_")
                text = "\n".join(lines)


                await bot_app.bot.send_message(
                    chat_id=uid, text=text, parse_mode="Markdown"
                )
                logger.info(f"Evening digest sent to {uid}")
            except Exception as e:
                logger.error(f"Evening digest error for {uid}: {e}")

    except Exception as e:
        logger.error(f"Evening digest global error: {e}")


async def reminder_loop(bot_app):
    logger.info("Reminder loop started")
    last_digest_day = None
    last_evening_day = None

    while True:
        now = datetime.now(MOSCOW_TZ)

        # Утренний дайджест в 9:00
        if now.hour == 9 and now.minute < 5 and now.date() != last_digest_day:
            await send_morning_digest(bot_app)
            last_digest_day = now.date()

        # Вечерний обзор в 21:00
        if now.hour == 21 and now.minute < 5 and now.date() != last_evening_day:
            await send_evening_digest(bot_app)
            last_evening_day = now.date()

        await check_task_reminders(bot_app)
        await check_habit_reminders(bot_app)
        await asyncio.sleep(300)  # каждые 5 минут
