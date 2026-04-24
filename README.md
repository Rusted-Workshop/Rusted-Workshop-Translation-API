# Rusted-Workshop-Translation-API

> Rusted Workshop 模组 (.rwmod) 的自动翻译服务 —— FastAPI + RabbitMQ + Postgres + S3 + LLM。

本服务由 4 个可独立部署的组件组成，全部以 Docker 镜像发布到 **GitHub Container Registry (GHCR)**，支持 `linux/amd64` 与 `linux/arm64` 双架构。

[![Build & Release](https://github.com/Rusted-Workshop/Rusted-Workshop-Translation-API/actions/workflows/release.yml/badge.svg)](https://github.com/Rusted-Workshop/Rusted-Workshop-Translation-API/actions/workflows/release.yml)

---

## 目录

- [架构](#架构)
- [镜像清单](#镜像清单)
- [快速开始](#快速开始)
- [部署拓扑](#部署拓扑)
- [环境变量](#环境变量)
- [本地开发](#本地开发)
- [发版](#发版)
- [License](#license)

---

## 架构

```
                     ┌───────────────┐
   client  ──HTTP──▶ │  api (8001)   │──写任务──▶ Postgres (任务主存储)
                     └───────┬───────┘
                             │ 发布
                             ▼
                     ┌───────────────┐
                     │   RabbitMQ    │◀── 消息总线
                     └───┬────┬──────┘
                         │    │
                  ┌──────▼┐  ┌▼──────────────┐
                  │coordi-│  │ file-worker   │ × N
                  │ nator │  │ （翻译执行） │
                  └───┬───┘  └───┬───────────┘
                      │          │
                      ▼          ▼
                 Postgres/Redis  S3 (or MinIO) + OpenAI

                     ┌───────────────┐
                     │   cleanup     │──定时清理工作目录
                     └───────────────┘
```

| 组件         | 作用                                                         | 扩缩容       |
|--------------|--------------------------------------------------------------|--------------|
| **api**      | FastAPI 入口；接收上传、创建任务、查询状态；监听 `:8001`     | 水平扩展     |
| **coordinator** | 任务编排、分片、Stall 恢复                                | 单实例即可   |
| **file-worker** | 消费文件级队列，真正执行 LLM 翻译                          | **按队列深度水平扩展**（默认 4 副本） |
| **cleanup**  | 周期性清理共享工作目录 `/tmp/translation_work`               | 单实例       |

底层依赖：**PostgreSQL 16**、**Redis 7**、**RabbitMQ 3**、**S3 兼容存储**（AWS S3 / MinIO / Ceph）、**OpenAI 兼容 LLM 端点**。

---

## 镜像清单

所有镜像均发布在：
`ghcr.io/rusted-workshop/rusted-workshop-translation-api/<component>:<tag>`

| Component    | Pull command                                                                                                        |
|--------------|---------------------------------------------------------------------------------------------------------------------|
| API          | `docker pull ghcr.io/rusted-workshop/rusted-workshop-translation-api/api:latest`                                    |
| Coordinator  | `docker pull ghcr.io/rusted-workshop/rusted-workshop-translation-api/coordinator:latest`                            |
| File Worker  | `docker pull ghcr.io/rusted-workshop/rusted-workshop-translation-api/file-worker:latest`                            |
| Cleanup      | `docker pull ghcr.io/rusted-workshop/rusted-workshop-translation-api/cleanup:latest`                                |

**强烈建议生产环境固定到具体版本号**（如 `:1.2.3`），不要用 `:latest`。可用 tag 见 [Releases](../../releases)。

---

## 快速开始

### 方案 A：单机一把梭（推荐用于试用 / 内网部署）

在一台 4C8G+ 的机器上，**无需构建**，直接拉镜像启动：

```bash
# 1. 下载部署文件
curl -LO https://raw.githubusercontent.com/Rusted-Workshop/Rusted-Workshop-Translation-API/main/docker-compose.prod.yml
curl -LO https://raw.githubusercontent.com/Rusted-Workshop/Rusted-Workshop-Translation-API/main/.env.example
mv .env.example .env

# 2. 编辑 .env，至少填好：
#    POSTGRES_PASSWORD / REDIS_PASSWORD / RABBITMQ_PASSWORD
#    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / S3_BUCKET
#    OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
vim .env

# 3. 选择版本（可选，默认 latest）
export IMAGE_TAG=1.2.3
export IMAGE_NAMESPACE=rusted-workshop/rusted-workshop-translation-api

# 4. 启动
docker compose -f docker-compose.prod.yml --env-file .env pull
docker compose -f docker-compose.prod.yml --env-file .env up -d

# 5. 初始化数据库（仅首次）
docker compose -f docker-compose.prod.yml exec api python scripts/init_db.py

# 6. 访问
curl http://localhost:8001/docs
```

### 方案 B：私有镜像

如果 GHCR package 是 private，机器上先登录：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-username> --password-stdin
```

---

## 部署拓扑

### 拓扑 1：纯内网（最简单）

所有服务跑在同一台内网机器上。对外仅暴露 api:8001（或再加一层 Nginx/Caddy）。适用于**仅内部使用**的场景。

### 拓扑 2：API + 中间件在公网 VPS，Worker 在内网（推荐）

适用于「公网用户访问 API，但重活不想跑在云上」的场景。

```
  [公网用户] ──▶ VPS (api + pg + redis + mq + minio)
                          ▲
                          │ TLS / WireGuard
                          │
                   [内网机器] (coordinator + file-worker × N + cleanup)
                          │
                          ▼ 出站
                       OpenAI
```

- 公网 VPS 跑 `docker-compose.prod.yml` 中 `api / postgres / redis / rabbitmq / minio / minio-init`
- 内网机器跑同一份 compose 中 `coordinator / file-worker / cleanup`，把 `POSTGRES_HOST` / `REDIS_HOST` / `RABBITMQ_HOST` / `AWS_ENDPOINT_URL` 指向 VPS
- **建议通过 WireGuard 把两台机器接入同一个私有子网**，中间件只监听 WG 接口，避免把 DB/MQ 直接暴露到公网

### 拓扑 3：全部容器 + 外部托管中间件（AWS / Aliyun）

把 `docker-compose.prod.yml` 里的 `postgres / redis / rabbitmq / minio` 四个服务删除，并在 `.env` 中填入外部地址即可：

- Postgres → RDS / 阿里云 RDS
- Redis → ElastiCache / Tencent Cloud Redis
- RabbitMQ → Amazon MQ / CloudAMQP
- S3 → 直接用 AWS S3（删除 `AWS_ENDPOINT_URL`）

---

## 环境变量

完整变量列表见 [`.env.example`](./.env.example)。关键变量：

| 变量                     | 说明                                                | 示例                                  |
|--------------------------|-----------------------------------------------------|---------------------------------------|
| `POSTGRES_HOST` / `PORT` | Postgres 地址                                       | `postgres` / `5432`                   |
| `REDIS_HOST` / `PORT`    | Redis 地址                                          | `redis` / `6379`                      |
| `REDIS_USE_SSL`          | Redis 是否使用 TLS                                  | `false`                               |
| `RABBITMQ_HOST` / `PORT` | RabbitMQ 地址                                       | `rabbitmq` / `5672`（TLS: `5671`）    |
| `RABBITMQ_USE_SSL`       | RabbitMQ 是否使用 TLS                               | `false`                               |
| `AWS_ENDPOINT_URL`       | S3 端点，MinIO 时指向内网 MinIO                     | `http://minio:9000`                   |
| `S3_BUCKET`              | 对象存储桶名                                        | `translation`                         |
| `OPENAI_API_KEY`         | LLM 凭据                                            | `sk-xxx`                              |
| `OPENAI_BASE_URL`        | 兼容 OpenAI 的 API 根路径，可指向 Azure/国内代理等 | `https://api.openai.com/v1`           |
| `OPENAI_MODEL`           | 模型名                                              | `gpt-4o-mini`                         |
| `FILE_WORKER_REPLICAS`   | file-worker 副本数                                  | `4`                                   |
| `RETENTION_DAYS`         | 工作目录保留天数                                    | `7`                                   |

---

## 本地开发

```bash
# Python 3.12+ with uv
uv sync

# 起中间件
docker compose up -d postgres redis rabbitmq minio

# 初始化 DB
python scripts/init_db.py

# 分终端启动
python start_api.py
python -m workers.coordinator_worker
python -m workers.file_translation_worker
python start_cleanup.py
```

---

## 发版

完整发版流程见 [`RELEASE.md`](./RELEASE.md)。简而言之：

```bash
# 更新 CHANGELOG，然后打 tag
git tag -a v1.2.3 -m "Release v1.2.3"
git push origin v1.2.3
```

GitHub Actions 会自动：

1. 矩阵并行构建 4 个镜像（amd64 + arm64）
2. 推送到 `ghcr.io/<owner>/<repo>/<component>`，打上 `1.2.3`、`1.2`、`1`、`sha-<short>` 多个 tag
3. 生成 Changelog 并创建 GitHub Release，附带 `docker-compose.prod.yml` 等部署资产

---

## License

MIT（或按仓库实际情况调整）。
