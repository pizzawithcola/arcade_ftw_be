# arcade_ftw_be

Arcade FTW 后端（FastAPI + Python 3.12）。与现有 `cyber_ai_festival_be` 完全隔离。

> 跨项目文档（系统手册 / WS 协议 / 题型契约 / AWS）见 [`../arcade_ftw_docs`](../arcade_ftw_docs)。

## 结构

```
.github/workflows/
└── deploy.yml         # CI：构建 → ECR → 迁移 → 滚动更新 → 验证 /health 与 /health/db
infra/
└── cloudformation.yml # 后端基础设施（ECR / ALB / ECS / RDS / IAM / 日志）
alembic.ini            # 迁移配置；DSN 由 DB_* 环境变量提供，不进版本库的凭据
docker-compose.yml     # 本地 Postgres（宿主 5433）
app/
├── main.py            # FastAPI 入口（CORS + /health + /health/db + /api 路由）
├── config.py          # pydantic-settings 环境变量；database_url 由 DB_* 拼装
├── database.py        # async engine + session factory
├── models.py          # rooms / players / scores / score_events（宽表 + 生成列 total_score）
├── migrations/        # Alembic（env.py 从环境变量取 DSN）
├── engine.py          # 状态机：idle / playing / paused / finished + index
├── flow.py            # 主线流程配置（改流程不改代码）
├── rooms.py           # RoomRegistry：每房间一个内存引擎，启动时从库里读房间
├── roster.py          # 房间成员：加入房间、房间名单、红蓝团队分配（玩家只看到名字）
├── players.py         # 管理员用的玩家 / 分数接口（改分、删人级联删分）
├── scores.py          # 玩家上报得分：按题幂等，写哪个分项由流程节点决定
├── sabotage.py        # Sabotage 引擎：红蓝三轮攻防投票 + 状态机 + 投票/启动/状态接口
├── sabotage_config.py # Sabotage 配置：卡牌、6×6 效果矩阵、剧本、计时（改配置不改代码）
├── ws.py              # WebSocket 端点：连接即推快照，之后同房间扇出
└── question_types/    # 题型注册表（插件化）
    ├── base.py        # 题型协议
    ├── choice.py      # 选择题（示例实现）
    └── __init__.py    # QUESTION_TYPES 注册表
tests/                 # pytest；schema 由真实迁移建出，不用 create_all
```

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

docker compose up -d          # 本地 Postgres（宿主端口 5433）
cp .env.example .env
alembic upgrade head          # 建表

uvicorn app.main:app --reload --port 5071
pytest                        # 集成测试：迁移 + 读写往返
```

> 端口不要用 1688（旧系统占用），本项目后端用 **5071**、前端用 **5070**。
> 本地 Postgres 固定 **5433**：开发机的 5432 常已被自己的 Postgres 占用。

验证：`curl http://localhost:5071/health` 与 `curl http://localhost:5071/health/db`

## 路由约定

- **游戏相关的一切都挂在 `/api/*`**。CloudFront 只用一条 `/api/*` 行为把流量反代到 ALB，所以**新增接口永远不需要改基础设施**。
- `/health`、`/health/db` 是唯一留在根路径的例外：ALB 直接探活 `/health`（不经过 CloudFront）。
- **ALB 只接受来自 CloudFront 的流量**（安全组用的是 CloudFront 的源站前缀列表 `pl-9aa247f3`）。所以**直连 ALB 会超时**，验证一律走 `https://<cloudfront 域名>`。

## 游戏接口

