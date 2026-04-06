import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


class Database:
    """Обёртка над Supabase клиентом"""

    # ── USERS ──────────────────────────────────────────────────────────────

    def get_user(self, telegram_id: int) -> dict | None:
        try:
            res = supabase.table("users").select("*").eq("id", telegram_id).single().execute()
            return res.data if res.data else None
        except Exception:
            return None

    def get_user_by_token(self, token: str) -> dict | None:
        res = supabase.table("users").select("*").eq("web_token", token).single().execute()
        return res.data if res.data else None

    def create_user(self, telegram_id: int, username: str | None, first_name: str | None) -> dict:
        res = supabase.table("users").upsert({
            "id": telegram_id,
            "username": username,
            "first_name": first_name or "User",
        }).execute()
        return res.data[0]

    def get_web_token(self, telegram_id: int) -> str:
        res = supabase.table("users").select("web_token").eq("id", telegram_id).single().execute()
        return res.data["web_token"]

    # ── HABITS ─────────────────────────────────────────────────────────────

    def get_habits(self, user_id: int, active_only: bool = True) -> list:
        q = supabase.table("habits").select("*").eq("user_id", user_id).order("sort_order")
        if active_only:
            q = q.eq("is_active", True)
        return q.execute().data or []

    def create_habit(self, user_id: int, name: str, emoji: str = "✅", frequency: str = "daily") -> dict:
        res = supabase.table("habits").insert({
            "user_id": user_id,
            "name": name,
            "emoji": emoji,
            "frequency": frequency,
        }).execute()
        return res.data[0]

    def delete_habit(self, habit_id: str, user_id: int) -> bool:
        res = supabase.table("habits").update({"is_active": False})\
            .eq("id", habit_id).eq("user_id", user_id).execute()
        return bool(res.data)

    # ── HABIT LOGS ─────────────────────────────────────────────────────────

    def get_today_logs(self, user_id: int, date_str: str) -> list:
        return supabase.table("habit_logs").select("habit_id")\
            .eq("user_id", user_id).eq("logged_date", date_str).execute().data or []

    def toggle_habit(self, habit_id: str, user_id: int, date_str: str) -> bool:
        """Отмечает/снимает привычку за дату. Возвращает True если теперь выполнена."""
        existing = supabase.table("habit_logs").select("id")\
            .eq("habit_id", habit_id).eq("logged_date", date_str).execute().data
        if existing:
            supabase.table("habit_logs").delete().eq("id", existing[0]["id"]).execute()
            return False
        else:
            supabase.table("habit_logs").insert({
                "habit_id": habit_id,
                "user_id": user_id,
                "logged_date": date_str,
            }).execute()
            return True

    def get_habit_logs_range(self, user_id: int, date_from: str, date_to: str) -> list:
        return supabase.table("habit_logs").select("habit_id, logged_date")\
            .eq("user_id", user_id)\
            .gte("logged_date", date_from)\
            .lte("logged_date", date_to)\
            .execute().data or []

    # ── TASKS ──────────────────────────────────────────────────────────────

    def get_tasks(self, user_id: int, completed: bool | None = None) -> list:
        q = supabase.table("tasks").select("*").eq("user_id", user_id).order("deadline", nullsfirst=False)
        if completed is not None:
            q = q.eq("completed", completed)
        return q.execute().data or []

    def create_task(self, user_id: int, title: str, emoji: str = "📌",
                    deadline: str | None = None, priority: str = "medium",
                    category: str = "personal") -> dict:
        res = supabase.table("tasks").insert({
            "user_id": user_id,
            "title": title,
            "emoji": emoji,
            "deadline": deadline,
            "priority": priority,
            "category": category,
        }).execute()
        return res.data[0]

    def toggle_task(self, task_id: str, user_id: int) -> bool:
        """Возвращает новое состояние completed"""
        task = supabase.table("tasks").select("completed").eq("id", task_id).eq("user_id", user_id).single().execute()
        if not task.data:
            return False
        new_state = not task.data["completed"]
        supabase.table("tasks").update({
            "completed": new_state,
            "completed_at": "NOW()" if new_state else None,
        }).eq("id", task_id).execute()
        return new_state

    def delete_task(self, task_id: str, user_id: int) -> bool:
        res = supabase.table("tasks").delete().eq("id", task_id).eq("user_id", user_id).execute()
        return bool(res.data)

    def get_stats(self, user_id: int, date_str: str, week_start: str) -> dict:
        habits = self.get_habits(user_id)
        today_logs = self.get_today_logs(user_id, date_str)
        week_logs = self.get_habit_logs_range(user_id, week_start, date_str)
        tasks_pending = len(self.get_tasks(user_id, completed=False))
        tasks_done = len(self.get_tasks(user_id, completed=True))

        done_ids = {l["habit_id"] for l in today_logs}
        total = len(habits)
        done_today = sum(1 for h in habits if h["id"] in done_ids)
        today_pct = round(done_today / total * 100) if total else 0

        week_done = len(week_logs)
        week_total = total * 7  # упрощённо
        week_pct = round(week_done / week_total * 100) if week_total else 0

        return {
            "habits_total": total,
            "habits_done_today": done_today,
            "today_pct": today_pct,
            "week_pct": week_pct,
            "tasks_pending": tasks_pending,
            "tasks_done": tasks_done,
        }


    def get_tasks_for_reminder(self, date_str: str) -> list:
        """Возвращает задачи на сегодня с временем которые ещё не напомнили."""
        return supabase.table("tasks").select("*")            .eq("deadline", date_str)            .eq("completed", False)            .eq("reminded", False)            .not_.is_("reminder_time", "null")            .execute().data or []

    def mark_reminded(self, task_id: str) -> None:
        supabase.table("tasks").update({"reminded": True})            .eq("id", task_id).execute()

    def unmark_reminded(self, task_id: str) -> None:
        supabase.table("tasks").update({"reminded": False})            .eq("id", task_id).execute()

    def set_reminder_time(self, task_id: str, user_id: int, time_str: str) -> bool:
        res = supabase.table("tasks").update({"reminder_time": time_str})            .eq("id", task_id).eq("user_id", user_id).execute()
        return bool(res.data)


    # ── Habit extras ───────────────────────────────────────────────────────

    def update_habit(self, habit_id: str, user_id: int, data: dict) -> bool:
        res = supabase.table("habits").update(data)            .eq("id", habit_id).eq("user_id", user_id).execute()
        return bool(res.data)

    def get_habits_with_reminders(self) -> list:
        return supabase.table("habits").select("*")            .eq("is_active", True)            .not_.is_("reminder_time", "null")            .execute().data or []

    def calculate_streak(self, habit_id: str, user_id: int) -> tuple:
        from datetime import date, timedelta
        logs = supabase.table("habit_logs").select("logged_date")            .eq("habit_id", habit_id)            .order("logged_date", desc=True)            .limit(365).execute().data or []
        if not logs:
            return 0, 0
        dates = sorted([l["logged_date"] for l in logs], reverse=True)
        today = date.today()
        current = 0
        check = today
        for d in dates:
            if d == check.isoformat() or d == (check - timedelta(1)).isoformat():
                current += 1
                check = date.fromisoformat(d)
            else:
                break
        best = 1
        run = 1
        for i in range(1, len(dates)):
            d1 = date.fromisoformat(dates[i-1])
            d2 = date.fromisoformat(dates[i])
            if (d1 - d2).days == 1:
                run += 1
                best = max(best, run)
            else:
                run = 1
        return current, max(best, current)

    def supabase_get_all_users(self) -> list:
        return supabase.table("users").select("id,first_name").execute().data or []

    def update_streak(self, habit_id: str, user_id: int, streak: int, best: int, last_date: str):
        supabase.table("habits").update({
            "current_streak": streak,
            "best_streak": best,
            "last_done_date": last_date,
        }).eq("id", habit_id).eq("user_id", user_id).execute()


db = Database()
