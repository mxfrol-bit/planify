-- ====================================
-- PlanifyBot - Supabase Schema
-- Выполнить в Supabase SQL Editor
-- ====================================

-- Пользователи (по Telegram ID)
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,  -- telegram_id
    username TEXT,
    first_name TEXT,
    web_token TEXT UNIQUE DEFAULT gen_random_uuid()::text,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Привычки
CREATE TABLE IF NOT EXISTS habits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    emoji TEXT DEFAULT '✅',
    frequency TEXT DEFAULT 'daily' CHECK (frequency IN ('daily', 'weekly', 'monthly')),
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Логи привычек (отметки выполнения)
CREATE TABLE IF NOT EXISTS habit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id UUID REFERENCES habits(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    logged_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(habit_id, logged_date)
);

-- Задачи
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    emoji TEXT DEFAULT '📌',
    deadline DATE,
    priority TEXT DEFAULT 'medium' CHECK (priority IN ('urgent', 'high', 'medium', 'low')),
    category TEXT DEFAULT 'personal' CHECK (category IN ('work', 'personal', 'health', 'learning', 'other')),
    completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS idx_habit_logs_user_date ON habit_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_habits_user ON habits(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, completed);

-- RLS (Row Level Security) - опционально, если используете Supabase Auth
-- ALTER TABLE users ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE habits ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE habit_logs ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;

-- Тестовые данные (опционально, удалите в проде)
-- INSERT INTO users (id, username, first_name) VALUES (123456789, 'test_user', 'Тест');
