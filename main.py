from __future__ import annotations

import ast
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import httpx
import sys
import threading
import time
import tokenize
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "orange_sprout.db")))
LESSON_IDS = set(range(1, 19))
TASK_IDS = {f"t{i}" for i in range(1, 19)}
QUESTION_IDS = {f"q{i}" for i in range(1, 19)}
SESSION_DAYS = 30

QUIZ_ANSWERS = {
    "q1": 0, "q2": 1, "q3": 1, "q4": 2, "q5": 0, "q6": 0,
    "q7": 2, "q8": 1, "q9": 1, "q10": 0, "q11": 1, "q12": 1,
    "q13": 1, "q14": 0, "q15": 0, "q16": 1, "q17": 0, "q18": 1,
}

# 题目顺序需要与前端课程一一对应。服务端重新执行和判定代码，
# 不再相信浏览器提交的 passed/output 字段。
TASK_RULES = {
    "t9": {
        "lesson": 1,
        "expected": "接收任务\n执行步骤\n得到结果",
        "min_calls": {"print": 3},
    },
    "t1": {
        "lesson": 2,
        "expected": "昵称：小橙\n年龄： 10\n兴趣：画画",
        "nodes": {"Assign": 3},
        "min_calls": {"print": 3},
    },
    "t10": {
        "lesson": 3,
        "expected": "我正在学习：Python",
        "nodes": {"Assign": 1},
        "min_calls": {"print": 1},
        "comment": True,
    },
    "t11": {
        "lesson": 4,
        "expected": "80",
        "nodes": {"Assign": 2, "Add": 1},
        "min_calls": {"print": 1},
    },
    "t2": {
        "lesson": 5,
        "expected": "21",
        "nodes": {"Assign": 3, "Mult": 2, "Add": 1},
        "min_calls": {"print": 1},
    },
    "t12": {
        "lesson": 6,
        "expected": "我爱Python\nPythonPython\n6\nP",
        "nodes": {"Assign": 1, "Add": 1, "Mult": 1, "Subscript": 1},
        "min_calls": {"print": 4, "len": 1},
    },
    "t13": {
        "lesson": 7,
        "expected": "True\nFalse\nTrue",
        "nodes": {"GtE": 1, "Eq": 1, "NotEq": 1},
        "min_calls": {"print": 3},
    },
    "t3": {
        "lesson": 8,
        "expected": "通关",
        "nodes": {"If": 1, "GtE": 1},
        "min_calls": {"print": 1},
    },
    "t14": {
        "lesson": 9,
        "expected": "舒适",
        "nodes": {"If": 2},
        "min_calls": {"print": 3},
    },
    "t4": {
        "lesson": 10,
        "expected": "7 x 1 = 7\n7 x 2 = 14\n7 x 3 = 21\n7 x 4 = 28\n7 x 5 = 35",
        "nodes": {"For": 1, "Mult": 1},
        "min_calls": {"range": 1, "print": 1},
    },
    "t15": {
        "lesson": 11,
        "expected": "3\n2\n1\n开始",
        "nodes": {"While": 1, "Sub": 1},
        "min_calls": {"print": 2},
    },
    "t5": {
        "lesson": 12,
        "expected": "10",
        "nodes": {"For": 1, "If": 1, "Mod": 1},
        "min_calls": {"range": 1, "print": 1},
    },
    "t6": {
        "lesson": 13,
        "expected": "学习\n练习\n复习",
        "nodes": {"List": 1, "For": 1},
        "min_calls": {"append": 1, "print": 1},
    },
    "t16": {
        "lesson": 14,
        "expected": "橙子侠\n2",
        "nodes": {"Dict": 1, "Subscript": 3},
        "min_calls": {"print": 2},
    },
    "t17": {
        "lesson": 15,
        "expected": "240\n80.0\n2",
        "nodes": {"List": 1, "For": 1, "If": 1},
        "min_calls": {"len": 1, "print": 3},
    },
    "t18": {
        "lesson": 16,
        "expected": "继续加油\n你正在进步",
        "nodes": {"FunctionDef": 1},
        "min_calls": {"cheer": 1, "print": 2},
    },
    "t7": {
        "lesson": 17,
        "expected": "12",
        "nodes": {"FunctionDef": 1, "Return": 1, "Mult": 1},
        "min_calls": {"rectangle_area": 1, "print": 1},
    },
    "t8": {
        "lesson": 18,
        "expected": "78.25\n3",
        "nodes": {"List": 1, "For": 1, "If": 1},
        "min_calls": {"len": 1, "print": 2},
    },
}

