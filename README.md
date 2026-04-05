# 🗓 PlanifyBot — Трекер привычек и задач

Telegram бот + веб-дашборд для отслеживания привычек и задач.
Стек: **FastAPI + Supabase + Railway** (всё бесплатно).

---

## 🚀 Деплой за 10 минут

### 1. Supabase (база данных)
1. Зайди на [supabase.com](https://supabase.com) → New Project
2. Зайди в **SQL Editor** → вставь содержимое `sql/schema.sql` → Run
3. Скопируй из **Settings → API**:
   - `Project URL` → `SUPABASE_URL`
   - `anon public` key → `SUPABASE_KEY`

### 2. Telegram Bot
1. Открой [@BotFather](https://t.me/BotFather) → `/newbot`
2. Получи `BOT_TOKEN`
3. Установи команды:
```
/setcommands
habits - Привычки на сегодня
tasks - Список задач
addhabit - Добавить привычку
addtask - Добавить задачу
progress - Прогресс за неделю
web - Открыть веб-дашборд
```

### 3. Railway (сервер)
1. Зайди на [railway.app](https://railway.app) → New Project → Deploy from GitHub
2. Добавь переменные в **Variables**:
   ```
   BOT_TOKEN=...
   SUPABASE_URL=...
   SUPABASE_KEY=...
   WEBHOOK_URL=https://your-app.up.railway.app
   ```
3. После деплоя скопируй URL из Railway → обнови `WEBHOOK_URL`
4. Redeploy (один раз, чтобы webhook зарегистрировался)

---

## 📱 Структура проекта

```
planify/
├── app/
│   ├── main.py        # FastAPI приложение + API роуты
│   ├── bot.py         # Telegram бот (все команды)
│   ├── database.py    # Supabase клиент
│   └── models.py      # Pydantic модели
├── frontend/
│   └── index.html     # Веб-дашборд (SPA)
├── sql/
│   └── schema.sql     # SQL схема для Supabase
├── railway.toml       # Конфиг Railway
├── requirements.txt
└── .env.example
```

## 🤖 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Регистрация + приветствие |
| `/habits` | Привычки на сегодня с отметками |
| `/addhabit` | Добавить новую привычку |
| `/tasks` | Список активных задач |
| `/addtask` | Добавить задачу (с дедлайном и приоритетом) |
| `/progress` | Прогресс за сегодня и неделю |
| `/web` | Ссылка на веб-дашборд |

## 🌐 Веб-дашборд

- Доступен по ссылке из бота (/web команда)
- Авторизация через уникальный токен в URL
- Страницы: Дашборд, Привычки, Задачи

## 🔜 Планы (Clowd Bot интеграция)

- [ ] AI-анализ привычек и рекомендации
- [ ] Умные напоминания через Clowd Bot
- [ ] Еженедельный AI-отчёт по прогрессу
- [ ] Голосовое добавление задач через бота
