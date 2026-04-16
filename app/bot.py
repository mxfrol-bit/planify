import os
import logging
from datetime import date, timedelta
from app.ai_parser import parse_task, ai_available
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from app.database import db

logger = logging.getLogger(__name__)
import tempfile, os

# ── Постоянная клавиатура ──────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup([
    ["📋 Задачи", "🎯 Привычки"],
    ["📊 Прогресс", "🌐 Дашборд"],
    ["➕ Добавить задачу", "📅 Календарь"],
    ["⚙️ Настройки", "❓ Помощь"],
], resize_keyboard=True, input_field_placeholder="Или напиши задачу текстом...")



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

# ── Voice handler ─────────────────────────────────────────────────────────

async def transcribe_voice(file_path: str) -> str | None:
    """Транскрибирует голосовое через OpenRouter Whisper"""
    import httpx

    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    if not OPENROUTER_API_KEY:
        return None

    try:
        with open(file_path, "rb") as f:
            audio_bytes = f.read()

        # OpenRouter поддерживает Whisper через совместимый endpoint
        async with httpx.AsyncClient(timeout=40) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                files={"file": ("voice.oga", audio_bytes, "audio/ogg")},
                data={"model": "openai/whisper-1", "language": "ru", "response_format": "json"}
            )

        logger.info(f"Whisper status: {resp.status_code}")
        if resp.status_code == 200:
            text = resp.json().get("text", "").strip()
            logger.info(f"Whisper transcribed: {text[:60]}")
            return text if text else None
        else:
            logger.error(f"Whisper error: {resp.text[:200]}")
            # Fallback — пробуем через Gemini если есть ключ
            GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
            if not GEMINI_KEY:
                return None
            import base64
            audio_b64 = base64.b64encode(audio_bytes).decode()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
            resp2 = await httpx.AsyncClient(timeout=30).post(url, json={
                "contents": [{"parts": [
                    {"inline_data": {"mime_type": "audio/ogg", "data": audio_b64}},
                    {"text": "Транскрибируй аудио на русском. Только текст."}
                ]}]
            })
            if resp2.status_code == 200:
                return resp2.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            return None

    except Exception as e:
        logger.error(f"Voice transcribe error: {e}")
        return None


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает голосовые сообщения"""
    ensure_user(update)
    uid = update.effective_user.id
    
    msg = await update.message.reply_text("🎙 Слушаю...")
    
    try:
        # Скачиваем голосовое
        voice = update.message.voice or update.message.audio
        if not voice:
            await msg.edit_text("Не удалось получить аудио")
            return
        
        file = await ctx.bot.get_file(voice.file_id)
        
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        
        await file.download_to_drive(tmp_path)
        
        # Транскрибируем
        await msg.edit_text("🎙 Распознаю речь...")
        text = await transcribe_voice(tmp_path)
        
        # Удаляем временный файл
        try:
            os.unlink(tmp_path)
        except:
            pass
        
        if not text:
            await msg.edit_text("Не удалось распознать речь. Попробуй написать текстом.")
            return
        
        # Показываем что распознали
        await msg.edit_text(f"🎙 Распознал: _{text}_", parse_mode="Markdown")
        
        # Парсим через AI как обычный текст
        if not ai_available():
            db.create_task(uid, text, "📌", None, "medium", "personal")
            await update.message.reply_text(f"📌 Записал: *{text}*", parse_mode="Markdown")
            return
        
        parsed_list = await parse_task(text)
        if not parsed_list:
            parsed_list = [{"is_task": True, "title": text, "emoji": "📌", "deadline": None, "time": None, "priority": "medium", "category": "personal"}]
        
        tasks_to_create = [p for p in parsed_list if p.get("is_task")]
        if not tasks_to_create:
            tasks_to_create = [{"title": text, "emoji": "📌", "deadline": None, "time": None, "priority": "medium", "category": "personal"}]
        
        created = []
        for p in tasks_to_create:
            title = p.get("title", text)
            emoji = p.get("emoji") or "📌"
            deadline = p.get("deadline")
            time_str = p.get("time")
            priority = p.get("priority", "medium")
            category = p.get("category", "personal")
            task = db.create_task(uid, title, emoji, deadline, priority, category)
            if time_str and deadline:
                db.set_reminder_time(task["id"], uid, time_str)
            created.append({"title": title, "emoji": emoji, "deadline": deadline, "time_str": time_str, "priority": priority, "task": task})
        
        if len(created) == 1:
            t = created[0]
            dl = f"\n📅 {t['deadline']}" if t["deadline"] else ""
            tm = f" в {t['time_str']}" if t["time_str"] else ""
            pr = PRIORITY_MAP.get(t["priority"], "")
            kb = [[InlineKeyboardButton("✅ Окей", callback_data="ai_ok"),
                   InlineKeyboardButton("🗑 Удалить", callback_data=f"task_del:{t['task']['id']}")]]
            await update.message.reply_text(
                f"📌 Записал:\n\n*{t['emoji']} {t['title']}*{dl}{tm}\n{pr}",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
            )
        else:
            lines = [f"{t['emoji']} *{t['title']}*" + (f" · {t['deadline']}" if t['deadline'] else "") for t in created]
            await update.message.reply_text(
                f"📌 Записал *{len(created)}* задачи:\n\n" + "\n".join(lines),
                parse_mode="Markdown"
            )
    
    except Exception as e:
        logger.error(f"Voice handler error: {e}")
        await msg.edit_text("Ошибка обработки голосового. Попробуй написать текстом.")


# ── Free text handler ─────────────────────────────────────────────────────

async def handle_free_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text_in = update.message.text.strip()

    # Обрабатываем кнопки постоянной клавиатуры
    if text_in == "📋 Задачи":
        await tasks_list(update, ctx); return
    if text_in == "🎯 Привычки":
        await habits_today(update, ctx); return
    if text_in == "📊 Прогресс":
        await progress(update, ctx); return
    if text_in == "🌐 Дашборд":
        await web_link(update, ctx); return
    if text_in == "➕ Добавить задачу":
        await update.message.reply_text("✏️ Напиши задачу в свободной форме:\n_«Встреча с Андреем завтра в 14:00»_", parse_mode="Markdown"); return
    if text_in == "📅 Календарь":
        await calendar_cmd(update, ctx); return
    if text_in == "⚙️ Настройки":
        await settings_cmd(update, ctx); return
    if text_in == "❓ Помощь":
        await help_cmd(update, ctx); return

    # Иначе обрабатываем как задачу
    ensure_user(update)
    text = update.message.text.strip()
    uid = update.effective_user.id

    if ai_available():
        msg = await update.message.reply_text("⏳ Записываю...")
        parsed_list = await parse_task(text)

        # Если AI не вернул ничего — сохраняем как есть
        if not parsed_list:
            parsed_list = [{"is_task": True, "title": text, "emoji": "📌", "deadline": None, "time": None, "priority": "medium", "category": "personal"}]

        # Фильтруем только задачи
        tasks_to_create = [p for p in parsed_list if p.get("is_task")]
        if not tasks_to_create:
            tasks_to_create = [{"is_task": True, "title": text, "emoji": "📌", "deadline": None, "time": None, "priority": "medium", "category": "personal"}]

        created_tasks = []
        for p in tasks_to_create:
            title = p.get("title", text)
            emoji = p.get("emoji") or "📌"
            deadline = p.get("deadline")
            time_str = p.get("time")
            priority = p.get("priority", "medium")
            category = p.get("category", "personal")
            task = db.create_task(uid, title, emoji, deadline, priority, category)
            if time_str and deadline:
                db.set_reminder_time(task["id"], uid, time_str)
            created_tasks.append({"task": task, "title": title, "emoji": emoji, "deadline": deadline, "time_str": time_str, "priority": priority})

        if len(created_tasks) == 1:
            t = created_tasks[0]
            dl = f"\n📅 {t['deadline']}" if t['deadline'] else ""
            tm = f" в {t['time_str']}" if t['time_str'] else ""
            pr = PRIORITY_MAP.get(t['priority'], "")
            kb = [[InlineKeyboardButton("✅ Окей", callback_data="ai_ok"),
                   InlineKeyboardButton("🗑 Удалить", callback_data=f"task_del:{t['task']['id']}")]]
            await msg.edit_text(f"📌 Записал:\n\n*{t['emoji']} {t['title']}*{dl}{tm}\n{pr}",
                                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        else:
            # Несколько задач
            lines = []
            for t in created_tasks:
                dl = f" · {t['deadline']}" if t['deadline'] else ""
                tm = f" в {t['time_str']}" if t['time_str'] else ""
                lines.append(f"{t['emoji']} *{t['title']}*{dl}{tm}")
            text_out = "📌 Записал *{}* задачи:\n\n".format(len(created_tasks)) + "\n".join(lines)
            await msg.edit_text(text_out, parse_mode="Markdown")
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
    uid = update.effective_user.id
    name = update.effective_user.first_name
    ai_status = "🤖 AI активен" if ai_available() else "💡 Базовый режим"

    # Проверяем новый ли пользователь
    user = db.get_user(uid)
    is_new = not user.get("phone") and not db.get_habits(uid)

    if is_new:
        # Онбординг для новых пользователей
        text = (
            f"👋 Привет, *{name}*! Добро пожаловать в *Planify*!\n\n"
            f"Я — твой личный ИИ-планировщик. Вот что я умею:\n\n"
            f"📋 *Задачи* — пишешь в свободной форме, я сам разбираю дату и время\n"
            f"🎯 *Привычки* — слежу за стриками, напоминаю в нужное время\n"
            f"📞 *Звонки* — звоню за час до важных встреч\n"
            f"📅 *Календарь* — синхронизация с iPhone и Google\n"
            f"🤖 *ИИ* — понимаю голосовые сообщения\n\n"
            f"Давай настроим всё за 2 минуты! 🚀"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Начать настройку", callback_data="onboard:start")],
            [InlineKeyboardButton("⏭ Пропустить", callback_data="onboard:skip")],
        ])
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KB)
        await update.message.reply_text("Хочешь пройти быструю настройку?", reply_markup=kb)
    else:
        # Для существующих пользователей
        tasks_count = len(db.get_tasks(uid, completed=False))
        habits = db.get_habits(uid)
        from datetime import date
        today = date.today().isoformat()
        logs = db.get_today_logs(uid, today)
        done_count = len(logs)

        text = (
            f"👋 С возвращением, *{name}*!\n\n"
            f"📊 *Сегодня:* {done_count}/{len(habits)} привычек выполнено\n"
            f"📋 *Активных задач:* {tasks_count}\n\n"
            f"{ai_status}"
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KB)


async def onboard_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    name = update.effective_user.first_name
    data = query.data

    if data == "onboard:start":
        await query.edit_message_text(
            "📞 *Шаг 1 из 3: Номер телефона*\n\n"
            "Бот будет звонить тебе за час до важных встреч — голосом зачитает все задачи на ближайшее время.\n\n"
            "Отправь свой номер:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ Пропустить", callback_data="onboard:habits")],
            ])
        )
        ctx.user_data["onboard_step"] = "phone"

    elif data == "onboard:habits":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏃 Бег", callback_data="onboard:habit:🏃:Бег"),
             InlineKeyboardButton("💧 Вода", callback_data="onboard:habit:💧:Вода 2л")],
            [InlineKeyboardButton("📚 Чтение", callback_data="onboard:habit:📚:Чтение"),
             InlineKeyboardButton("💊 Витамины", callback_data="onboard:habit:💊:Витамины")],
            [InlineKeyboardButton("🧘 Медитация", callback_data="onboard:habit:🧘:Медитация"),
             InlineKeyboardButton("📝 Дневник", callback_data="onboard:habit:📝:Дневник")],
            [InlineKeyboardButton("✅ Готово →", callback_data="onboard:done")],
        ])
        await query.edit_message_text(
            "🎯 *Шаг 2 из 3: Привычки*\n\n"
            "Выбери привычки которые хочешь отслеживать (можно несколько):",
            parse_mode="Markdown",
            reply_markup=kb
        )

    elif data.startswith("onboard:habit:"):
        parts = data.split(":")
        emoji, name_h = parts[2], parts[3]
        db.create_habit(uid, name_h, emoji, "daily")
        await query.answer(f"✅ {emoji} {name_h} добавлена!")

    elif data == "onboard:done":
        await query.edit_message_text(
            "🌐 *Шаг 3 из 3: Дашборд*\n\n"
            "Открой веб-дашборд — там Канбан задач, аналитика, ИИ-ассистент и настройки календаря!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Открыть дашборд", callback_data="cmd:web")],
                [InlineKeyboardButton("✅ Всё готово!", callback_data="onboard:finish")],
            ])
        )

    elif data == "onboard:skip" or data == "onboard:finish":
        await query.edit_message_text(
            f"🚀 *Всё готово, {name}!*\n\n"
            "Используй кнопки внизу или просто напиши задачу текстом.\n\n"
            "💬 Например: _«Встреча с командой завтра в 10:00»_\n"
            "🎙 Или отправь голосовое сообщение!",
            parse_mode="Markdown"
        )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Как добавить задачу?", callback_data="help:tasks")],
        [InlineKeyboardButton("🎯 Как работают привычки?", callback_data="help:habits")],
        [InlineKeyboardButton("📞 Как настроить звонки?", callback_data="help:calls")],
        [InlineKeyboardButton("📅 Как подключить календарь?", callback_data="help:calendar")],
        [InlineKeyboardButton("🎙 Голосовые сообщения", callback_data="help:voice")],
    ])
    await update.message.reply_text(
        "❓ *Помощь — Planify*\n\nВыбери тему:",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def help_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    topic = query.data.split(":")[1]

    texts = {
        "tasks": (
            "📋 *Как добавить задачу*\n\n"
            "Просто напиши мне в свободной форме:\n\n"
            "• _«Встреча с Андреем завтра в 14:00»_\n"
            "• _«Купить шины в пятницу, срочно»_\n"
            "• _«Позвонить маме сегодня вечером»_\n\n"
            "Я сам определю дату, время и приоритет!\n\n"
            "Или нажми *➕ Добавить задачу* и следуй инструкциям."
        ),
        "habits": (
            "🎯 *Как работают привычки*\n\n"
            "1. Нажми *🎯 Привычки* → добавь привычку\n"
            "2. Каждый день отмечай выполнение\n"
            "3. Стрик 🔥 растёт при ежедневном выполнении\n\n"
            "В дашборде можно настроить:\n"
            "• Время напоминания (бот напомнит в нужное время)\n"
            "• Дни недели (например, только пн-пт)\n\n"
            "Рекорд стрика сохраняется навсегда 🏆"
        ),
        "calls": (
            "📞 *Голосовые звонки*\n\n"
            "Бот звонит за час до задач с напоминанием и зачитывает все дела на ближайшее время.\n\n"
            "Чтобы включить:\n"
            "1. Отправь: `/setphone +79001234567`\n"
            "2. Добавь задачу с временем\n"
            "3. За час до — получишь звонок!\n\n"
            "Тест звонка — в дашборде на главной странице 📲"
        ),
        "calendar": (
            "📅 *Подписка на календарь*\n\n"
            "Все задачи появятся в родном Календаре iPhone или Google.\n\n"
            "Нажми /calendar — получишь кнопки для каждой платформы.\n\n"
            "*iPhone/Mac:* Нажми 🍎 — iOS сам предложит добавить\n"
            "*Google:* Нажми 🗓 — откроется Google Calendar"
        ),
        "voice": (
            "🎙 *Голосовые сообщения*\n\n"
            "Запиши голосовое — я транскрибирую и создам задачи!\n\n"
            "Можно надиктовать сразу несколько:\n"
            "_«Завтра в 9 встреча с командой, в четверг купить шины, позвонить маме в воскресенье»_\n\n"
            "→ Создам 3 задачи с правильными датами!"
        ),
    }

    await query.edit_message_text(
        texts.get(topic, "Раздел не найден"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("← Назад", callback_data="help:back")]
        ])
    )


async def settings_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = db.get_user(uid)
    if not user:
        await update.message.reply_text("Ошибка загрузки профиля")
        return

    phone = user.get("phone") or "не установлен"
    call_enabled = user.get("call_enabled", True)
    call_before = user.get("call_before_minutes", 60)
    call_name = user.get("call_name") or update.effective_user.first_name
    obsidian = user.get("obsidian_webhook") or "не подключён"

    call_status = f"✅ за {call_before} мин" if call_enabled else "❌ отключены"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Телефон", callback_data="set:phone"),
         InlineKeyboardButton("🔔 Звонки: " + ("вкл" if call_enabled else "выкл"), callback_data="set:call_toggle")],
        [InlineKeyboardButton("⏰ Звонить за:", callback_data="set:call_time")],
        [InlineKeyboardButton("👤 Имя для звонка", callback_data="set:call_name")],
        [InlineKeyboardButton("📅 Календарь", callback_data="cmd:calendar"),
         InlineKeyboardButton("📓 Obsidian", callback_data="set:obsidian")],
        [InlineKeyboardButton("🗑 Очистить выполненные", callback_data="set:clear_done")],
    ])

    await update.message.reply_text(
        f"⚙️ *Настройки*\n\n"
        f"📞 Телефон: `{phone}`\n"
        f"🔔 Звонки: {call_status}\n"
        f"👤 Имя в звонке: *{call_name}*\n"
        f"📓 Obsidian: `{obsidian[:30]}...` " + ("✅" if obsidian != "не подключён" else "") + "\n\n"
        f"Что хочешь изменить?",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def settings_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "phone":
        await query.edit_message_text(
            "📞 Отправь номер телефона командой:\n`/setphone +79001234567`",
            parse_mode="Markdown"
        )

    elif action == "call_toggle":
        user = db.get_user(uid)
        new_val = not user.get("call_enabled", True)
        db.update_user_settings(uid, {"call_enabled": new_val})
        status = "включены ✅" if new_val else "отключены ❌"
        await query.answer(f"Звонки {status}", show_alert=True)
        await settings_cmd_from_query(query, uid)

    elif action == "call_time":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("15 мин", callback_data="set:call_set:15"),
             InlineKeyboardButton("30 мин", callback_data="set:call_set:30")],
            [InlineKeyboardButton("45 мин", callback_data="set:call_set:45"),
             InlineKeyboardButton("60 мин", callback_data="set:call_set:60")],
            [InlineKeyboardButton("90 мин", callback_data="set:call_set:90"),
             InlineKeyboardButton("120 мин", callback_data="set:call_set:120")],
            [InlineKeyboardButton("← Назад", callback_data="set:back")],
        ])
        await query.edit_message_text(
            "⏰ За сколько минут звонить?",
            reply_markup=kb
        )

    elif action == "call_set":
        mins = int(parts[2]) if len(parts) > 2 else 60
        db.update_user_settings(uid, {"call_before_minutes": mins})
        await query.answer(f"✅ Буду звонить за {mins} минут", show_alert=True)
        await settings_cmd_from_query(query, uid)

    elif action == "call_name":
        await query.edit_message_text(
            "👤 Как обращаться к тебе в звонке?\n\nОтправь имя командой:\n`/setname Владимир`",
            parse_mode="Markdown"
        )

    elif action == "obsidian":
        await query.edit_message_text(
            "📓 *Интеграция с Obsidian*\n\n"
            "Planify может отправлять задачи прямо в Obsidian vault!\n\n"
            "*Как настроить:*\n"
            "1. Установи плагин *Local REST API* в Obsidian\n"
            "2. Включи его в настройках\n"
            "3. Скопируй API ключ\n"
            "4. Отправь команду:\n`/obsidian http://localhost:27123 твой_ключ`\n\n"
            "Задачи будут добавляться в файл `Tasks.md` в твоём vault!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data="set:back")]])
        )

    elif action == "clear_done":
        from app.database import supabase
        supabase.table("tasks").delete().eq("user_id", uid).eq("completed", True).execute()
        await query.answer("✅ Выполненные задачи удалены!", show_alert=True)

    elif action == "back":
        await settings_cmd_from_query(query, uid)


async def settings_cmd_from_query(query, uid: int):
    user = db.get_user(uid)
    if not user:
        return
    phone = user.get("phone") or "не установлен"
    call_enabled = user.get("call_enabled", True)
    call_before = user.get("call_before_minutes", 60)
    call_name = user.get("call_name") or "не задано"
    obsidian = "✅ подключён" if user.get("obsidian_webhook") else "не подключён"
    call_status = f"✅ за {call_before} мин" if call_enabled else "❌ отключены"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Телефон", callback_data="set:phone"),
         InlineKeyboardButton("🔔 Звонки: " + ("вкл" if call_enabled else "выкл"), callback_data="set:call_toggle")],
        [InlineKeyboardButton("⏰ Звонить за:", callback_data="set:call_time")],
        [InlineKeyboardButton("👤 Имя для звонка", callback_data="set:call_name")],
        [InlineKeyboardButton("📅 Календарь", callback_data="cmd:calendar"),
         InlineKeyboardButton("📓 Obsidian", callback_data="set:obsidian")],
        [InlineKeyboardButton("🗑 Очистить выполненные", callback_data="set:clear_done")],
    ])
    await query.edit_message_text(
        f"⚙️ *Настройки*\n\n"
        f"📞 Телефон: `{phone}`\n"
        f"🔔 Звонки: {call_status}\n"
        f"👤 Имя: *{call_name}*\n"
        f"📓 Obsidian: {obsidian}\n\n"
        f"Что хочешь изменить?",
        parse_mode="Markdown",
        reply_markup=kb
    )



async def calendar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /calendar — ссылка на подписку календаря"""
    ensure_user(update)
    uid = update.effective_user.id
    token = db.get_web_token(uid)
    base_url = WEBHOOK_URL or "https://planify-production-6462.up.railway.app"
    
    ical_url = f"{base_url}/calendar/{token}.ics"
    webcal_url = ical_url.replace("https://", "webcal://").replace("http://", "webcal://")
    google_url = f"https://calendar.google.com/calendar/r?cid={ical_url}"
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Добавить на iPhone/Mac", url=webcal_url)],
        [InlineKeyboardButton("🗓 Добавить в Google Calendar", url=google_url)],
        [InlineKeyboardButton("📋 Скопировать iCal URL", url=ical_url)],
    ])
    
    await update.message.reply_text(
        "📅 *Подписка на календарь Planify*\n\n"
        "Все задачи с дедлайнами появятся в родном Календаре!\n\n"
        "Выбери платформу:",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def setphone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /setphone +79001234567"""
    ensure_user(update)
    uid = update.effective_user.id
    args = ctx.args
    if not args:
        await update.message.reply_text("Укажите номер телефона: /setphone +79001234567")
        return
    phone = args[0].strip()
    from app.database import supabase
    supabase.table("users").update({"phone": phone}).eq("id", uid).execute()
    await update.message.reply_text(f"Номер сохранён: {phone}. Буду звонить за час до задач!")

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

async def reminder_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    parts = query.data.split(":")
    action, task_id = parts[0], parts[1]

    if action == "rem_ok":
        await query.edit_message_text("✅ Отлично! Удачи на встрече!")
    elif action == "rem_snooze30":
        from datetime import datetime, timedelta
        import pytz
        tz = pytz.timezone("Europe/Moscow")
        new_time = (datetime.now(tz) + timedelta(minutes=90)).strftime("%H:%M")
        db.set_reminder_time(task_id, uid, new_time)
        db.unmark_reminded(task_id)
        await query.edit_message_text(f"⏰ Напомню в {new_time}")
    elif action == "rem_snooze60":
        from datetime import datetime, timedelta
        import pytz
        tz = pytz.timezone("Europe/Moscow")
        new_time = (datetime.now(tz) + timedelta(minutes=120)).strftime("%H:%M")
        db.set_reminder_time(task_id, uid, new_time)
        db.unmark_reminded(task_id)
        await query.edit_message_text(f"⏰ Напомню в {new_time}")
    elif action == "rem_cancel":
        db.delete_task(task_id, uid)
        await query.edit_message_text("🗑 Задача удалена.")


async def habit_settings_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Настройки конкретной привычки"""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[0]
    habit_id = parts[1] if len(parts) > 1 else None
    uid = update.effective_user.id

    if action == "habit_snooze":
        await query.edit_message_text("⏰ Напомню через 30 минут!")
    elif action == "habit_settings":
        habits = db.get_habits(uid)
        habit = next((h for h in habits if h["id"] == habit_id), None)
        if not habit:
            return
        streak = habit.get("current_streak", 0)
        best = habit.get("best_streak", 0)
        rem_time = habit.get("reminder_time", "не задано")
        kb = [
            [InlineKeyboardButton("⏰ Время напоминания", callback_data=f"habit_set_time:{habit_id}")],
            [InlineKeyboardButton("📅 Дни недели", callback_data=f"habit_set_days:{habit_id}")],
            [InlineKeyboardButton("← Назад", callback_data="cmd:habits")],
        ]
        await query.edit_message_text(
            f"⚙️ *{habit['emoji']} {habit['name']}*\n\n🔥 Стрик: *{streak} дней*\n🏆 Рекорд: *{best} дней*\n⏰ Напоминание: *{rem_time}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )


async def cmd_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd = query.data.split(":")[1]
    if cmd == "habits": await habits_today(update, ctx)
    elif cmd == "tasks": await tasks_list(update, ctx)
    elif cmd == "progress":
        # Имитируем update.message для progress
        class FakeUpdate:
            effective_user = query.from_user
            message = query.message
        await progress(FakeUpdate(), ctx)
    elif cmd == "web":
        class FakeUpdate:
            effective_user = query.from_user
            message = query.message
        await web_link(FakeUpdate(), ctx)
    elif cmd == "calendar":
        class FakeUpdate:
            effective_user = query.from_user
            message = query.message
        await calendar_cmd(FakeUpdate(), ctx)
    elif cmd == "setphone":
        await query.message.reply_text("Отправьте: /setphone +79001234567")

# ── AddHabit conversation ─────────────────────────────────────────────────
WAITING_HABIT_NAME = "WAITING_HABIT_NAME"

async def addhabit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            "🎯 Как называется привычка?\n_Например: Пробежка 30 мин_",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "🎯 Как называется привычка?\n_Например: Пробежка 30 мин_",
            parse_mode="Markdown"
        )
    return WAITING_HABIT_NAME

async def addhabit_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    name = update.message.text.strip()
    # Simple emoji detection
    emojis = {"бег":"🏃","пробежка":"🏃","вода":"💧","чтение":"📚","медитация":"🧘",
              "витамин":"💊","дневник":"📝","спорт":"💪","йога":"🧘","сон":"😴"}
    emoji = "✅"
    for k, v in emojis.items():
        if k in name.lower():
            emoji = v
            break
    db.create_habit(uid, name, emoji, "daily")
    await update.message.reply_text(
        f"✅ Привычка добавлена!\n\n{emoji} *{name}*\n\nТеперь отмечай каждый день в /habits",
        parse_mode="Markdown",
        reply_markup=MAIN_KB
    )
    return ConversationHandler.END


# ── AddTask conversation ───────────────────────────────────────────────────
WAITING_TASK_TITLE = "WAITING_TASK_TITLE"
WAITING_TASK_DEADLINE = "WAITING_TASK_DEADLINE"
WAITING_TASK_PRIORITY = "WAITING_TASK_PRIORITY"

async def addtask_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            "📋 Напиши задачу в свободной форме:\n_«Встреча с Андреем завтра в 14:00»_\n\nИли просто название:",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "📋 Напиши задачу в свободной форме:\n_«Встреча с Андреем завтра в 14:00»_",
            parse_mode="Markdown"
        )
    return WAITING_TASK_TITLE

async def addtask_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Just pass to free text handler which handles AI parsing
    await handle_free_text(update, ctx)
    return ConversationHandler.END

async def addtask_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END

async def addtask_priority(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END


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
    app.add_handler(CommandHandler("setphone", setphone))
    app.add_handler(CommandHandler("calendar", calendar_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("setname", setname_cmd))
    app.add_handler(CommandHandler("obsidian", obsidian_cmd))
    app.add_handler(CallbackQueryHandler(onboard_callback, pattern="^onboard:"))
    app.add_handler(CallbackQueryHandler(help_callback, pattern="^help:"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern="^set:"))
    app.add_handler(habit_conv)
    app.add_handler(task_conv)
    app.add_handler(CallbackQueryHandler(toggle_habit_callback, pattern="^toggle_habit:"))
    app.add_handler(CallbackQueryHandler(task_action_callback, pattern="^(task_done|task_view|task_del|ai_ok)"))
    app.add_handler(CallbackQueryHandler(habit_settings_callback, pattern="^habit_(snooze|settings|set_time|set_days):"))
    app.add_handler(CallbackQueryHandler(reminder_callback, pattern="^rem_"))
    app.add_handler(CallbackQueryHandler(cmd_callback, pattern="^cmd:"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))

    return app