| 接口 | 鉴权 | 作用 |
|---|---|---|
| `POST /api/auth/player` | — | 昵称 + 活动登录；带旧令牌认回本人，否则重名自动编号（`Alex` → `Alex (2)`），返回令牌 |
| `GET /api/auth/events` | — | 可参加的活动列表；**登录页下拉框的数据源** |
| `POST /api/auth/admin` | — | 管理员用户名 + 密码登录，返回令牌 |
| `GET /api/auth/me` | Bearer | 这个令牌是谁 |
| `GET /api/rooms` | **display** | `{current, rooms}`：房间按新到旧，`current` 是最近创建的那一轮；含状态、`label` / `event`、已加入人数、在线连接数 |
| `GET /api/rooms/{code}/state` | — | 该房间的 phase / 节点 / 剩余毫秒 |
| `POST /api/rooms/{code}/start` \| `pause` \| `resume` \| `finish` | **管理员（full）** | 播放控制；`finish` 结束本轮并**清空房间成员**（`room_code`/`team` 置空），`start` 重开即为空房间 |
| `POST /api/rooms/{code}/go/{index}` | **管理员（full）** | 跳到任意节点（也是从 `finished` / `idle` 复活的方式）|
| `POST /api/rooms/{code}/reset` | **管理员（full）** | 回到 `idle` 未开演态、第 1 节点、时钟停止（排练后交台）|
| `POST /api/rooms/{code}/join` | **玩家** | 把当前玩家记入该房间（每次进页面都调，重连后仍在原房间）|
| `GET /api/rooms/{code}/players` | **已登录** | 房间名单，按总分倒序。admin/display 得到 `player_id/event/team/total_score`，玩家**只得到昵称**（等待室用）|
| `POST /api/rooms/{code}/teams/assign` | **管理员（full）** | Sabotage 分队：按 id 交替均分红蓝，可重跑（幂等、确定性）；join 进新房间会清掉旧 team |
| `GET /api/rooms/current` | — | 当前轮的 `{code, phase}`；**玩家端靠它跟随当前轮**（只暴露这两个字段）|
| `POST /api/rooms` | **管理员（full）** | 开新一轮：code 可省略（自动 `round-N`），写库并**立刻建引擎**（否则刚拿到的房间码打开是 404）|
| `GET /api/rooms/{code}` | **display** | 单个房间的元信息 + 状态 |
| `PATCH /api/rooms/{code}` | **管理员（full）** | 改 `event` / `label`；**不支持改 code**（code 已在 URL、`/r/{code}` 排障链接和玩家归属里）|
| `DELETE /api/rooms/{code}` | **管理员（full）** | 删房间；**进行中（playing/paused）拒绝删除**（409）。成员由 DB 置空，不删人 |
| `POST /api/rooms/{code}/players` | **管理员（full）** | 替玩家建成员（设备掉了也照样在名单上），重名自动编号 |
| `GET /api/players` \| `GET /api/players/{id}` | **display** | 查玩家（可 `?room=` / `?event=` 过滤），带 `total_score` 与四个分项 |
| `PATCH /api/players/{id}` | **管理员（full）** | 改昵称 / 活动 / 所属房间；`room_code: null` 表示移出房间，省略表示不动 |
| `DELETE /api/players/{id}` | **管理员（full）** | 删玩家，**分数由外键级联一起删** |
| `PUT /api/players/{id}/score` | **管理员（full）** | 设置分项（未给的分项保持不变），首次写入自动建行 |
| `POST /api/players/{id}/score/adjust` | **管理员（full）** | 按增量加/减分（运营纠错用）|
| `POST /api/me/score` | **玩家** | 上报自己在一题 / 整个节点上得的分数；写哪个分项由节点的 `score` 决定，按 `room:node:question` 幂等 |
| `POST /api/sabotage/{code}/start` | **管理员（full）** | 用房间当前红蓝分队启动 Sabotage（两队非空），进入 10s 揭晓 |
| `POST /api/sabotage/{code}/vote` | **玩家** | 投/改自己队伍的卡；只有当前行动方、且卡可投（未 spent、买得起）才接受 |
| `GET /api/sabotage/{code}/state` | — | 当前阶段、预算、比分、已用卡、可选卡（不暴露个人投票）|
| `WS /api/ws?room={code}` | — | 只收推送：连上先推该房间当前状态，之后只扇出给**同房间**的连接 |

> **单人房不跑 Sabotage**：一个玩家无法分成两队，所以主流程到该节点时直接 `go_to(index + 1)` 越过它，该玩家 `sabotage_score` 记为 `cfg.SOLO_SCORE`（2700，完美获胜分）。0 人房保持原行为（不启动、节点照常走完）。细节见 `arcade_ftw_docs/games/sabotage.md`。

管理员控制走 **HTTP 而不是 WS**：可鉴权、能在 `/api/docs` 里直接点、失败返回真实状态码；状态变化再由 WS 广播给所有人。

### 活动与玩家身份

活动列表的**唯一来源**是 `app/events.py`，它同时驱动 `GET /api/auth/events` 与登录校验——所以新增活动是**纯后端改动**，下拉框下次加载就自动带上。

四个可选活动键：`hk_fintech_week`、`hk_employee`、`sg_fintech_week`、`sg_employee`。

**玩家身份是 `(event, nickname)` 而不是昵称**：同名但参加不同活动算**两个玩家**。否则两个恰好同名的真实用户会被合并成一行、共用一个记分板。