LESSON_TASKS = {rule["lesson"]: task_id for task_id, rule in TASK_RULES.items()}

_rate_events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_rate_lock = threading.Lock()

app = FastAPI(title="橙芽 Python 学园", version="7.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class RegisterIn(BaseModel):
    account: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z0-9_\u4e00-\u9fff]+$")
    password: str = Field(min_length=6, max_length=100)
    age_group: str = Field(default="other", pattern=r"^(8-10|11-12|13-14|other)$")
    guardian_consent: bool

class LoginIn(BaseModel):
    account: str
    password: str

class RunCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=20000)

class LessonStepIn(BaseModel):
    step: int = Field(ge=1, le=5)

class QuizAttemptIn(BaseModel):
    question_id: str
    module_id: int = 0
    correct: bool | None = None
    selected: int = Field(ge=0, le=3)

class CodeSubmissionIn(BaseModel):
    task_id: str
    lesson_id: int
    passed: bool | None = None
    code: str = Field(max_length=30000)
    output: str = Field(default="", max_length=30000)

class AssistantIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    code: str = Field(default="", max_length=12000)
    task: str = Field(default="", max_length=200)
    lesson_id: int | None = None


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def enforce_rate_limit(scope: str, key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    bucket_key = (scope, key)
    with _rate_lock:
        events = _rate_events[bucket_key]
        cutoff = now - window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            retry_after = max(1, int(window_seconds - (now - events[0])))
            raise HTTPException(
                429,
                f"操作太频繁，请在 {retry_after} 秒后再试",
                headers={"Retry-After": str(retry_after)},
            )
        events.append(now)


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def session_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 210_000).hex()
    return salt, digest


