# Agentic RAG Paper QA — 智能论文问答系统

> **本地部署的论文问答系统** | 上传 PDF → AI 自动解析 → 提问获取答案（附论文出处）

---

## 功能介绍

上传学术论文 PDF，系统会自动解析、分块、建立索引。之后你可以用自然语言提问，AI 会：
- 从论文中检索相关内容
- 生成准确答案
- 标注答案出处（哪篇论文、哪一段）

支持两种问答模式：
- **标准 RAG 模式**：快速检索 → 直接回答
- **Agentic 模式**：AI 自主判断是否需要多次检索、如何优化查询

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 API | FastAPI (Python 3.12) |
| 数据库 | PostgreSQL 16 |
| 检索引擎 | OpenSearch 2.13 (BM25 + KNN 向量) |
| 大模型 | Ollama (本地运行 qwen2.5:3b) |
| 嵌入模型 | nomic-embed-text (768D) |
| 缓存 | Redis (语义缓存) |
| 文档解析 | pypdf (Docling 可选) |
| 前端 | HTML/CSS/JS (单页应用) |
| 部署 | Docker Compose |

---

## 系统要求

- **内存**：至少 8GB RAM（推荐 16GB）
- **磁盘**：至少 10GB 可用空间
- **软件**：Docker Desktop + Git

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/你的用户名/agentic-rag-paper-qa.git
cd agentic-rag-paper-qa
```

### 2. 启动 Docker 容器

```bash
docker-compose up -d
```

这会启动 4 个服务：
- PostgreSQL (端口 5432)
- OpenSearch (端口 9200)
- Redis (端口 6379)
- Ollama (端口 11434)

等待约 30 秒让所有服务完全启动。

### 3. 下载 AI 模型

```bash
# 嵌入模型（必需，约 274MB）
docker exec rag-ollama ollama pull nomic-embed-text

# 对话模型（必需，约 1.9GB）
docker exec rag-ollama ollama pull qwen2.5:3b
```

### 4. 配置环境变量

```bash
# 复制环境变量模板
cp backend/.env.example backend/.env
```

如果想启用 Langfuse 可观测功能，编辑 `backend/.env` 填入你的 API Key。

### 5. 安装后端依赖并启动

```bash
cd backend
pip install -r requirements.txt

# 启动后端（Windows）
PYTHONIOENCODING=utf-8 python run.py --port 8000

# 启动后端（Mac/Linux）
python run.py --port 8000
```

### 6. 打开前端

浏览器访问：**http://localhost:8000**

---

## 使用方法

### 上传论文

1. 在前端页面点击「上传论文」
2. 选择 PDF 文件
3. 等待解析完成（约 1-5 分钟，取决于文件大小）
4. 解析完成后状态显示为「已解析」

### 提问

在聊天框输入问题，例如：
- "这篇论文的核心观点是什么？"
- "作者使用了什么研究方法？"
- "实验结果如何？"

### 删除论文

鼠标悬停在文档列表中的论文项上，点击出现的 🗑️ 按钮。

---

## API 接口

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/v1/documents/upload` | 上传论文 PDF |
| GET | `/api/v1/documents` | 查看文档列表 |
| DELETE | `/api/v1/documents/{id}` | 删除文档 |
| POST | `/api/v1/chat` | Agentic 模式问答 |
| POST | `/api/v1/chat/standard` | 标准 RAG 模式问答 |
| GET | `/api/v1/search?q=xxx` | 检索论文内容 |
| DELETE | `/api/v1/chat/cache/clear` | 清空语义缓存 |

完整 API 文档：http://localhost:8000/docs

---

## 项目结构

```
agentic-rag-paper-qa/
├── docker-compose.yml          # Docker 服务定义
├── backend/
│   ├── run.py                 # 后端启动脚本
│   ├── .env.example           # 环境变量模板
│   ├── requirements.txt       # Python 依赖
│   ├── app/
│   │   ├── main.py            # FastAPI 入口
│   │   ├── api/v1/endpoints/  # API 端点
│   │   │   ├── chat.py        # 问答接口
│   │   │   ├── upload.py      # 文件上传
│   │   │   ├── search.py      # 检索接口
│   │   │   └── documents.py   # 文档管理
│   │   ├── services/          # 核心服务
│   │   │   ├── agent.py       # LangGraph Agent
│   │   │   ├── retrieval.py   # 混合检索
│   │   │   ├── document.py    # 文档解析
│   │   │   └── cache.py       # Redis 缓存
│   │   └── models/            # 数据模型
│   └── data/papers/            # 上传的 PDF 存储（不提交到 Git）
├── frontend/
│   └── index.html             # 前端页面
└── README.md
```

---

## 常见问题

### Q: 内存不足怎么办？

A: 项目默认使用 `qwen2.5:3b`（1.9GB）。如果还是 OOM，可以换更小的模型：
```bash
docker exec rag-ollama ollama pull qwen2.5:0.5b
```
然后修改 `backend/.env` 中的 `OLLAMA_MODEL=qwen2.5:0.5b`。

### Q: 中文回答质量不好？

A: `qwen2.5:3b` 的中文能力有限。如果机器内存够（16GB+），可以用更大的模型：
```bash
docker exec rag-ollama ollama pull qwen2.5:7b
```
然后修改 `backend/.env` 中的 `OLLAMA_MODEL=qwen2.5:7b`。

### Q: 缓存记住了错误答案怎么办？

A: 调用缓存清除接口：
```bash
curl -X DELETE http://localhost:8000/api/v1/chat/cache/clear
```

### Q: PDF 解析后只有 1 个 chunk？

A: 这是 pypdf 的局限性。安装 Docling 可以提升解析质量：
```bash
pip install docling
```

---

## 开发笔记

项目开发过程中的技术决策和踩坑记录：
- `learning-notes/` — 学习笔记
- `docs/` — 技术文档

---

## License

MIT License

---

**Made with ❤️**
