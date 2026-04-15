import os
import json
import logging
import httpx
from datetime import date, timedelta

logger = logging.getLogger(__name__)

CLOWD_API_URL = os.getenv("CLOWD_API_URL", "")
CLOWD_API_KEY = os.getenv("CLOWD_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


def get_prompt(text: str) -> str:
    today = date.today()
    dates = {
        "сегодня": today.isoformat(),
        "завтра": (today + timedelta(1)).isoformat(),
        "послезавтра": (today + timedelta(2)).isoformat(),
        "через неделю": (today + timedelta(7)).isoformat(),
    }
    ru_days = ["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]
    for i in range(1, 8):
        d = today + timedelta(i)
        dates[ru_days[d.weekday()]] = d.isoformat()

    dates_hint = ", ".join([f"{k}={v}" for k, v in dates.items()])

    return (
        f"Сегодня {today.isoformat()}. Даты: {dates_hint}.\n"
        f"Сообщение пользователя: {text}\n\n"
        "ВАЖНО: В сообщении может быть несколько задач. Они могут быть разделены:\n"
        "- Нумерацией: 1) 2) 3) или 1. 2. 3.\n"
        "- Запятыми или точками с запятой\n"
        "- Словами: также, ещё, потом, плюс\n"
        "Каждый пункт = отдельная задача в массиве!\n\n"
        "Верни JSON массив:\n"
        "[{\"is_task\":true,\"title\":\"короткое название задачи\",\"emoji\":\"подходящий эмодзи\","
        "\"deadline\":\"YYYY-MM-DD или null\",\"time\":\"HH:MM или null\","
        "\"priority\":\"urgent/high/medium/low\",\"category\":\"work/personal/health/learning/other\"}]\n"
        "Если не задача: [{\"is_task\":false}]\n"
        "ТОЛЬКО JSON массив. Без markdown, без пояснений. Названия делай короткими (до 60 символов)."
    )


async def parse_with_clowd(text: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{CLOWD_API_URL}/parse",
                headers={
                    "Authorization": f"Bearer {CLOWD_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={"text": text, "today": date.today().isoformat()}
            )
        result = resp.json()
        if isinstance(result, dict):
            result = [result]
        logger.info(f"Clowd OK: {result}")
        return result
    except Exception as e:
        logger.error(f"Clowd error: {e}")
        return None


async def parse_with_openrouter(text: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "content-type": "application/json"},
                json={"model": "google/gemini-2.0-flash-001", "max_tokens": 300,
                      "messages": [{"role": "user", "content": get_prompt(text)}]}
            )
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        if isinstance(result, dict):
            result = [result]
        logger.info(f"OpenRouter OK: {result}")
        return result
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        return None


async def parse_with_gemini(text: str) -> dict | None:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "contents": [{"parts": [{"text": get_prompt(text)}]}],
                "generationConfig": {"maxOutputTokens": 300, "temperature": 0}
            })
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        logger.info(f"Gemini OK: {result}")
        return result
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return None


async def parse_with_anthropic(text: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
                      "messages": [{"role": "user", "content": get_prompt(text)}]}
            )
        result = json.loads(resp.json()["content"][0]["text"].strip())
        logger.info(f"Anthropic OK: {result}")
        return result
    except Exception as e:
        logger.error(f"Anthropic error: {e}")
        return None


async def parse_task(text: str) -> list | None:
    """Приоритет: Clowd → OpenRouter → Gemini → Anthropic"""
    if CLOWD_API_URL and CLOWD_API_KEY:
        result = await parse_with_clowd(text)
        if result:
            return result
    if OPENROUTER_API_KEY:
        result = await parse_with_openrouter(text)
        if result:
            return result
    if GEMINI_API_KEY:
        return await parse_with_gemini(text)
    if ANTHROPIC_API_KEY:
        return await parse_with_anthropic(text)
    return None


def ai_available() -> bool:
    return bool(CLOWD_API_URL or GEMINI_API_KEY or ANTHROPIC_API_KEY or OPENROUTER_API_KEY)
