# 橙芽 Python 学园 V7

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/lmh888332-maker/orange-sprout-python)

面向 8—14 岁零基础学习者的免费 Python 入门网站。当前版本包含注册登录、18 节课程、在线代码运行、实时学习进度、课程小测、代码挑战、徽章与经验值、深色模式，以及可选的通义千问 AI 编程助教。

## 本地运行

需要 Python 3.11 或更高版本。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Windows PowerShell 激活虚拟环境：

```powershell
.venv\Scripts\Activate.ps1
```

启动后打开 <http://127.0.0.1:8000>。

## 配置 AI 助教

在服务器环境变量中设置：

```text
DASHSCOPE_API_KEY=你的阿里云百炼APIKey
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.6-flash
```

不要把真实 API Key 写进前端、代码或提交到 GitHub。未配置 API Key 时，网站会自动使用基础规则助教。

## Render 部署

仓库已包含 `render.yaml`。在 Render 中选择 **New + → Blueprint**，连接本仓库并创建服务，然后在服务环境变量中填写 `DASHSCOPE_API_KEY`。

Blueprint 会创建 `orange-sprout-db` PostgreSQL，并自动把内部连接地址注入为 `DATABASE_URL`。正式站的账号、课程进度、小测和代码提交因此不会在普通重新部署后丢失；本地未设置 `DATABASE_URL` 时仍使用 SQLite。

Render 免费 PostgreSQL 只适合作业演示：容量为 1 GB，创建 30 天后到期且不提供备份。需要长期运营时，应在到期前升级数据库实例。

## 学习与安全规则

- 每课需要同时通过对应代码挑战和本课小测，才能完成并解锁下一课。
- 练习中心只抽取已经完成课程的题目，实验室不会开放尚未解锁的任务。
- 小测正确性和代码挑战结果均由服务端重新判定，浏览器提交的结果不会被直接采信。
- 实验室前两级提示提供思路，第三级显示完整正确答案；进入题目时不会预填答案。
- 实验室代码会自动保存在当前浏览器中，切换任务后仍可继续。
- 登录会话有效期为 30 天；登录、注册、代码运行和 AI 助教均有限流保护。

## 自动化测试

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

测试覆盖 18 道标准答案、服务端小测判定、课程解锁、代码挑战必做、防止写死输出和会话过期。

## 主要文件

- `main.py`：FastAPI 后端、登录与进度接口、代码运行器、AI 助教接口。
- `static/index.html`：网站前端。
- `render.yaml`：Render 部署配置。
- `Dockerfile`：容器部署配置。
- `.env.example`：环境变量示例，不包含真实密钥。

## 上线前提醒

当前版本适合作为个人低预算 Demo。面向儿童公开运营前，还需要补齐隐私政策、监护人同意机制、内容版权核验、接口限流，以及更严格的代码执行隔离。
