from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path

from fastapi.testclient import TestClient

import main


SOLUTIONS = {
    "t9": 'print("接收任务")\nprint("执行步骤")\nprint("得到结果")',
    "t1": (
        'name = "小橙"\nage = 10\nhobby = "画画"\n'
        'print("昵称：" + name)\nprint("年龄：", age)\nprint("兴趣：" + hobby)'
    ),
    "t10": '# subject 保存课程名称\nsubject = "Python"\nprint("我正在学习：" + subject)',
    "t11": "score = 60\nscore = score + 20\nprint(score)",
    "t2": (
        "pen_price = 3\nbook_price = 6\n"
        "total = pen_price * 3 + book_price * 2\nprint(total)"
    ),
    "t12": (
        'word = "Python"\nprint("我爱" + word)\nprint(word * 2)\n'
        "print(len(word))\nprint(word[0])"
    ),
    "t13": (
        "score = 85\nprint(score >= 60)\n"
        "print(score == 100)\nprint(score != 0)"
    ),
    "t3": 'score = 75\nif score >= 60:\n    print("通关")',
    "t14": (
        "temperature = 25\nif temperature >= 30:\n"
        '    print("炎热")\nelif temperature >= 20:\n'
        '    print("舒适")\nelse:\n    print("偏凉")'
    ),
    "t4": 'for number in range(1, 6):\n    print(7, "x", number, "=", 7 * number)',
    "t15": (
        'count = 3\nwhile count >= 1:\n    print(count)\n'
        '    count = count - 1\nprint("开始")'
    ),
    "t5": (
        "count = 0\nfor number in range(1, 21):\n"
        "    if number % 2 == 0:\n        count = count + 1\nprint(count)"
    ),
    "t6": (
        'tasks = ["学习", "练习"]\ntasks.append("复习")\n'
        "for task in tasks:\n    print(task)"
    ),
    "t16": (
        'hero = {"name": "橙子侠", "level": 1}\nhero["level"] = 2\n'
        'print(hero["name"])\nprint(hero["level"])'
    ),
    "t17": (
        "scores = [90, 80, 70]\ntotal = 0\nexcellent = 0\n"
        "for score in scores:\n    total = total + score\n"
        "    if score >= 80:\n        excellent = excellent + 1\n"
        "print(total)\nprint(total / len(scores))\nprint(excellent)"
    ),
    "t18": (
        'def cheer():\n    print("继续加油")\n'
        '    print("你正在进步")\n\ncheer()'
    ),
    "t7": (
        "def rectangle_area(width, height):\n"
        "    return width * height\n\n"
        "result = rectangle_area(4, 3)\nprint(result)"
    ),
    "t8": (
        "scores = [88, 76, 69, 80]\ntotal = 0\npassed = 0\n"
        "for score in scores:\n    total = total + score\n"
        "    if score >= 70:\n        passed = passed + 1\n"
        "print(total / len(scores))\nprint(passed)"
    ),
}


class OrangeSproutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_url = main.DATABASE_URL
        main.DATABASE_URL = ""
        main.DB_PATH = Path(self.temp_dir.name) / "test.db"
        main._rate_events.clear()
        main.init_db()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        main.DATABASE_URL = self.original_database_url
        self.temp_dir.cleanup()

    def register_and_login(self, account: str = "测试学生") -> dict[str, str]:
        register = self.client.post(
            "/api/register",
            json={
                "account": account,
                "password": "orange123",
                "age_group": "8-10",
                "guardian_consent": True,
            },
        )
        self.assertEqual(register.status_code, 200, register.text)
        login = self.client.post(
            "/api/login",
            json={"account": account, "password": "orange123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_all_published_solutions_run_and_pass_structure(self) -> None:
        self.assertEqual(set(SOLUTIONS), set(main.TASK_RULES))
        for task_id, code in SOLUTIONS.items():
            with self.subTest(task_id=task_id):
                self.assertEqual(main.validate_task_structure(task_id, code), "")
                output = main.execute_student_code(code)
                self.assertEqual(output.strip(), main.TASK_RULES[task_id]["expected"].strip())

    def test_frontend_last_hints_contain_the_tested_solutions(self) -> None:
        html = (Path(main.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
        block = re.search(
            r"const TASK_SOLUTIONS=\{(?P<body>.*?)\n\};\nconst \$=",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(block)
        published = {
            task_id: code
            for task_id, code in re.findall(
                r"\s*(t\d+):`([^`]*)`",
                block.group("body"),
                re.DOTALL,
            )
        }
        self.assertEqual(set(published), set(SOLUTIONS))
        for task_id, code in published.items():
            with self.subTest(frontend_task_id=task_id):
                self.assertEqual(main.validate_task_structure(task_id, code), "")
                output = main.execute_student_code(code)
                self.assertEqual(output.strip(), main.TASK_RULES[task_id]["expected"].strip())
        self.assertIn("<strong>正确答案</strong>", html)
        self.assertIn('class="answer-code"', html)
        self.assertIn(".hint-solution pre.answer-code code{display:block", html)
        self.assertIn("background:transparent;color:inherit;font:inherit", html)
        self.assertNotIn(
            "${state.hintIndex===total?'正确答案':`提示",
            html,
        )

    def test_database_configuration_supports_postgres_and_local_sqlite(self) -> None:
        self.assertEqual(main.database_backend(), "sqlite")
        self.assertEqual(
            main.adapt_sql("SELECT * FROM users WHERE username=?", "postgresql"),
            "SELECT * FROM users WHERE username=%s",
        )
        self.assertIn("BIGSERIAL PRIMARY KEY", main.POSTGRES_SCHEMA)
        self.assertIn("TIMESTAMPTZ", main.POSTGRES_SCHEMA)
        render_yaml = (Path(main.__file__).parent / "render.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("fromDatabase:", render_yaml)
        self.assertIn("name: orange-sprout-db", render_yaml)
        self.assertIn("databases:", render_yaml)

    def test_account_survives_database_reinitialization(self) -> None:
        self.register_and_login("持久化学生")
        main.init_db()
        login = self.client.post(
            "/api/login",
            json={"account": "持久化学生", "password": "orange123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertTrue(login.json()["token"])

    def test_health_reports_database_backend(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "sqlite")

    def test_guardian_consent_is_required(self) -> None:
        response = self.client.post(
            "/api/register",
            json={
                "account": "不同意",
                "password": "orange123",
                "age_group": "8-10",
                "guardian_consent": False,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_server_does_not_trust_quiz_correct_flag(self) -> None:
        headers = self.register_and_login()
        response = self.client.post(
            "/api/quiz-attempts",
            headers=headers,
            json={"question_id": "q1", "selected": 1, "correct": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["attempt"]["correct"])
        self.assertNotIn("q1", response.json()["quizDone"])

    def test_lesson_requires_code_and_quiz(self) -> None:
        headers = self.register_and_login()
        quiz = self.client.post(
            "/api/quiz-attempts",
            headers=headers,
            json={"question_id": "q1", "selected": 0},
        )
        self.assertTrue(quiz.json()["attempt"]["correct"])
        blocked = self.client.post("/api/progress/1/complete", headers=headers)
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("代码挑战", blocked.json()["detail"])

        code = self.client.post(
            "/api/code-submissions",
            headers=headers,
            json={"task_id": "t9", "lesson_id": 1, "code": SOLUTIONS["t9"]},
        )
        self.assertEqual(code.status_code, 200, code.text)
        self.assertTrue(code.json()["validation"]["passed"])

        complete = self.client.post("/api/progress/1/complete", headers=headers)
        self.assertEqual(complete.status_code, 200, complete.text)
        self.assertIn(1, complete.json()["completed"])

    def test_hardcoded_output_does_not_pass_code_challenge(self) -> None:
        headers = self.register_and_login()
        response = self.client.post(
            "/api/code-submissions",
            headers=headers,
            json={
                "task_id": "t9",
                "lesson_id": 1,
                "passed": True,
                "output": main.TASK_RULES["t9"]["expected"],
                "code": 'print("接收任务\\n执行步骤\\n得到结果")',
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["validation"]["passed"])
        self.assertIn("print()", response.json()["validation"]["feedback"])

    def test_locked_lesson_cannot_submit_quiz_or_code(self) -> None:
        headers = self.register_and_login()
        quiz = self.client.post(
            "/api/quiz-attempts",
            headers=headers,
            json={"question_id": "q2", "selected": 1},
        )
        self.assertEqual(quiz.status_code, 400)
        code = self.client.post(
            "/api/code-submissions",
            headers=headers,
            json={"task_id": "t1", "lesson_id": 2, "code": SOLUTIONS["t1"]},
        )
        self.assertEqual(code.status_code, 400)

    def test_complete_learning_path_in_order(self) -> None:
        headers = self.register_and_login()
        latest = {}
        for lesson_id in range(1, 19):
            task_id = main.LESSON_TASKS[lesson_id]
            code = self.client.post(
                "/api/code-submissions",
                headers=headers,
                json={
                    "task_id": task_id,
                    "lesson_id": lesson_id,
                    "code": SOLUTIONS[task_id],
                },
            )
            self.assertEqual(code.status_code, 200, code.text)
            self.assertTrue(code.json()["validation"]["passed"])
            quiz = self.client.post(
                "/api/quiz-attempts",
                headers=headers,
                json={
                    "question_id": f"q{lesson_id}",
                    "selected": main.QUIZ_ANSWERS[f"q{lesson_id}"],
                },
            )
            self.assertEqual(quiz.status_code, 200, quiz.text)
            self.assertTrue(quiz.json()["attempt"]["correct"])
            complete = self.client.post(
                f"/api/progress/{lesson_id}/complete",
                headers=headers,
            )
            self.assertEqual(complete.status_code, 200, complete.text)
            latest = complete.json()
        self.assertEqual(latest["completed"], list(range(1, 19)))
        self.assertEqual(len(latest["codePassed"]), 18)
        self.assertEqual(len(latest["quizDone"]), 18)

    def test_expired_session_is_rejected(self) -> None:
        headers = self.register_and_login()
        with main.connect() as conn:
            conn.execute("UPDATE sessions SET expires_at='2000-01-01 00:00:00'")
        response = self.client.get("/api/me", headers=headers)
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
