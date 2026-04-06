import os
import logging
import httpx
from datetime import date

logger = logging.getLogger(__name__)

ZVONOK_API_KEY = os.getenv("ZVONOK_API_KEY", "")
ZVONOK_CAMPAIGN_ID = os.getenv("ZVONOK_CAMPAIGN_ID", "")
ZVONOK_API_URL = "https://zvonok.com/manager/cabapi_external/api/v1/phones/call/"


async def generate_call_text(task: dict) -> str:
    """Генерирует текст для голосового звонка через AI"""
    from app.ai_parser import GEMINI_API_KEY, OPENROUTER_API_KEY, CLOWD_API_URL

    title = task.get("title", "")
    time_str = task.get("reminder_time", "")
    
    # Простой шаблон без AI
    base_text = f"Здравствуйте! Напоминаю — через час у вас запланировано: {title}."
    if time_str:
        base_text += f" Время: {time_str}."
    base_text += " Удачного дня!"

    # Если есть AI — генерируем более живой текст
    if OPENROUTER_API_KEY or GEMINI_API_KEY or CLOWD_API_URL:
        try:
            prompt = (
                f"Напиши короткий текст для голосового звонка-напоминания (3-4 предложения, разговорный стиль, дружелюбно).\n"
                f"Задача: {title}\n"
                f"Время: {time_str or 'сегодня'}\n"
                f"Текст должен звучать естественно когда его зачитает робот."
            )
            
            if OPENROUTER_API_KEY:
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "content-type": "application/json"},
                        json={"model": "google/gemini-2.0-flash-001", "max_tokens": 150,
                              "messages": [{"role": "user", "content": prompt}]}
                    )
                text = resp.json()["choices"][0]["message"]["content"].strip()
                if text and len(text) > 20:
                    return text
        except Exception as e:
            logger.error(f"AI text generation error: {e}")

    return base_text


async def make_call(phone: str, task: dict) -> dict:
    """Совершает голосовой звонок через Zvonok.com"""
    if not ZVONOK_API_KEY or not ZVONOK_CAMPAIGN_ID:
        logger.warning("Zvonok credentials not configured")
        return {"success": False, "error": "Not configured"}

    # Нормализуем номер — убираем всё кроме цифр
    phone_clean = "".join(filter(str.isdigit, phone))
    if phone_clean.startswith("8"):
        phone_clean = "7" + phone_clean[1:]
    if not phone_clean.startswith("7"):
        phone_clean = "7" + phone_clean

    text = await generate_call_text(task)
    logger.info(f"Calling {phone_clean}: {text[:50]}...")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                ZVONOK_API_URL,
                data={
                    "public_key": ZVONOK_API_KEY,
                    "campaign_id": ZVONOK_CAMPAIGN_ID,
                    "phone": phone_clean,
                    "text": text,
                }
            )
        result = resp.json()
        logger.info(f"Zvonok response: {result}")
        
        if result.get("status") == "ok" or result.get("call_id"):
            return {"success": True, "call_id": result.get("call_id"), "text": text}
        else:
            return {"success": False, "error": result.get("error", "Unknown error")}

    except Exception as e:
        logger.error(f"Call error: {e}")
        return {"success": False, "error": str(e)}


async def notify_call_result(bot_app, user_id: int, task: dict, call_result: dict):
    """Уведомляет пользователя в Telegram о звонке"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if call_result["success"]:
        text = (
            f"📞 *Звоню вам!*\n\n"
            f"{task.get('emoji','📌')} *{task.get('title','')}*\n"
            f"⏰ в {task.get('reminder_time','')}\n\n"
            f"_Текст звонка:_\n{call_result.get('text','')}"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Понял!", callback_data=f"rem_ok:{task['id']}"),
            InlineKeyboardButton("🔕 Отложить", callback_data=f"rem_snooze30:{task['id']}"),
        ]])
    else:
        text = (
            f"⚠️ Не удалось позвонить\n\n"
            f"{task.get('emoji','📌')} {task.get('title','')}\n"
            f"Ошибка: {call_result.get('error','')}"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Всё равно понял", callback_data=f"rem_ok:{task['id']}"),
        ]])

    await bot_app.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown", reply_markup=kb)
