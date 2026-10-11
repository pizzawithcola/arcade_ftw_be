# arcade_ftw_be

Arcade FTW 后端骨架（FastAPI + Python 3.12）。与现有 `cyber_ai_festival_be` 完全隔离。

## 结构

```
app/
├── main.py            # FastAPI 入口（CORS + /health + WS 路由）
├── config.py          # pydantic-settings 环境变量
├── engine.py          # 状态机：idle / playing / paused / finished + current_index
├── flow.py            # 主线流程配置（改流程不改代码）
├── ws.py              # WebSocket 端点（占位）
└── question_types/    # 题型注册表（插件化）
    ├── base.py        # 题型协议
    ├── choice.py      # 选择题（示例实现）
    └── __init__.py    # QUESTION_TYPES 注册表
```

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8010
```

> 端口不要用 1688（现有系统占用），另起独立端口。

验证：`curl http://localhost:8010/health`

## 部署注意

- 镜像必须 `--platform linux/amd64`（Mac 是 arm64，否则 Fargate 起不来）。
- WS 是**进程内内存态**：单进程运行，禁止多 worker。
