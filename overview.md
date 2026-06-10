# Agentic RAG 论文问答系统 — 项目概览

## 项目来源

基于一份真实的简历项目拆解复刻：
- **项目名称**：Agentic RAG 论文问答系统
- **GitHub**：https://github.com/Z005-HV/Agentic-RAG
- **原开发者**：深圳大学信息管理与信息系统专业学生
- **时间**：2026.04 - 2026.06（2 个月）

## 已完成工作

### 1. 简历信息完整识别与拆解

使用 GLM-5v-Turbo 多模态模型成功识别简历截图，提取出：
- 完整技术栈（8 大组件）
- 5 大功能模块细节
- 关键性能指标（4ms 缓存、0% 幻觉召回）
- LangGraph Agent 状态机设计
- 数据流全景图

### 2. 项目脚手架搭建

- **docker-compose.yml** — 编排 PostgreSQL + Redis + OpenSearch + Ollama + Airflow + Langfuse
- **FastAPI 骨架** — config、logging、exceptions、routers
- **学习笔记** — Phase 1 Docker Compose 基础已完成
- **项目拆解文档** — `docs/项目拆解.md` 完整对齐简历所有功能点

### 3. 10 阶段复刻路线图（40 天）

| 阶段 | 主题 | 天数 | 对标简历功能 |
|------|------|------|-------------|
| 1 | 基建 | 2 | Docker Compose 一键部署 |
| 2 | FastAPI 骨架 | 3 | 后端架构 + 流式 SSE |
| 3 | 数据库 + 模型 | 3 | PostgreSQL 元数据管理 |
| 4 | 混合检索 | 6 | BM25 + 向量 RRF + BGE 嵌入 |
| 5 | LangGraph Agent | 8 | 问题链校验 → 改写 → 打分 → 补充 |
| 6 | 文档流水线 | 6 | Airflow + Docling + 自动建索引 |
| 7 | 缓存 + 观测 | 4 | Redis 4ms 缓存 + Langfuse 追踪 |
| 8 | 标准 RAG 模式 | 3 | 直接检索 → 生成（非 Agent） |
| 9 | 前端 + 部署 | 5 | 论文浏览 + 问答界面 + 生产部署 |

## 技术栈

FastAPI + PostgreSQL + OpenSearch + Redis + LangGraph + Ollama + Airflow + Langfuse + Docker Compose

## 关键性能指标

- **缓存命中响应**：~4ms
- **幻觉召回率**：0%（从传统 RAG 的 17.4% 降至 0%）
- **文档支持**：PDF / Word / TXT 自动解析
- **检索模式**：标准 RAG + Agentic 双模式

## 项目路径

```
C:\Users\PC\WorkBuddy\2026-06-09-16-31-36\agentic-rag-paper-qa
```

## 核心文档

- `README.md` — 项目总览与快速开始
- `docs/项目拆解.md` — 简历完整拆解与技术对标
- `learning-notes/01-docker-compose-基础.md` — Phase 1 学习笔记
- `docker-compose.yml` — 一键启动 7 个服务

## 后续建议

1. **从 Phase 1 开始**：`docker compose up -d` 跑通基础设施
2. **按路线图推进**：每个 Phase 产出可运行代码
3. **Code Review 文化**：每完成一个 Phase 团队 Review
