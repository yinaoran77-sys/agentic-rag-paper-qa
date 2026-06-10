# 阿里云 ECS 云部署指南

> 把系统部署到云服务器，朋友打开网址就能用，零门槛。

---

## 系统要求

| 配置 | 最低要求 | 推荐 |
|------|---------|------|
| CPU | 2 核 | 4 核 |
| 内存 | 6 GB | 8 GB |
| 磁盘 | 20 GB | 40 GB |
| 带宽 | 1 Mbps | 3 Mbps |
| 系统 | Ubuntu 22.04 | Ubuntu 22.04 |

> 阿里云 ECS 免费试用：4核8GB 足够运行。

---

## 部署步骤

### 1. 购买/领取阿里云 ECS

1. 登录 [阿里云控制台](https://ecs.console.aliyun.com/)
2. 领取免费试用（4核8GB，300元额度，3个月）
3. 创建实例：
   - **地域**：选择离你最近的（或香港，免备案）
   - **镜像**：Ubuntu 22.04 LTS
   - **实例规格**：4核8GB
   - **安全组**：开放端口 `22`、`80`、`8000`（后面会用到）
4. 设置 root 密码或使用密钥对登录

### 2. 连接服务器

```bash
# 用 SSH 登录（把 x.x.x.x 换成你的服务器公网 IP）
ssh root@x.x.x.x
```

### 3. 一键部署

```bash
# 克隆项目
git clone https://github.com/yinaoran77-sys/agentic-rag-paper-qa.git
cd agentic-rag-paper-qa/deploy

# 给脚本执行权限
chmod +x deploy.sh

# 运行部署脚本（约 10-15 分钟）
./deploy.sh
```

脚本会自动完成：
- ✅ 安装 Docker（如果没有）
- ✅ 启动 PostgreSQL + OpenSearch + Redis + Ollama
- ✅ 构建并启动后端服务
- ✅ 配置 Nginx 反向代理
- ✅ 下载 AI 模型（qwen2.5:3b + nomic-embed-text）

### 4. 访问系统

部署完成后，脚本会输出访问地址：

```
前端页面: http://你的服务器IP
API 文档: http://你的服务器IP/docs
```

把 `http://你的服务器IP` 发给朋友，打开就能用！

---

## 安全组配置

确保阿里云安全组放行了以下端口：

| 端口 | 用途 | 来源 |
|------|------|------|
| 22 | SSH 登录 | 你的 IP |
| 80 | HTTP 访问 | 0.0.0.0/0 |
| 8000 | API 调试（可选） | 你的 IP |

> **注意**：如果安全组没配好，朋友会打不开页面。

---

## 常用管理命令

```bash
cd agentic-rag-paper-qa/deploy

# 查看所有容器状态
docker compose -f docker-compose.prod.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml logs -f

# 只看后端日志
docker compose -f docker-compose.prod.yml logs -f backend

# 重启服务
docker compose -f docker-compose.prod.yml restart

# 停止服务
docker compose -f docker-compose.prod.yml down

# 更新代码后重新部署
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

---

## 配置 HTTPS（可选，推荐）

如果你有自己的域名，可以配 HTTPS：

### 1. 域名解析
把你的域名指向服务器公网 IP。

### 2. 安装 certbot
```bash
sudo apt install certbot python3-certbot-nginx
```

### 3. 申请证书
```bash
sudo certbot --nginx -d yourdomain.com
```

### 4. 修改 nginx.conf
编辑 `deploy/nginx.conf`，把 `listen 80;` 改成 `listen 443 ssl;` 并添加证书路径（certbot 会自动处理）。

---

## 故障排查

### 问题：页面打不开

1. 检查安全组是否放行了 80 端口
2. 检查服务是否运行：`docker compose -f docker-compose.prod.yml ps`
3. 检查 Nginx 日志：`docker logs rag-nginx`

### 问题：AI 回答很慢

1. 检查服务器内存：`free -h`
2. 如果内存不足（< 6GB），换更小的模型：
   ```bash
   docker exec rag-ollama ollama pull qwen2.5:0.5b
   docker exec rag-ollama ollama rm qwen2.5:3b
   ```
   然后修改 `docker-compose.prod.yml` 中的 `OLLAMA_MODEL` 环境变量。

### 问题：上传 PDF 失败

1. 检查磁盘空间：`df -h`
2. 检查后端日志：`docker logs rag-backend`

### 问题：Ollama 模型下载慢

1. 配置镜像加速（阿里云服务器可以用阿里云镜像）
2. 或者手动下载后上传：
   ```bash
   # 在本地下载模型
   docker pull ollama/ollama
   docker run --rm -v ollama:/root/.ollama ollama/ollama pull qwen2.5:3b
   
   # 导出并上传到服务器
   docker volume inspect ollama
   ```

---

## 费用说明

阿里云 ECS 免费试用：
- **免费额度**：300 元
- **有效期**：3 个月
- **配置**：4核8GB
- **流量**：每月 20GB（国内）

超出后按量付费，约 ¥0.5-1/小时（4核8GB 配置）。

如果只是偶尔使用，3 个月免费期完全够用。之后可以：
- 续费继续使用
- 降级到 2核4GB（更便宜）
- 换其他云服务商的免费试用

---

## 升级指南

当你更新了代码，服务器上可以这样更新：

```bash
cd agentic-rag-paper-qa
git pull
cd deploy
docker compose -f docker-compose.prod.yml up -d --build
```

---

**部署完成后，你的朋友只需要一个浏览器就能使用！** 🎉