def init_db() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          nickname TEXT NOT NULL,
          username TEXT NOT NULL UNIQUE,
          age_group TEXT NOT NULL DEFAULT 'other',
          guardian_consent_at TEXT,
          salt TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions(
          token TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          expires_at TEXT,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS lesson_progress(
          user_id INTEGER NOT NULL,
          lesson_id INTEGER NOT NULL,
          completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(user_id, lesson_id),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS lesson_steps(
          user_id INTEGER NOT NULL,
          lesson_id INTEGER NOT NULL,
          step INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(user_id, lesson_id),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS quiz_attempts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          question_id TEXT NOT NULL,
          module_id INTEGER NOT NULL DEFAULT 0,
          correct INTEGER NOT NULL,
          selected INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS code_submissions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          task_id TEXT NOT NULL,
          lesson_id INTEGER NOT NULL,
          passed INTEGER NOT NULL,
          code TEXT NOT NULL,
          output TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS activity_days(
          user_id INTEGER NOT NULL,
          activity_date TEXT NOT NULL,
          PRIMARY KEY(user_id, activity_date),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS assistant_usage(
          user_id INTEGER NOT NULL,
          usage_date TEXT NOT NULL,
          request_count INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(user_id, usage_date),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "guardian_consent_at" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN guardian_consent_at TEXT")
        session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "expires_at" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN expires_at TEXT")
        conn.execute(
            """
            UPDATE sessions
            SET expires_at=datetime(created_at, ?)
            WHERE expires_at IS NULL
            """,
            (f"+{SESSION_DAYS} days",),
        )

@app.on_event("startup")
def startup() -> None:
    init_db()

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "orange-sprout-v7"}


def current_user(authorization: Annotated[str | None, Header()] = None) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "请先登录")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "请先登录")
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at IS NULL OR expires_at<=CURRENT_TIMESTAMP")
        row = conn.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id=sessions.user_id
            WHERE sessions.token=? AND sessions.expires_at>CURRENT_TIMESTAMP
            """,
            (session_key(token),),
        ).fetchone()
    if not row:
        raise HTTPException(401, "登录状态已失效，请重新登录")
    return row


def completed_lessons(conn: sqlite3.Connection, user_id: int) -> set[int]:
    return {
        int(row["lesson_id"])
        for row in conn.execute(
            "SELECT lesson_id FROM lesson_progress WHERE user_id=?",
            (user_id,),
        ).fetchall()
    }


def require_lesson_unlocked(conn: sqlite3.Connection, user_id: int, lesson_id: int) -> None:
    completed = completed_lessons(conn, user_id)
    if lesson_id != 1 and lesson_id not in completed and lesson_id - 1 not in completed:
        raise HTTPException(400, "请先完成上一课")


def mark_activity(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO activity_days(user_id,activity_date) VALUES (?,?)", (user_id, date.today().isoformat()))


def streak_for(conn: sqlite3.Connection, user_id: int) -> int:
    days = {r["activity_date"] for r in conn.execute("SELECT activity_date FROM activity_days WHERE user_id=?", (user_id,)).fetchall()}
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    streak = 0
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def me_payload(user: sqlite3.Row) -> dict:
    with connect() as conn:
        completed = [int(r["lesson_id"]) for r in conn.execute("SELECT lesson_id FROM lesson_progress WHERE user_id=? ORDER BY lesson_id", (user["id"],)).fetchall()]
        quiz_done = [r["question_id"] for r in conn.execute("SELECT DISTINCT question_id FROM quiz_attempts WHERE user_id=? AND correct=1", (user["id"],)).fetchall()]
        wrong = [r["question_id"] for r in conn.execute("""
          SELECT qa.question_id FROM quiz_attempts qa
          JOIN (SELECT question_id, MAX(id) id FROM quiz_attempts WHERE user_id=? GROUP BY question_id) last ON last.id=qa.id
          WHERE qa.correct=0
        """, (user["id"],)).fetchall()]
        code_passed = [r["task_id"] for r in conn.execute("SELECT DISTINCT task_id FROM code_submissions WHERE user_id=? AND passed=1", (user["id"],)).fetchall()]
        lesson_steps = {str(r["lesson_id"]): int(r["step"]) for r in conn.execute("SELECT lesson_id,step FROM lesson_steps WHERE user_id=?", (user["id"],)).fetchall()}
        for lesson_id in completed:
            lesson_steps[str(lesson_id)] = 5
        streak = streak_for(conn, int(user["id"]))
    xp = len(completed)*20 + len(quiz_done)*5 + len(code_passed)*10
    return {"user":{"id":int(user["id"]),"nickname":user["nickname"],"username":user["username"],"ageGroup":user["age_group"]},"completed":completed,"quizDone":quiz_done,"wrong":wrong,"codePassed":code_passed,"lessonSteps":lesson_steps,"streak":streak,"xp":xp}

@app.post("/api/register")
def register(data: RegisterIn, request: Request) -> dict:
    enforce_rate_limit("register", client_key(request), 5, 600)
    if not data.guardian_consent:
        raise HTTPException(400, "请先确认已获得监护人同意")
    account = data.account.strip()
    salt, digest = hash_password(data.password)
    try:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO users(
                  nickname,username,age_group,guardian_consent_at,salt,password_hash
                ) VALUES (?,?,?,CURRENT_TIMESTAMP,?,?)
                """,
                (account, account, data.age_group, salt, digest),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "学习账号已经存在") from exc
    return {"ok": True, "account": account}

@app.post("/api/login")
def login(data: LoginIn, request: Request) -> dict:
    enforce_rate_limit("login", client_key(request), 10, 600)
    with connect() as conn:
        account = data.account.strip()
        user = conn.execute("SELECT * FROM users WHERE username=?", (account,)).fetchone()
        if not user:
            raise HTTPException(401, "学习账号或密码错误")
        _, digest = hash_password(data.password, user["salt"])
        if not secrets.compare_digest(digest, user["password_hash"]):
            raise HTTPException(401, "学习账号或密码错误")
        token = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM sessions WHERE expires_at IS NULL OR expires_at<=CURRENT_TIMESTAMP")
        expires_at = (datetime.now(UTC) + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO sessions(token,user_id,expires_at) VALUES (?,?,?)",
            (session_key(token), user["id"], expires_at),
        )
    return {"token":token}

@app.post("/api/logout")
def logout(user: Annotated[sqlite3.Row, Depends(current_user)], authorization: Annotated[str | None, Header()] = None) -> dict:
    del user
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (session_key(token),))
    return {"ok":True}

