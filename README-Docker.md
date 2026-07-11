# PaperGraph — 快速部署指南

## 方式一:Docker 一键部署(推荐)

### 前置条件
- 安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)

### 步骤

```bash
# 1. 克隆项目
git clone <repo-url> PaperGraph
cd PaperGraph

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env,至少填入 LLM_API_KEY 和 LLM_BASE_URL

# 3. 启动
docker compose up -d --build

# 4. 访问
# 前端: http://localhost:5173
# 后端: http://localhost:8000/health

# 5. 首次使用
# 打开 http://localhost:5173 → 登录(用户名 default,密码 default)
# 或注册新账号
```

### 常用命令

```bash
# 查看日志
docker compose logs -f backend

# 停止
docker compose down

# 更新代码后重新构建
docker compose up -d --build

# 查看状态
docker compose ps
```

### 数据持久化

数据存储在 Docker volume 中:
- `papergraph-data` — SQLite 数据库(papers.db)
- `papergraph-downloads` — 下载的 PDF 文件

备份数据:
```bash
docker compose exec backend cp /app/data/papers.db /backup/papers.db
```

---

## 方式二:本地开发部署

### 前置条件
- Python 3.11+
- Node.js 18+
- conda 或 venv

### 步骤

```bash
# 1. 后端
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 编辑填入 LLM_API_KEY
python run.py

# 2. 前端(新终端)
cd frontend
npm install
npm run dev

# 3. 访问 http://127.0.0.1:5173
```

---

## 环境变量说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `LLM_API_KEY` | ✅ | LLM API 密钥 |
| `LLM_BASE_URL` | ✅ | LLM API 地址(OpenAI 兼容) |
| `LLM_MODEL_ID` | ✅ | 模型名称 |
| `LLM_DISABLE_PROXY` | 可选 | 设为 1 禁用代理 |
| `PAPERGRAPH_JWT_SECRET` | 可选 | JWT 密钥,不填自动生成 |
| `TAVILY_API_KEY` | 可选 | Tavily 预搜索 |
| `EMBED_API_KEY` | 可选 | Embedding(记忆向量化) |

## 支持的 LLM

任何 OpenAI 兼容接口:
- DeepSeek: `LLM_BASE_URL=https://api.deepseek.com/v1`
- OpenAI: `LLM_BASE_URL=https://api.openai.com/v1`
- 通义千问: `LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`
- AIHubMix: `LLM_BASE_URL=https://aihubmix.com/v1`
- 本地 Ollama: `LLM_BASE_URL=http://localhost:11434/v1`
