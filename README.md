# arcade_ftw_be

Arcade FTW 后端骨架（FastAPI + Python 3.12）。与现有 `cyber_ai_festival_be` 完全隔离。

> 跨项目文档（系统手册 / WS 协议 / 题型契约 / AWS）见 [`../arcade_ftw_docs`](../arcade_ftw_docs)。

## 结构

```
.github/workflows/
└── deploy.yml         # CI：构建 → ECR → ECS → 验证 /health
infra/
└── cloudformation.yml # 后端基础设施（ECR / ALB / ECS / IAM / 日志）
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
uvicorn app.main:app --reload --port 5071
```

> 端口不要用 1688（旧系统占用），本项目后端用 **5071**、前端用 **5070**。

验证：`curl http://localhost:5071/health`

## 部署

### 基础设施（CloudFormation）

[`infra/cloudformation.yml`](infra/cloudformation.yml) —— **只覆盖后端**资源：ECR、ALB + Target Group、ECS cluster/task/service、安全组、IAM role、日志组。

不包含（刻意）：VPC/子网（与旧系统共享，作为参数传入）、RDS（placeholder 阶段）、S3 + CloudFront（前端 repo 的范围）。

```bash
aws cloudformation deploy \
  --template-file infra/cloudformation.yml \
  --stack-name arcade-ftw \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-south-1 \
  --parameter-overrides \
    VpcId=vpc-xxx \
    PublicSubnet1=subnet-xxx PublicSubnet2=subnet-xxx \
    TaskSubnet1=subnet-xxx TaskSubnet2=subnet-xxx \
    ApiKey=xxx \
    DesiredCount=0
```

> ⚠️ **首次建栈必须 `DesiredCount=0`**：ECR 为空时，非 0 的服务永远达不到稳定态，CloudFormation 会挂住并回滚。等服务里有镜像后再经 CI 或 `update-service --desired-count 1` 拉起。

参数取值（`ap-south-1`，与旧系统共享 VPC）：

| 参数 | 值 |
|---|---|
| `VpcId` | `vpc-0dda76b286c45fc27` |
| `PublicSubnet1` | `subnet-01b3aa683790a0e8c`（1a）|
| `PublicSubnet2` | `subnet-045ccd04e547efddb`（1b）|
| `TaskSubnet1` / `TaskSubnet2` | 同上两者 |
| `DesiredCount` | 首次 `0`，上线后 `1` |

> ⛔ **不要用 `subnet-0034e389a5d549ae3`（ap-south-1c）**：ALB 未覆盖该 AZ，会导致「约 1/3 概率部署失败」（旧系统踩过）。

### CI

[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) —— `push main` → 构建镜像 → 推 ECR → **拉线上 task definition 再改镜像** → `update-service` → 验证 `/health`。

需要仓库 secret：`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`。未配置时**只告警不失败**（`check-creds` 守卫）。

## 本地校验

```bash
# 静态检查
actionlint .github/workflows/deploy.yml
hadolint Dockerfile

# 真机构建 + 冒烟（镜像必须是 linux/amd64，与 Fargate 一致）
docker build --platform linux/amd64 -t arcade-ftw-backend:local .
docker run --rm -p 1692:8000 arcade-ftw-backend:local &
curl -s http://127.0.0.1:1692/health
```

> 基础镜像用 `public.ecr.aws/docker/library/python:3.12-slim` 而非 Docker Hub：避开 Docker Hub 匿名拉取限流（会让 CI 随机失败）。
> 需要 `actionlint` / `hadolint`：`brew install actionlint hadolint`。

## 注意事项

- 本地构建镜像必须 `--platform linux/amd64`（Mac 是 arm64，否则 Fargate 起不来）；CI 的 runner 本身是 amd64，不受影响。
- WS 是**进程内内存态**：单进程运行，禁止多 worker；`DesiredCount` 固定 1。
- **部署会清空房间**（替换 task 即重启进程）——活动期间禁止部署。