> ⚠️ `players.event` 里可能出现一个**不可选**的值 `legacy`：它由迁移回填，用于标记「`event` 字段出现之前就存在的行」。按活动统计时记得排除它。

> ⚠️ `event` 字段是**由 `country` 重命名**而来（`9a1c4f0b7d21`）。原字段从未被任何接口写入，只在一个测试里出现过，所以重命名无损且保留了行 id。

> ⚠️ **Swagger UI 在 `/api/docs`，不在 `/docs`。** 后端只挂在 `/api` 下，因为 CloudFront 只把 `/api/*` 代理到后端。根路径会落到 S3 源站，而 SPA 回退把「对象不存在」改写成 `index.html`——于是 `/docs` 会**返回 200 但内容是 React 页面**，任何只看状态码的检查都发现不了。CI 因此改成断言 `content-type` 与 spec 内容。

### 引擎如何推进

`app/engine.py` 用 `asyncio` 后台任务 + `time.monotonic()` **自主推进**主流程，管理员只能干预。

- 快照里的 `question` 让**每块屏都知道现在是第几题**：节点声明 `questions` / `seconds_each`，引擎按节点时钟算，设备不需要上报。
- 倒计时基于**单调时钟**，宿主改系统时间不会让节目跳。
- 暂停是**冻结剩余时间**（而不是挪动 deadline），所以暂停多久，恢复后剩余时间都不变。
- 快照里给的是 `remaining_ms`（时长）而不是绝对截止时刻，前端据此在本机算倒计时——**机器时钟有偏差只会整体平移，不会改变节点长度**。

## 数据库

- **schema 只由 Alembic 迁移创建**，应用启动不建表。新增改动：`alembic revision --autogenerate -m "..."` → 检查生成的脚本 → `alembic upgrade head`。
- 四张表：`rooms(code PK, event, label, created_at)`、`players(..., room_code → rooms.code ON DELETE SET NULL, team red/blue 可空)`、`scores(player_id PK, 四个分项 + 生成列 total_score)`、`score_events(player_id + event_key PK, points)`（上报幂等记录）。
- 计分是**宽表**（四个游戏各一列 + 数据库生成列 `total_score`），不是长表——被问的查询是「按 total 排名」。加第五个游戏需要一次迁移。
- **房间是数据**：`rooms` 表是房间的真相来源，`alembic` 迁移里 seed 了 `main`。启动时 `rooms.load()` 读库建引擎；**读库失败只警告并退回默认房间**，否则 DB 抖动会让任务启动失败、被 ALB 反复重启，把内存里的节目全清掉。
- `players.room_code` 可空：登录就建行，**进房间**才写归属（`POST /api/rooms/{code}/join`）。删房间只会把成员置空，不会删人。
- `scores` 属于玩家（`ON DELETE CASCADE`）：**删玩家就删分**，不需要应用层清理，也不会有分数挂在已删的人上。`total_score` 是生成列，接口**只接受四个分项**——直接写 total 会被 pydantic 忽略，库里也会拒。
- **四个分项 = 四个分区**（`hallucination + quiz1`、`data + quiz2`、`sabotage`、`ultimate`），节点在 `flow.py` 用 `score` 声明归属。
- `score_events` 是分数上报的幂等键：`(player_id, event_key)` 冲突时插入无效，只有真正插入的那条才累加分数。
- `/health` **刻意不碰数据库**（ALB 用它探活，DB 抖动不该重启任务、清空内存房间）；数据库状态看 `/health/db`。
- 测试会**先 DROP SCHEMA 再用迁移重建**，所以绝不能指向真实库——请只对 `docker-compose` 起的本地库运行 `pytest`。

## 部署

### 基础设施（CloudFormation）

[`infra/cloudformation.yml`](infra/cloudformation.yml) —— **只覆盖后端**资源：ECR、ALB + Target Group、ECS cluster/task/service、**RDS**、安全组、IAM role、日志组，以及 CI 用的 GitHub OIDC provider + 部署角色。

不包含（刻意）：VPC / 子网（与旧系统共享，仅作参数传入）、DB subnet group（复用现有 `default-vpc-0dda76b286c45fc27`）、S3 + CloudFront（前端 repo 的范围）。

RDS 要点：`db.t4g.micro`、PostgreSQL 16、`PubliclyAccessible=false`、由 `arcade-ftw-rds-sg` 限制只允许 ECS 安全组访问、**主密码由 RDS 托管在 Secrets Manager**（模板里没有任何凭据）。

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
    SecretKey=xxx \
    AdminUsername=admin \
    AdminPassword=xxx \
    DesiredCount=0
