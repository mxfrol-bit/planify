import os
import json
import logging
import httpx
from datetime import date, timedelta

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")


def get_prompt(text: str) -> str:
    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    # Next 7 days with weekday names
    days_ref = "\n".join([
        f"- «{weekdays[(today + timedelta(i)).weekday()]}» = {(today + timedelta(i)).isoformat()}"
        for i in range(1, 8)
    ])

    return f"""Сегодня {today.isoformat()} ({weekdays[today.weekday()]}).
«Завтра» = {tomorrow}.
Ближайшие дни:
{days_ref}

Пользователь пишет боту-планировщику: "{text}"

Извлеки задачу и верни JSON:
{{
  "is_task": true,
  "title": "короткое название (убери слова типа 'нужно', 'надо', 'завтра')",
  "emoji": "один подходящий эмодзи",
  "deadline": "YYYY-MM-DD если упомянута дата/время/день, иначе null",
  "time": "HH:MM если упомянуто время, иначе null",
  "priority": "urgent если срочно/важно, high если скоро дедлайн, medium по умолчанию, low если не важно",
  "category": "work/personal/health/learning/other"
}}

Правила для deadline:
- "завтра", "завтра утром", "завтра в 9" → {tomorrow}
- "сегодня" → {today.isoformat()}
- "в пятницу", "до пятницы" → ближайшая пятница из списка выше
- "через неделю" → {(today + timedelta(7)).isoformat()}
- конкретная дата → конвертируй в YYYY-MM-DD

Если это явно НЕ задача (случайный текст, вопрос боту) → {{"is_task": false}}

Только JSON без пояснений и markdown."""


async def parse_with_gemini(text: str) -> dict | None:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "contents": [{"parts": [{"text": get_prompt(text)}]}],
                "generationConfig": {"maxOutputTokens": 300, "temperature": 0}
            })
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
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
        return json.loads(resp.json()["content"][0]["text"].strip())
    except Exception as e:
        logger.error(f"Anthropic error: {e}")
        return None


async def parse_with_openrouter(text: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "content-type": "application/json"},
                json={"model": "google/gemini-flash-1.5", "max_tokens": 300,
                      "messages": [{"role": "user", "content": get_prompt(text)}]}
            )
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        return None


async def parse_task(text: str) -> dict | None:
    if GEMINI_API_KEY:
        return await parse_with_gemini(text)
    if ANTHROPIC_API_KEY:
        return await parse_with_anthropic(text)
    if OPENROUTER_API_KEY:
        return await parse_with_openrouter(text)
    return None


def ai_available() -> bool:
    return bool(GEMINI_API_KEY or ANTHROPIC_API_KEY or OPENROUTER_API_KEY)
