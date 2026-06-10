#!/bin/bash
# ============================================================
# 阿里云 ECS 一键部署脚本
# ============================================================
# 使用方法：
#   1. 登录阿里云 ECS（SSH）
#   2. 克隆项目：git clone https://github.com/yinaoran77-sys/agentic-rag-paper-qa.git
#   3. cd agentic-rag-paper-qa/deploy
#   4. chmod +x deploy.sh
#   5. ./deploy.sh
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PROJECT_NAME="agentic-rag-paper-qa"
MODEL_NAME="${OLLAMA_MODEL:-qwen2.5:3b}"
EMBED_MODEL="${EMBEDDING_MODEL:-nomic-embed-text}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Agentic RAG Paper QA - 云部署脚本${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Step 1: 检查 Docker
echo -e "${YELLOW}[Step 1/7] 检查 Docker...${NC}"
if ! command -v docker &> /dev/null; then
    echo "Docker 未安装，正在安装..."
    curl -fsSL https://get.docker.com | sh
    sudo systemctl start docker
    sudo systemctl enable docker
    sudo usermod -aG docker $USER
    echo -e "${GREEN}Docker 安装完成，请重新登录后重试${NC}"
    exit 0
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "Docker Compose 未安装，正在安装..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

DOCKER_COMPOSE="docker compose"
if ! docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
fi

echo -e "${GREEN}Docker 检查通过 ✓${NC}"
echo ""

# Step 2: 检查内存
echo -e "${YELLOW}[Step 2/7] 检查系统资源...${NC}"
TOTAL_MEM=$(free -m | awk '/^Mem:/{print $2}')
echo "总内存: ${TOTAL_MEM}MB"

if [ "$TOTAL_MEM" -lt 6144 ]; then
    echo -e "${RED}警告：内存不足 6GB，运行可能会较慢${NC}"
    echo "建议升级到 8GB 以上配置"
fi
echo ""

# Step 3: 启动基础设施
echo -e "${YELLOW}[Step 3/7] 启动 Docker 容器...${NC}"
cd "$(dirname "$0")"
$DOCKER_COMPOSE -f docker-compose.prod.yml down 2>/dev/null || true
$DOCKER_COMPOSE -f docker-compose.prod.yml up -d
echo -e "${GREEN}容器启动完成 ✓${NC}"
echo ""

# Step 4: 等待服务就绪
echo -e "${YELLOW}[Step 4/7] 等待服务就绪（约 30 秒）...${NC}"
sleep 10
echo "  PostgreSQL 检查中..."
until $DOCKER_COMPOSE -f docker-compose.prod.yml exec -T postgres pg_isready -U raguser -d ragdb >/dev/null 2>&1; do
    sleep 2
done
echo "  PostgreSQL 就绪 ✓"

echo "  OpenSearch 检查中..."
until curl -s http://localhost:9200/_cluster/health >/dev/null 2>&1; do
    sleep 3
done
echo "  OpenSearch 就绪 ✓"
echo ""

# Step 5: 下载 AI 模型
echo -e "${YELLOW}[Step 5/7] 下载 AI 模型...${NC}"
echo "  下载嵌入模型: $EMBED_MODEL"
docker exec rag-ollama ollama pull "$EMBED_MODEL" || echo "嵌入模型可能已存在"

echo "  下载对话模型: $MODEL_NAME"
docker exec rag-ollama ollama pull "$MODEL_NAME" || echo "对话模型可能已存在"
echo -e "${GREEN}模型下载完成 ✓${NC}"
echo ""

# Step 6: 初始化数据库
echo -e "${YELLOW}[Step 6/7] 初始化数据库...${NC}"
$DOCKER_COMPOSE -f docker-compose.prod.yml exec -T backend python -c "
import asyncio
from app.core.database import init_db
asyncio.run(init_db())
print('数据库初始化完成')
" 2>/dev/null || echo "数据库已初始化或将在首次请求时自动初始化"
echo -e "${GREEN}数据库初始化完成 ✓${NC}"
echo ""

# Step 7: 完成
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  部署完成！🎉${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "访问地址:"
echo "  前端页面: http://$(curl -s ifconfig.me || echo '你的服务器IP')"
echo "  API 文档: http://$(curl -s ifconfig.me || echo '你的服务器IP')/docs"
echo ""
echo "管理命令:"
echo "  查看日志: cd deploy && $DOCKER_COMPOSE -f docker-compose.prod.yml logs -f"
echo "  停止服务: cd deploy && $DOCKER_COMPOSE -f docker-compose.prod.yml down"
echo "  重启服务: cd deploy && $DOCKER_COMPOSE -f docker-compose.prod.yml restart"
echo ""
echo -e "${YELLOW}提示：首次使用需要上传论文 PDF 后，AI 才能回答相关问题。${NC}"