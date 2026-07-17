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
QWEN_MODEL=qwen-plus
```

不要把真实 API Key 写进前端、代码或提交到 GitHub。未配置 API Key 时，网站会自动使用基础规则助教。

## Render 部署

仓库已包含 `render.yaml`。在 Render 中选择 **New + → Blueprint**，连接本仓库并创建服务，然后在服务环境变量中填写 `DASHSCOPE_API_KEY`。

SQLite 数据库适合个人演示。免费云实例重启或重新部署时，本地数据库可能不会永久保留；正式长期运营建议改用 PostgreSQL。

## 主要文件

- `main.py`：FastAPI 后端、登录与进度接口、代码运行器、AI 助教接口。
- `static/index.html`：网站前端。
- `render.yaml`：Render 部署配置。
- `Dockerfile`：容器部署配置。
- `.env.example`：环境变量示例，不包含真实密钥。

## 上线前提醒

当前版本适合作为个人低预算 Demo。面向儿童公开运营前，还需要补齐隐私政策、监护人同意机制、内容版权核验、接口限流，以及更严格的代码执行隔离。
