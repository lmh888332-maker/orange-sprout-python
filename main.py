from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import httpx
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "orange_sprout.db")))
LESSON_IDS = set(range(1, 19))
TASK_IDS = {f"t{i}" for i in range(1, 9)}
QUESTION_IDS = {f"q{i}" for i in range(1, 19)}

app = FastAPI(title="橙芽 Python 学园", version="7.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class RegisterIn(BaseModel):
    account: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z0-9_\u4e00-\u9fff]+$")
    password: str = Field(min_length=6, max_length=100)
    age_group: str = Field(default="other", max_length=20)

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
    correct: bool
    selected: int

class CodeSubmissionIn(BaseModel):
    task_id: str
    lesson_id: int
    passed: bool
    code: str = Field(max_length=30000)
    output: str = Field(default="", max_length=30000)

class AssistantIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    code: str = Field(default="", max_length=12000)
    task: str = Field(default="", max_length=200)
    lesson_id: int | None = None


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 210_000).hex()
    return salt, digest


def init_db() -> None:
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          nickname TEXT NOT NULL,
          username TEXT NOT NULL UNIQUE,
          age_group TEXT NOT NULL DEFAULT 'other',
          salt TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions(
          token TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
        """)

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
    with connect() as conn:
        row = conn.execute("SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token=?", (token,)).fetchone()
    if not row:
        raise HTTPException(401, "登录状态已失效，请重新登录")
    return row


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
def register(data: RegisterIn) -> dict:
    account = data.account.strip()
    salt, digest = hash_password(data.password)
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO users(nickname,username,age_group,salt,password_hash) VALUES (?,?,?,?,?)",
                (account, account, data.age_group, salt, digest),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "学习账号已经存在") from exc
    return {"ok": True, "account": account}

@app.post("/api/login")
def login(data: LoginIn) -> dict:
    with connect() as conn:
        account = data.account.strip()
        user = conn.execute("SELECT * FROM users WHERE username=?", (account,)).fetchone()
        if not user:
            raise HTTPException(401, "学习账号或密码错误")
        _, digest = hash_password(data.password, user["salt"])
        if not secrets.compare_digest(digest, user["password_hash"]):
            raise HTTPException(401, "学习账号或密码错误")
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sessions(token,user_id) VALUES (?,?)", (token,user["id"]))
    return {"token":token}

@app.post("/api/logout")
def logout(user: Annotated[sqlite3.Row, Depends(current_user)], authorization: Annotated[str | None, Header()] = None) -> dict:
    del user
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    return {"ok":True}

@app.get("/api/me")
def me(user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    return me_payload(user)

@app.post("/api/progress/{lesson_id}/step")
def lesson_step(lesson_id: int, data: LessonStepIn, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if lesson_id not in LESSON_IDS:
        raise HTTPException(404, "课程不存在")
    with connect() as conn:
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
        completed = {int(r["lesson_id"]) for r in conn.execute("SELECT lesson_id FROM lesson_progress WHERE user_id=?", (user["id"],)).fetchall()}
        if lesson_id > 1 and lesson_id-1 not in completed:
            raise HTTPException(400, "请先完成上一课")
        # 至少答对本课小测后才能完成
        qid = f"q{lesson_id}"
        passed = conn.execute("SELECT 1 FROM quiz_attempts WHERE user_id=? AND question_id=? AND correct=1 LIMIT 1", (user["id"],qid)).fetchone()
        if not passed:
            raise HTTPException(400, "请先答对本课小测")
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
    with connect() as conn:
        conn.execute("INSERT INTO quiz_attempts(user_id,question_id,module_id,correct,selected) VALUES (?,?,?,?,?)", (user["id"],data.question_id,data.module_id,int(data.correct),data.selected))
        mark_activity(conn, int(user["id"]))
    return me_payload(user)

@app.post("/api/code-submissions")
def code_submission(data: CodeSubmissionIn, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if data.task_id not in TASK_IDS or data.lesson_id not in LESSON_IDS:
        raise HTTPException(404, "代码任务不存在")
    with connect() as conn:
        conn.execute("INSERT INTO code_submissions(user_id,task_id,lesson_id,passed,code,output) VALUES (?,?,?,?,?,?)", (user["id"],data.task_id,data.lesson_id,int(data.passed),data.code,data.output))
        mark_activity(conn, int(user["id"]))
    return me_payload(user)


SAFE_RUNNER = r"""
import ast, builtins, sys
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
exec(compile(tree,'<student>','exec'),{'__builtins__':safe},{})
"""

@app.post('/api/run-code')
def run_code(data: RunCodeIn, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    del user
    try:
        proc=subprocess.run([sys.executable,'-I','-S','-c',SAFE_RUNNER],input=data.code,text=True,capture_output=True,timeout=3,env={'PYTHONIOENCODING':'utf-8'})
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(400,'代码运行时间过长，请检查循环停止条件') from exc
    if proc.returncode!=0:
        msg=(proc.stderr or proc.stdout).strip().splitlines()
        detail=msg[-1] if msg else '代码运行失败'
        raise HTTPException(400,detail)
    return {'output':proc.stdout.rstrip()}

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
    model = os.getenv("QWEN_MODEL", "qwen3.7-plus").strip() or "qwen3.7-plus"
    return api_key, base_url, model


@app.get("/api/assistant/status")
def assistant_status(user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    del user
    api_key, _, model = qwen_settings()
    return {"enabled": bool(api_key), "provider": "qwen" if api_key else "local", "model": model if api_key else "基础助教"}


@app.post("/api/assistant")
def assistant(data: AssistantIn, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    del user
    api_key, base_url, model = qwen_settings()
    if not api_key:
        return {"answer": local_assistant_answer(data.question), "provider": "local"}

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
        "X-DashScope-DataInspection": '{"input":"cip","output":"cip"}',
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
        safe_message = error_message[:300] or "百炼未返回错误说明"
        raise HTTPException(
            502,
            f"千问连接失败（HTTP {status} / {safe_code}）：{safe_message}",
        ) from exc
    except httpx.HTTPError as exc:
        print(
            f"Qwen network error: {type(exc).__name__}: {str(exc)[:500]}",
            file=sys.stderr,
            flush=True,
        )
        raise HTTPException(502, "千问助教网络连接失败，请稍后重试") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        print(
            f"Qwen response error: {type(exc).__name__}: {str(exc)[:500]}",
            file=sys.stderr,
            flush=True,
        )
        raise HTTPException(502, "千问助教返回格式异常，请稍后重试") from exc
