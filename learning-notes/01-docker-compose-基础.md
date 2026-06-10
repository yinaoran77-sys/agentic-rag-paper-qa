# Phase 1: Docker Compose 基础与项目基建

> 日期：2026-06-09  
> 目标：让所有基础设施跑起来

---

## 📖 今日学习要点

### 1. Docker Compose 是什么？

Docker Compose 是 Docker 官方的多容器编排工具。用一个 `docker-compose.yml` 文件定义所有服务，一条命令启动整个应用栈。

**为什么不用 Docker 命令逐个启动？**
- 服务之间有依赖关系（如后端依赖数据库）
- 网络需要统一配置
- 数据卷需要持久化声明
- 环境变量需要集中管理

### 2. 本项目的服务架构

```
┌─────────────────────────────────────────────────────────┐
│                     Docker Compose                       │
├─────────────┬─────────────┬─────────────┬───────────────┤
│  PostgreSQL │    Redis    │ OpenSearch  │    Ollama     │
│   :5432     │   :6379     │   :9200     │   :11434      │
├─────────────┴─────────────┴─────────────┴───────────────┤
│  Airflow Webserver :8080  │  Airflow Scheduler           │
├───────────────────────────┴─────────────────────────────┤
│                    Langfuse :3000                        │
└─────────────────────────────────────────────────────────┘
```

### 3. 每个服务的作用

| 服务 | 端口 | 用途 |
|------|------|------|
| **PostgreSQL** | 5432 | 主数据库，存储用户、文档、会话元数据 |
| **Redis** | 6379 | 缓存层，加速重复查询；会话状态存储 |
| **OpenSearch** | 9200 | 搜索引擎，支持 BM25 全文 + KNN 向量检索 |
| **Ollama** | 11434 | 本地大模型推理引擎，无需联网 |
| **Airflow** | 8080 | 工作流编排，定时爬取 arXiv 论文 |
| **Langfuse** | 3000 | 可观测平台，追踪每个 LLM 请求 |

### 4. 关键配置解析

```yaml
# 服务依赖与健康检查
services:
  langfuse:
    depends_on:
      postgres:
        condition: service_healthy  # 等 PostgreSQL 健康后才启动
    
  postgres:
    healthcheck:
      test: ["CMD-SHELL", "pg_isready ..."]
      interval: 10s
      retries: 5
```

**为什么要 healthcheck？**
- 避免服务启动顺序导致的连接失败
- 容器编排系统（Docker Compose / K8s）需要知道服务是否就绪
- 数据库初始化需要时间，直接连接会报错

### 5. 数据卷（Volumes）

```yaml
volumes:
  postgres_data:  # 命名卷，Docker 自动管理
```

**为什么用命名卷而不是 bind mount？**
- 跨平台兼容（Windows 路径问题）
- Docker 自动管理生命周期
- 备份和恢复更方便

---

## 🚀 动手操作

### Step 1: 启动所有服务

```bash
cd agentic-rag-paper-qa

# 复制环境变量模板
cp backend/.env.example backend/.env

# 启动所有服务（后台运行）
docker compose up -d

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f postgres
```

### Step 2: 验证每个服务

```bash
# PostgreSQL
docker compose exec postgres psql -U raguser -d ragdb -c "SELECT 1;"

# Redis
docker compose exec redis redis-cli ping
# 预期输出: PONG

# OpenSearch
curl http://localhost:9200/_cluster/health
# 预期输出: {"status":"green", ...}

# Ollama
curl http://localhost:11434/api/tags
# 预期输出: 模型列表（刚开始可能是空的）

# Airflow
# 浏览器访问 http://localhost:8080
# 用户名: admin 密码: admin

# Langfuse
# 浏览器访问 http://localhost:3000
```

### Step 3: 拉取 Ollama 模型

```bash
# 拉取通义千问 7B 模型（适合中文）
docker compose exec ollama ollama pull qwen2.5:7b

# 拉取嵌入模型
docker compose exec ollama ollama pull nomic-embed-text

# 测试推理
docker compose exec ollama ollama run qwen2.5:7b
```

---

## 🧠 核心概念总结

### 网络隔离

所有服务在同一个 `rag-network` 桥接网络中：
- 服务之间通过服务名通信（如 `postgres`、`redis`）
- 外部通过 `localhost:端口` 访问
- 安全：未暴露的端口无法从外部访问

### 环境变量管理

```
优先级：环境变量 > .env 文件 > 默认值
```

Docker Compose 会自动读取项目目录下的 `.env` 文件，用 `${VAR}` 语法引用。

### 资源限制（重要！）

```yaml
environment:
  - "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m"
```

**为什么要限制内存？**
- Java 应用（OpenSearch、Airflow）默认会吃掉大量内存
- 本地开发机器资源有限（尤其是 16GB 内存的笔记本）
- 生产环境需要根据实际负载调整

---

## ❓ 常见问题

**Q: OpenSearch 启动失败，日志显示内存错误？**
A: 修改 `OPENSEARCH_JAVA_OPTS` 为更小的值，如 `-Xms256m -Xmx256m`

**Q: Windows 上 Docker 很慢？**
A: 建议：
1. 使用 WSL2 后端
2. 给 Docker Desktop 分配至少 8GB 内存
3. 将项目目录放在 WSL 文件系统中（`\\wsl$\Ubuntu\home\...`）

**Q: 端口冲突？**
A: 修改 `docker-compose.yml` 中的端口映射，如 `5433:5432`

---

## 📚 延伸阅读

- [Docker Compose 官方文档](https://docs.docker.com/compose/)
- [OpenSearch Docker 部署指南](https://opensearch.org/docs/latest/install-and-configure/install-opensearch/docker/)
- [Ollama 模型库](https://ollama.com/library)

---

## ✅ 本阶段检查清单

- [ ] `docker compose up -d` 成功启动所有服务
- [ ] PostgreSQL 可以连接并执行 SQL
- [ ] Redis 返回 PONG
- [ ] OpenSearch 集群状态为 green/yellow
- [ ] Ollama 可以拉取模型
- [ ] Airflow Web UI 可以登录
- [ ] 理解了每个服务的作用

---

*下一章：[02-fastapi-工程实践.md]*