@app.get("/api/me")
def me(user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    return me_payload(user)

@app.post("/api/progress/{lesson_id}/step")
def lesson_step(lesson_id: int, data: LessonStepIn, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if lesson_id not in LESSON_IDS:
        raise HTTPException(404, "课程不存在")
    with connect() as conn:
        require_lesson_unlocked(conn, int(user["id"]), lesson_id)
        old = conn.execute("SELECT step FROM lesson_steps WHERE user_id=? AND lesson_id=?", (user["id"], lesson_id)).fetchone()
        step = max(int(old["step"]) if old else 0, int(data.step))
        conn.execute("""
          INSERT INTO lesson_steps(user_id,lesson_id,step,updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)
          ON CONFLICT(user_id,lesson_id) DO UPDATE SET step=MAX(lesson_steps.step,excluded.step),updated_at=CURRENT_TIMESTAMP
        """, (user["id"], lesson_id, step))
        mark_activity(conn, int(user["id"]))
    return me_payload(user)

@app.post("/api/progress/{lesson_id}/complete")
def complete(lesson_id: int, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if lesson_id not in LESSON_IDS:
        raise HTTPException(404, "课程不存在")
    with connect() as conn:
        require_lesson_unlocked(conn, int(user["id"]), lesson_id)
        qid = f"q{lesson_id}"
        quiz_passed = conn.execute(
            """
            SELECT 1 FROM quiz_attempts
            WHERE user_id=? AND question_id=? AND correct=1
            LIMIT 1
            """,
            (user["id"], qid),
        ).fetchone()
        if not quiz_passed:
            raise HTTPException(400, "请先答对本课小测")
        task_id = LESSON_TASKS[lesson_id]
        code_passed = conn.execute(
            """
            SELECT 1 FROM code_submissions
            WHERE user_id=? AND task_id=? AND lesson_id=? AND passed=1
            LIMIT 1
            """,
            (user["id"], task_id, lesson_id),
        ).fetchone()
        if not code_passed:
            raise HTTPException(400, "请先通过本课代码挑战")
        conn.execute("INSERT OR IGNORE INTO lesson_progress(user_id,lesson_id) VALUES (?,?)", (user["id"],lesson_id))
        conn.execute("""
          INSERT INTO lesson_steps(user_id,lesson_id,step,updated_at) VALUES (?,?,5,CURRENT_TIMESTAMP)
          ON CONFLICT(user_id,lesson_id) DO UPDATE SET step=5,updated_at=CURRENT_TIMESTAMP
        """, (user["id"], lesson_id))
        mark_activity(conn, int(user["id"]))
    return me_payload(user)

@app.post("/api/quiz-attempts")
def quiz_attempt(data: QuizAttemptIn, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if data.question_id not in QUESTION_IDS:
        raise HTTPException(404, "题目不存在")
    lesson_id = int(data.question_id.removeprefix("q"))
    correct = data.selected == QUIZ_ANSWERS[data.question_id]
    module_id = (lesson_id - 1) // 3 + 1
    with connect() as conn:
        require_lesson_unlocked(conn, int(user["id"]), lesson_id)
        conn.execute(
            """
            INSERT INTO quiz_attempts(
              user_id,question_id,module_id,correct,selected
            ) VALUES (?,?,?,?,?)
            """,
            (user["id"], data.question_id, module_id, int(correct), data.selected),
        )
        mark_activity(conn, int(user["id"]))
    return {**me_payload(user), "attempt": {"correct": correct}}

@app.post("/api/code-submissions")
def code_submission(
    data: CodeSubmissionIn,
    user: Annotated[sqlite3.Row, Depends(current_user)],
) -> dict:
    if data.task_id not in TASK_IDS or data.lesson_id not in LESSON_IDS:
        raise HTTPException(404, "代码任务不存在")
    rule = TASK_RULES[data.task_id]
    if int(rule["lesson"]) != data.lesson_id:
        raise HTTPException(400, "代码任务与课程不匹配")
    enforce_rate_limit("code-submit", str(user["id"]), 30, 60)
    with connect() as conn:
        require_lesson_unlocked(conn, int(user["id"]), data.lesson_id)
    structure_feedback = validate_task_structure(data.task_id, data.code)
    output = execute_student_code(data.code)
    passed = not structure_feedback and output.strip() == str(rule["expected"]).strip()
    if passed:
        feedback = "代码结构和运行结果都正确。"
    elif structure_feedback:
        feedback = structure_feedback
    else:
        feedback = output_difference_feedback(str(rule["expected"]), output)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO code_submissions(
              user_id,task_id,lesson_id,passed,code,output
            ) VALUES (?,?,?,?,?,?)
            """,
            (user["id"], data.task_id, data.lesson_id, int(passed), data.code, output),
        )
        mark_activity(conn, int(user["id"]))
    return {
        **me_payload(user),
        "validation": {
            "passed": passed,
            "output": output,
            "feedback": feedback,
        },
    }


SAFE_RUNNER = r"""
import ast, builtins, sys
try:
 import resource
 memory_limit=128*1024*1024
 resource.setrlimit(resource.RLIMIT_AS,(memory_limit,memory_limit))
 resource.setrlimit(resource.RLIMIT_CPU,(2,2))
 resource.setrlimit(resource.RLIMIT_NOFILE,(16,16))
except Exception:
 pass
code=sys.stdin.read()
allowed_nodes=(ast.Module,ast.Expr,ast.Assign,ast.AugAssign,ast.Name,ast.Load,ast.Store,ast.Constant,ast.List,ast.Tuple,ast.Dict,ast.Set,ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,ast.If,ast.For,ast.While,ast.FunctionDef,ast.Return,ast.Call,ast.keyword,ast.arguments,ast.arg,ast.Subscript,ast.Slice,ast.IfExp,ast.Pass,ast.Break,ast.Continue,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod,ast.Pow,ast.USub,ast.UAdd,ast.Not,ast.And,ast.Or,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,ast.Attribute)
allowed_names={'print','range','len','str','int','float','bool','list','dict','sum','min','max','abs','round','enumerate','zip'}
allowed_attrs={'append','pop','get','keys','values','items','upper','lower','strip','split','join','replace','count','index'}
class V(ast.NodeVisitor):
 def generic_visit(self,node):
  if not isinstance(node,allowed_nodes): raise ValueError('不允许使用：'+type(node).__name__)
  super().generic_visit(node)
 def visit_Import(self,node): raise ValueError('课程运行器不允许导入模块')
 def visit_ImportFrom(self,node): raise ValueError('课程运行器不允许导入模块')
 def visit_Attribute(self,node):
  if node.attr not in allowed_attrs: raise ValueError('不允许访问属性：'+node.attr)
  self.generic_visit(node)
 def visit_Call(self,node):
  if isinstance(node.func,ast.Name) and node.func.id.startswith('__'): raise ValueError('不允许调用该函数')
  self.generic_visit(node)
tree=ast.parse(code,'<student>','exec');V().visit(tree)
safe={k:getattr(builtins,k) for k in allowed_names}
written=0
def limited_print(*args,**kwargs):
 global written
 text=' '.join(str(value) for value in args)
 end=kwargs.get('end','\n')
 written+=len(text)+len(end)
 if written>20000: raise ValueError('输出内容过多，请减少循环次数')
 builtins.print(*args,**kwargs)
safe['print']=limited_print
exec(compile(tree,'<student>','exec'),{'__builtins__':safe},{})
"""


NODE_LABELS = {
    "Assign": "变量赋值",
    "Add": "加法或字符串拼接",
    "Sub": "减法更新",
    "Mult": "乘法",
    "Mod": "取余运算 %",
    "Subscript": "索引或字典取值",
    "List": "列表",
    "Dict": "字典",
    "Compare": "比较运算",
    "GtE": "大于等于比较",
    "Eq": "相等比较 ==",
    "NotEq": "不等于比较 !=",
    "If": "if 条件判断",
    "For": "for 循环",
    "While": "while 循环",
    "FunctionDef": "函数定义 def",
    "Return": "return 返回值",
}


def call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def validate_task_structure(task_id: str, code: str) -> str:
    rule = TASK_RULES[task_id]
    try:
        tree = ast.parse(code, "<student>", "exec")
    except SyntaxError:
        return "代码存在语法错误，请先根据运行错误修改。"
    node_counts: dict[str, int] = defaultdict(int)
    call_counts: dict[str, int] = defaultdict(int)
    for node in ast.walk(tree):
        node_counts[type(node).__name__] += 1
        if isinstance(node, ast.Call):
            call_counts[call_name(node)] += 1
    for node_name, minimum in rule.get("nodes", {}).items():
        if node_counts[node_name] < minimum:
            label = NODE_LABELS.get(node_name, node_name)
            return f"输出可能正确，但本题要求真正使用{label}，请按本课知识重新完成。"
    for name, minimum in rule.get("min_calls", {}).items():
        if call_counts[name] < minimum:
            suffix = f"至少 {minimum} 次" if minimum > 1 else ""
            return f"本题需要调用 {name}(){suffix}，不能只写死最终输出。"
    if rule.get("comment"):
        try:
            comments = [
                token
                for token in tokenize.generate_tokens(StringIO(code).readline)
                if token.type == tokenize.COMMENT
            ]
        except (tokenize.TokenError, IndentationError):
            comments = []
        if not comments:
            return "本题还需要写一行以 # 开头的说明注释。"
    return ""


def output_difference_feedback(expected: str, actual: str) -> str:
    expected_lines = expected.strip().splitlines()
    actual_lines = actual.strip().splitlines() if actual.strip() else []
    if len(actual_lines) != len(expected_lines):
        return (
            f"运行成功，但任务需要输出 {len(expected_lines)} 行，"
            f"当前输出了 {len(actual_lines)} 行。请检查遗漏、顺序和多余输出。"
        )
    for index, (wanted, got) in enumerate(zip(expected_lines, actual_lines), start=1):
        if wanted != got:
            return f"第 {index} 行与任务要求不一致。你当前输出的是：{got or '（空行）'}"
    return "运行结果与任务要求不一致，请检查空格、标点和输出顺序。"


def execute_student_code(code: str) -> str:
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-c", SAFE_RUNNER],
            input=code,
            text=True,
            capture_output=True,
            timeout=3,
            env={"PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(400, "代码运行时间过长，请检查循环停止条件") from exc
    if proc.returncode != 0:
        messages = (proc.stderr or proc.stdout).strip().splitlines()
        detail = messages[-1] if messages else "代码运行失败"
        if "MemoryError" in detail:
            detail = "程序使用的内存过多，请减小列表或循环规模"
        raise HTTPException(400, detail)
    return proc.stdout.rstrip()


@app.post('/api/run-code')
def run_code(data: RunCodeIn, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    enforce_rate_limit("run-code", str(user["id"]), 30, 60)
    return {"output": execute_student_code(data.code)}

def local_assistant_answer(question: str) -> str:
    q = question.lower()
    if "indent" in q or "缩进" in question:
        return "先检查 if、for、while 或 def 行末是否有冒号，再确认下一行统一向右缩进4个空格。"
    if "syntax" in q or "语法" in question:
        return "语法错误常见于括号、引号或冒号没有成对出现。请从报错行和上一行开始检查。"
    if "nameerror" in q or "未定义" in question or "变量" in question:
        return "确认变量在使用前已经赋值，并检查拼写和大小写是否完全一致。"
    if "while" in q or "循环" in question:
        return "写清楚循环起点、每轮操作和停止条件；while 循环还要确保控制变量每轮变化。"
    if "return" in q or "函数" in question:
        return "把函数拆成参数、处理和返回结果三部分，先用一个简单输入测试返回值。"
    return "建议按三步排查：读最后一行报错类型；检查报错行和上一行；把代码缩小到最少几行后再次运行。"


def qwen_settings() -> tuple[str, str, str]:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    configured_model = os.getenv("QWEN_MODEL", "").strip()
    model = "qwen3.6-flash" if configured_model in {"", "qwen-plus", "qwen3.7-plus"} else configured_model
    return api_key, base_url, model


def qwen_key_type(api_key: str) -> str:
    if api_key.startswith("sk-sp-"):
        return "coding-plan"
    if api_key.startswith("sk-"):
        return "standard"
    return "unknown" if api_key else "missing"


def qwen_region(base_url: str) -> str:
    host = base_url.lower()
    if "dashscope-intl.aliyuncs.com" in host:
        return "singapore"
    if "dashscope.aliyuncs.com" in host:
        return "beijing"
    return "custom"


def consume_ai_quota(user_id: int, daily_limit: int = 40) -> None:
    usage_date = date.today().isoformat()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO assistant_usage(user_id,usage_date,request_count)
            VALUES (?,?,1)
            ON CONFLICT(user_id,usage_date) DO UPDATE
            SET request_count=assistant_usage.request_count+1
            WHERE assistant_usage.request_count<?
            """,
            (user_id, usage_date, daily_limit),
        )
        if cursor.rowcount == 0:
            raise HTTPException(429, f"今天的智能助教额度已用完（每日 {daily_limit} 次）")


@app.get("/api/assistant/status")
def assistant_status() -> dict:
    api_key, base_url, model = qwen_settings()
    key_type = qwen_key_type(api_key)
    compatible = bool(api_key) and key_type != "coding-plan"
    note = ""
    if key_type == "coding-plan":
        note = "Coding Plan Key 不能用于通用百炼 API，请改用按量付费标准 API Key"
    elif not api_key:
        note = "尚未配置百炼 API Key"
    return {
        "enabled": compatible,
        "configured": bool(api_key),
        "provider": "qwen" if compatible else "local",
        "model": model if compatible else "基础助教",
        "keyType": key_type,
        "region": qwen_region(base_url),
        "note": note,
    }


@app.post("/api/assistant")
def assistant(data: AssistantIn, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    enforce_rate_limit("assistant", str(user["id"]), 8, 60)
    api_key, base_url, model = qwen_settings()
    if not api_key:
        return {"answer": local_assistant_answer(data.question), "provider": "local", "model": "基础助教"}
    if qwen_key_type(api_key) == "coding-plan":
        return {
            "answer": local_assistant_answer(data.question),
            "provider": "local-fallback",
            "model": "基础助教",
            "warning": "智能助教暂时不可用，已为你切换到基础助教。",
            "diagnostic": "coding-plan-key-incompatible",
        }
    consume_ai_quota(int(user["id"]))

    context_parts = []
    if data.task:
        context_parts.append(f"当前代码任务：{data.task}")
    if data.lesson_id:
        context_parts.append(f"对应课程：第{data.lesson_id}课")
    if data.code.strip():
        context_parts.append("学生当前代码：\n```python\n" + data.code.strip()[:8000] + "\n```")
    context = "\n\n".join(context_parts)
    user_message = data.question if not context else context + "\n\n学生的问题：" + data.question
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是橙芽Python学园的儿童编程助教，服务8到14岁零基础学习者。"
                    "使用简洁、友善、鼓励性的中文。优先给分步提示，先解释错误原因，再给相似示例；"
                    "除非学生明确要求，不直接替他完成整道作业。代码必须是安全、基础、可运行的Python。"
                    "不要询问真实姓名、学校、地址、电话等个人信息。"
                ),
            },
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": 900,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=35.0) as client:
            response = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        answer = body["choices"][0]["message"]["content"].strip()
        if not answer:
            raise ValueError("千问返回了空内容")
        return {"answer": answer, "provider": "qwen", "model": model}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        request_id = exc.response.headers.get("x-request-id", "")
        try:
            error_body = exc.response.json()
            error_code = str(error_body.get("code", "")).strip()
            error_message = str(error_body.get("message", "")).strip()
        except (ValueError, TypeError, AttributeError):
            error_code = ""
            error_message = exc.response.text.strip()[:500]
        print(
            "Qwen upstream error:"
            f" status={status} code={error_code or '-'}"
            f" request_id={request_id or '-'}"
            f" message={error_message[:500] or '-'}",
            file=sys.stderr,
            flush=True,
        )
        safe_code = error_code or "UpstreamError"
        warning = "智能助教暂时不可用，已为你切换到基础助教。"
        return {
            "answer": local_assistant_answer(data.question),
            "provider": "local-fallback",
            "model": "基础助教",
            "warning": warning,
            "diagnostic": f"HTTP {status}/{safe_code}",
        }
    except httpx.HTTPError as exc:
        print(
            f"Qwen network error: {type(exc).__name__}: {str(exc)[:500]}",
            file=sys.stderr,
            flush=True,
        )
        return {
            "answer": local_assistant_answer(data.question),
            "provider": "local-fallback",
            "model": "基础助教",
            "warning": "智能助教暂时不可用，已为你切换到基础助教。",
            "diagnostic": "qwen-network-error",
        }
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        print(
            f"Qwen response error: {type(exc).__name__}: {str(exc)[:500]}",
            file=sys.stderr,
            flush=True,
        )
        return {
            "answer": local_assistant_answer(data.question),
            "provider": "local-fallback",
            "model": "基础助教",
            "warning": "智能助教暂时不可用，已为你切换到基础助教。",
            "diagnostic": "qwen-response-error",
        }
