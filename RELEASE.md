# Release Guide

本文档描述如何为 **Rusted-Workshop-Translation-API** 发布一个新版本，以及镜像发布到 GHCR 的完整流程。

---

## 1. 镜像清单

每次 release 会并行构建 **4 个镜像**，全部发布到 GitHub Container Registry：

| Component    | Image                                                                                                  | Dockerfile              |
|--------------|--------------------------------------------------------------------------------------------------------|-------------------------|
| API          | `ghcr.io/<owner>/<repo>/api:<tag>`                                                                    | `Dockerfile.api`        |
| Coordinator  | `ghcr.io/<owner>/<repo>/coordinator:<tag>`                                                            | `Dockerfile.coordinator`|
| File Worker  | `ghcr.io/<owner>/<repo>/file-worker:<tag>`                                                            | `Dockerfile.file-worker`|
| Cleanup      | `ghcr.io/<owner>/<repo>/cleanup:<tag>`                                                                | `Dockerfile.cleanup`    |

`<owner>/<repo>` 为当前 GitHub 仓库全路径（自动小写）。  
所有镜像同时提供 **linux/amd64** 与 **linux/arm64** 两个架构。

### 自动生成的 tag

Workflow 使用 `docker/metadata-action` 同时打出多个语义化标签。以 release `v1.2.3` 为例，镜像会同时带有以下 tag：

| 触发                 | 产生的镜像 tag                                      |
|----------------------|-----------------------------------------------------|
| push tag `v1.2.3`    | `1.2.3`、`1.2`、`1`、`sha-<short>`                  |
| push tag `v1.2.3-rc1`| `1.2.3-rc1`、`sha-<short>`（不打 major/minor）      |
| push `main`          | `edge`、`sha-<short>`                               |
| workflow_dispatch    | 用户输入的 tag + `sha-<short>`                      |

> **建议生产环境始终固定到完整版本号**（如 `1.2.3`），不要用 `latest`。

---

## 2. 发版流程

### 2.1 准备

1. 确认 `main`（或 `master`）分支 CI 通过。
2. 更新 [`CHANGELOG.md`](./CHANGELOG.md)，把 `Unreleased` 段落重命名为目标版本。
3. 如有必要，更新 `pyproject.toml` 的 `version` 字段。
4. 提交上述改动：
   ```bash
   git add CHANGELOG.md pyproject.toml
   git commit -m "chore(release): v1.2.3"
   git push
   ```

### 2.2 打 tag 并推送

```bash
git tag -a v1.2.3 -m "Release v1.2.3"
git push origin v1.2.3
```

推送 tag 后，`.github/workflows/release.yml` 会自动：

1. 以矩阵方式并行构建 4 个镜像（amd64 + arm64）。
2. 推送到 GHCR。
3. 基于两个 tag 之间的 git log 生成 Changelog。
4. 创建 GitHub Release，附带 `docker-compose.prod.yml`、`.env.example`、`RELEASE.md` 作为 asset。

### 2.3 预发布（pre-release）

Tag 格式 `vX.Y.Z-<suffix>` 会被识别为 prerelease：

```bash
git tag -a v1.2.3-rc1 -m "Release candidate"
git push origin v1.2.3-rc1
```

GitHub Release 会自动打上 **Pre-release** 标记。

### 2.4 手动重发

在 GitHub → Actions → **Build & Release Docker Images** → Run workflow，  
填入一个自定义 tag（例如 `hotfix-20260425`），即可重新构建并推送。

---

## 3. GHCR 权限与可见性

### 3.1 首次发布后的配置

GHCR package 默认为 **private**。第一次发布完成后：

1. 访问 `https://github.com/<owner>/<repo>/pkgs/container/<repo>%2F<component>`
2. **Package settings** → **Manage Actions access**：确保当前仓库拥有 `Write` 权限（默认会自动配好）。
3. 如果希望镜像公开，在 **Danger Zone** → **Change visibility** 改为 **Public**。

> 小提示：公开镜像可以被任何人 `docker pull`，无需 token。

### 3.2 私有镜像的 pull

如果保持私有，部署机器 pull 镜像前需要登录：

```bash
# 使用 Personal Access Token（scope: read:packages）
echo "$GHCR_TOKEN" | docker login ghcr.io -u <username> --password-stdin

docker pull ghcr.io/<owner>/<repo>/api:1.2.3
```

---

## 4. 一键部署

```bash
# 1. 在部署机器上下载生产 compose + env 模板
curl -LO https://github.com/<owner>/<repo>/releases/download/v1.2.3/docker-compose.prod.yml
curl -LO https://github.com/<owner>/<repo>/releases/download/v1.2.3/.env.example
mv .env.example .env && vim .env   # 填凭据

# 2. 设置版本
export IMAGE_TAG=1.2.3
export IMAGE_NAMESPACE=<owner>/<repo>

# 3. 启动
docker compose -f docker-compose.prod.yml --env-file .env pull
docker compose -f docker-compose.prod.yml --env-file .env up -d

# 4. 初始化数据库（仅首次）
docker compose -f docker-compose.prod.yml exec api python scripts/init_db.py
```

---

## 5. 回滚

```bash
export IMAGE_TAG=1.2.2   # 上一个稳定版本
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Postgres 的 schema 回滚需要单独处理，不在本文档范围内。

---

## 6. 清理旧镜像

GHCR 会保留所有历史版本，建议定期清理：

- GitHub → 仓库 → Packages → 选择 package → 点删除。
- 或者用 [`actions/delete-package-versions`](https://github.com/actions/delete-package-versions) 接入自动清理 workflow。

---

## 7. 安全建议

1. **不要在镜像内打包 `.env`**（已在 `.dockerignore` 中排除）。
2. **不要在公开仓库的 Dockerfile 中写死任何密钥**。
3. 生产环境 `RABBITMQ_USE_SSL=true` + `REDIS_USE_SSL=true`（若中间件暴露公网）。
4. 访问 API 的 HTTP 入口前必须加反向代理 + 认证，参考 README「部署拓扑」章节。