```

> ⚠️ **首次建栈必须 `DesiredCount=0`**：ECR 为空时，非 0 的服务永远达不到稳定态，CloudFormation 会挂住并回滚。等服务里有镜像后再经 CI 或 `update-service --desired-count 1` 拉起。
> ⚠️ **建 RDS 时不要一次接进任务定义**：先建库（不接任务定义）→ 确认托管密钥的 `password` 键可用 → 再接任务定义。否则一旦任务起不来，栈回滚会**连同刚建好的 RDS 一起删掉**。

参数取值（`ap-south-1`，与旧系统共享 VPC）：

| 参数 | 值 |
|---|---|
| `VpcId` | `vpc-0dda76b286c45fc27` |
| `PublicSubnet1` | `subnet-01b3aa683790a0e8c`（1a）|
| `PublicSubnet2` | `subnet-045ccd04e547efddb`（1b）|
| `TaskSubnet1` / `TaskSubnet2` | 同上两者 |
| `DbSubnetGroupName` | `default-vpc-0dda76b286c45fc27`（现有，未新建）|
| `DbInstanceClass` | `db.t4g.micro` |
| `DbEngineVersion` | `16.15`（必须给具体小版本，`16` 会被拒）|
| `SecretKey` | 令牌签名密钥；换掉它 = 一次性让所有已发令牌失效 |
| `AdminUsername` / `AdminPassword` | 管理员（主持）登录凭据 |
| `DesiredCount` | 首次 `0`，上线后 `1` |

> ⛔ **不要用 `subnet-0034e389a5d549ae3`（ap-south-1c）作任务子网**：ALB 未覆盖该 AZ，会导致「约 1/3 概率部署失败」（旧系统踩过）。RDS 的子网组不受此限制。

### CI

[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)：

```
push main
  → GitHub OIDC 换取临时凭证（无任何长期密钥 / GitHub secret）
  → 构建镜像 → 推 ECR
  → 拉线上 task definition、改镜像、注册新 revision
  → 以新镜像跑一次性 Fargate 任务：alembic upgrade head   ← 失败则中止，不动线上
  → update-service 滚动更新
  → 经 CloudFront 验证 /health 与 /health/db（ALB 直连已被安全组拒绝）
```

迁移刻意放在**滚动更新之前**：迁移失败时旧 revision 继续服务，不会出现「代码已上新、schema 未生效」。迁移任务复用线上服务的子网与安全组。

## 本地校验

```bash
# 静态检查
actionlint .github/workflows/deploy.yml
hadolint Dockerfile

# 真机构建 + 冒烟（镜像必须是 linux/amd64，与 Fargate 一致）
docker build --platform linux/amd64 -t arcade-ftw-backend:local .
docker run --rm -p 1692:8000 arcade-ftw-backend:local &
curl -s http://127.0.0.1:1692/health

# 测试（先 docker compose up -d 起本地库）
pytest
```

> ⚠️ **本机的 Python HTTP/WS 客户端会去读 macOS 系统 SOCKS 代理**（`127.0.0.1:7897`），把 `localhost` 也代理出去，于是本地请求得到 **502** 或 `requires python-socks`。本地写脚本验证时要显式绕过：
> - `httpx.AsyncClient(..., trust_env=False)`
> - `websockets.connect(..., proxy=None)`
> - `curl --noproxy '*'`
>
> ⚠️ `pytest` 的集成测试会 **DROP SCHEMA** 再跑迁移，**绝不能指向真实库**。

> 基础镜像已镜像到自己的 ECR `arcade-ftw-backend:python-base-3.12-slim`（`public.ecr.aws` 会随机 429 限流，CI 曾连挂 3 次）。Dockerfile 顶部注释有刷新命令；注意 `--platform linux/amd64`。
> 需要 `actionlint` / `hadolint`：`brew install actionlint hadolint`。

## 注意事项

- 本地构建镜像必须 `--platform linux/amd64`（Mac 是 arm64，否则 Fargate 起不来）；CI 的 runner 本身是 amd64，不受影响。
- WS 是**进程内内存态**：单进程运行，禁止多 worker；`DesiredCount` 固定 1。
- **部署会清空房间**（替换 task 即重启进程）——活动期间禁止部署。
- **迁移不可自动回滚**：代码能回滚，schema 不能。写迁移时请配好 `downgrade()`。
- 容器里必须能拿到 `alembic.ini`（Dockerfile 已 `COPY`）——漏了的话 CI 的迁移步骤会直接失败。
