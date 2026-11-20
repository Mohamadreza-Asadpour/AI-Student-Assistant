"""لایه SQLite: کاربرها، تاریخچه چت، آزمون‌ها و برنامه مطالعه. جزئیات محتوای RAG خودش تو ChromaDB ذخیره میشه، اینجا فقط ref سبک نگه می‌داریم."""
import sqlite3
import json
import time
import contextlib

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'chat',
    title TEXT,
    course TEXT,
    level TEXT DEFAULT 'متوسط',
    created_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,           -- 'user' | 'assistant'
    content TEXT NOT NULL,
    sources_json TEXT,            -- منابع (فایل/صفحه) استفاده‌شده برای این پاسخ، در صورت وجود
    created_at REAL NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    course TEXT,
    topic TEXT,
    title TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    question_type TEXT NOT NULL,   -- multiple_choice | true_false | essay
    question_text TEXT NOT NULL,
    options_json TEXT,             -- برای چهارگزینه‌ای
    correct_answer TEXT,
    explanation TEXT,
    topic_tag TEXT,                -- زیرموضوع، برای تحلیل نقاط ضعف
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    score REAL,
    total REAL,
    created_at REAL NOT NULL,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS quiz_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    student_answer TEXT,
    is_correct INTEGER,       -- 1/0 برای تستی، NULL برای تشریحی تا با AI ارزیابی شود
    score REAL,                -- نمره تقریبی (برای تشریحی)
    feedback TEXT,
    FOREIGN KEY (attempt_id) REFERENCES quiz_attempts(id),
    FOREIGN KEY (question_id) REFERENCES quiz_questions(id)
);

CREATE TABLE IF NOT EXISTS study_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    course TEXT,
    days_remaining INTEGER,
    daily_hours REAL,
    plan_json TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""


def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with contextlib.closing(get_conn()) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def get_or_create_user(username: str) -> int:
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO users (username, created_at) VALUES (?, ?)",
            (username, time.time()),
        )
        conn.commit()
        return cur.lastrowid


def create_conversation(user_id: int, mode: str = "chat", title: str = None,
                         course: str = None, level: str = "متوسط") -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO conversations (user_id, mode, title, course, level, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, mode, title, course, level, time.time()),
        )
        conn.commit()
        return cur.lastrowid


def add_message(conversation_id: int, role: str, content: str, sources: list = None):
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, sources_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content, json.dumps(sources or [], ensure_ascii=False), time.time()),
        )
        conn.commit()


def get_conversation(conversation_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return dict(row) if row else None


def get_messages(conversation_id: int, limit: int = 50):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT role, content, sources_json, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id ASC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "role": r["role"],
                "content": r["content"],
                "sources": json.loads(r["sources_json"] or "[]"),
                "created_at": r["created_at"],
            })
        return out


def list_conversations(user_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT c.id, c.mode, c.title, c.course, c.level, c.created_at, "
            "       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            "FROM conversations c WHERE c.user_id = ? ORDER BY c.created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_quiz(user_id: int, course: str, topic: str, title: str, questions: list) -> int:
    """questions: dict با question_type, question_text, options?, correct_answer, explanation, topic_tag?"""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO quizzes (user_id, course, topic, title, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, course, topic, title, time.time()),
        )
        quiz_id = cur.lastrowid
        for q in questions:
            conn.execute(
                "INSERT INTO quiz_questions "
                "(quiz_id, question_type, question_text, options_json, correct_answer, explanation, topic_tag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    quiz_id,
                    q["question_type"],
                    q["question_text"],
                    json.dumps(q.get("options"), ensure_ascii=False) if q.get("options") else None,
                    q.get("correct_answer"),
                    q.get("explanation"),
                    q.get("topic_tag", topic),
                ),
            )
        conn.commit()
        return quiz_id


def get_quiz(quiz_id: int):
    with contextlib.closing(get_conn()) as conn:
        quiz = conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
        if not quiz:
            return None
        questions = conn.execute(
            "SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY id ASC", (quiz_id,)
        ).fetchall()
        quiz_dict = dict(quiz)
        quiz_dict["questions"] = []
        for q in questions:
            qd = dict(q)
            qd["options"] = json.loads(qd["options_json"]) if qd["options_json"] else None
            del qd["options_json"]
            quiz_dict["questions"].append(qd)
        return quiz_dict


def list_quizzes(user_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, course, topic, title, created_at FROM quizzes "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_quiz_attempt(quiz_id: int, user_id: int, answers: list) -> dict:
    """answers: {question_id, student_answer, is_correct, score, feedback}"""
    total = len(answers)
    correct_count = sum(1 for a in answers if a.get("is_correct") is True)
    score = sum(a.get("score", 1 if a.get("is_correct") else 0) or 0 for a in answers)

    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO quiz_attempts (quiz_id, user_id, score, total, created_at) VALUES (?, ?, ?, ?, ?)",
            (quiz_id, user_id, score, total, time.time()),
        )
        attempt_id = cur.lastrowid
        for a in answers:
            conn.execute(
                "INSERT INTO quiz_answers "
                "(attempt_id, question_id, student_answer, is_correct, score, feedback) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    a["question_id"],
                    a.get("student_answer"),
                    1 if a.get("is_correct") is True else (0 if a.get("is_correct") is False else None),
                    a.get("score"),
                    a.get("feedback"),
                ),
            )
        conn.commit()

    return {"attempt_id": attempt_id, "score": score, "total": total, "correct_count": correct_count}


def get_weak_topics(user_id: int, min_attempts: int = 1):
    """موضوع‌هایی که دانشجو توشون غلط زیاد زده یا نمره‌ش پایینه رو برمی‌گردونه، به ترتیب بدترین."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            """
            SELECT qq.topic_tag AS topic,
                   COUNT(*) AS total_answers,
                   SUM(CASE WHEN qa.is_correct = 0 THEN 1
                            WHEN qa.score IS NOT NULL AND qa.score < 0.6 THEN 1
                            ELSE 0 END) AS weak_answers
            FROM quiz_answers qa
            JOIN quiz_questions qq ON qa.question_id = qq.id
            JOIN quiz_attempts qat ON qa.attempt_id = qat.id
            WHERE qat.user_id = ?
            GROUP BY qq.topic_tag
            HAVING total_answers >= ?
            ORDER BY (CAST(weak_answers AS FLOAT) / total_answers) DESC
            """,
            (user_id, min_attempts),
        ).fetchall()
        results = []
        for r in rows:
            ratio = r["weak_answers"] / r["total_answers"] if r["total_answers"] else 0
            results.append({
                "topic": r["topic"] or "نامشخص",
                "total_answers": r["total_answers"],
                "weak_answers": r["weak_answers"],
                "weakness_ratio": round(ratio, 2),
            })
        return results


def save_study_plan(user_id: int, course: str, days_remaining: int, daily_hours: float, plan: dict) -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO study_plans (user_id, course, days_remaining, daily_hours, plan_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, course, days_remaining, daily_hours, json.dumps(plan, ensure_ascii=False), time.time()),
        )
        conn.commit()
        return cur.lastrowid


def list_study_plans(user_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, course, days_remaining, daily_hours, plan_json, created_at FROM study_plans "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["plan"] = json.loads(d.pop("plan_json"))
            out.append(d)
        return out
