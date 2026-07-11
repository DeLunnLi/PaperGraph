# PaperGraph 🧠📚

> AI 驱动的开源文献管理与研究助手 —— 你的论文工作流，一站搞定

<p align="center">
  <img src="./screenshots/hero-screenshot.png" alt="PaperGraph Screenshot" width="800">
</p>

<p align="center">
  <a href="#-在线演示"><img src="https://img.shields.io/badge/🚀_在线演示-Live_Demo-brightgreen" alt="Live Demo"></a>
  <a href="#-快速开始"><img src="https://img.shields.io/badge/Docker-一键部署-2496ED?logo=docker&logoColor=white" alt="Docker"></a>
  <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Vue-3-green?logo=vue.js&logoColor=white" alt="Vue">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
  <img src="https://img.shields.io/github/stars/delunli/papergraph?style=social" alt="Stars">
</p>

<p align="center">
  <b>中文</b> | <a href="./README_EN.md">English</a>
</p>

---

## ✨ 核心功能

<table>
<tr>
<td width="50%">

### 🔍 多源智能搜索
同时搜索 **arXiv + OpenAlex + DBLP**，智能去重排序
- 自然语言查询，自动解析搜索意图
- 支持作者、年份、期刊多维度筛选
- Tavily 预搜索增强召回

</td>
<td width="50%">

### 🤖 AI 阅读助手
内置 LLM 深度理解论文内容
- 一键生成论文摘要和方法总结
- 智能问答："这篇论文的创新点是什么？"
- 支持 DeepSeek/OpenAI/本地模型

</td>
</tr>
<tr>
<td width="50%">

### 🕸️ 知识图谱可视化
自动构建文献引用网络
- 发现相关研究和引用关系
- 追踪研究脉络和方法演进
- 交互式图谱探索

</td>
<td width="50%">

### 🔒 数据完全自主
你的文献库，永远属于你
- 本地 SQLite 存储，无需联网
- 支持自托管部署
- 开放格式导出（JSON/BibTeX）

</td>
</tr>
</table>

---

## 🎬 功能演示

<p align="center">
  <img src="./screenshots/demo-search.gif" alt="Search Demo" width="750">
  <br>
  <i>🔍 智能搜索：输入自然语言，自动检索多源学术数据库</i>
</p>

<p align="center">
  <img src="./screenshots/demo-reader.gif" alt="Reader Demo" width="750">
  <br>
  <i>📖 AI 阅读助手：边读边问，AI 帮你理解论文细节</i>
</p>

---

## 🚀 快速开始

### 方式一：Docker 一键部署（推荐）

```bash
# 克隆仓库
git clone https://github.com/delunli/papergraph.git
cd PaperGraph

# 启动服务
docker-compose up -d

# 打开浏览器访问
open http://localhost:5173
```

### 方式二：本地开发环境

<details>
<summary>点击查看详细步骤</summary>

```bash
# 1. 后端启动
cd backend
conda create -n papergraph python=3.11
conda activate papergraph
pip install -r requirements.txt
python run.py

# 2. 前端启动（新终端）
cd frontend
npm install
npm run dev

# 3. 访问 http://localhost:5173
```

</details>

### 配置 LLM（可选）

```bash
# 在 backend/.env 中添加你的 API Key
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

> 💡 支持 DeepSeek、OpenAI、Azure OpenAI 等多种模型，也可使用本地 Ollama

---

## 📊 为什么选择 PaperGraph？

| 特性 | PaperGraph | Zotero | Mendeley | SciSpace |
|------|:----------:|:------:|:--------:|:--------:|
| **AI 智能搜索** | ✅ 内置 | ❌ 无 | ❌ 无 | ✅ 有 |
| **AI 阅读助手** | ✅ 内置 | ❌ 插件 | ❌ 无 | ✅ 有 |
| **知识图谱** | ✅ 内置 | ❌ 无 | ❌ 无 | ⚠️ 有限 |
| **数据主权** | ✅ 本地优先 | ⚠️ 可选 | ❌ 云端 | ❌ SaaS |
| **开源免费** | ✅ 完全 | ✅ 是 | ❌ 商业 | ❌ 订阅 |
| **自托管** | ✅ 完整 | ❌ 无 | ❌ 无 | ❌ 无 |
| **多源聚合** | ✅ 5+ 源 | ⚠️ 插件 | ⚠️ 有限 | ⚠️ 有限 |

---

## 🏗️ 技术架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────────────┐
│   Vue 3     │────▶│  FastAPI    │────▶│  Multi-Source Search        │
│  Frontend   │◀────│   Backend   │◀────│  (arXiv/OpenAlex/DBLP/...)  │
└─────────────┘     └─────────────┘     └─────────────────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  AI Agent Layer │
                    │ (Search/Reader/ │
                    │  Knowledge Graph)│
                    └─────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  Local SQLite   │
                    │  (Your Data)    │
                    └─────────────────┘
```

### 技术亮点

- **异步架构**：基于 FastAPI + AnyIO，高性能并发
- **模块化 AI**：支持多 LLM 提供商，可插拔设计
- **智能缓存**：TTL 缓存策略，响应速度 < 500ms
- **混合检索**：RRF 融合 + 语义重排序 + LLM 精排

---

## 🗺️ 路线图

### 已实现 ✅
- [x] 多源学术搜索（arXiv/OpenAlex/DBLP）
- [x] AI 阅读助手（问答/总结）
- [x] 知识图谱可视化
- [x] 文献库管理（标签/分类/搜索）
- [x] Daily arXiv 订阅
- [x] MCP 协议支持

### 进行中 🚧
- [ ] 插件系统架构
- [ ] 团队协作功能
- [ ] 移动端适配
- [ ] 更多数据源（IEEE/PubMed/CNKI）

### 规划中 📋
- [ ] 文献综述自动生成
- [ ] 研究趋势分析
- [ ] 学术社交网络
- [ ] 浏览器插件

查看详细路线图：[ROADMAP.md](./ROADMAP.md)

---

## 🤝 参与贡献

我们欢迎所有形式的贡献！

```bash
# 1. Fork 本仓库
# 2. 创建你的分支
git checkout -b feature/amazing-feature

# 3. 提交改动
git commit -m 'Add amazing feature'

# 4. 推送到分支
git push origin feature/amazing-feature

# 5. 发起 Pull Request
```

查看 [CONTRIBUTING.md](./CONTRIBUTING.md) 了解详细规范。

### 贡献者

<a href="https://github.com/delunli/papergraph/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=delunli/papergraph" />
</a>

---

## 💬 加入社区

- 💬 **Discord**：[加入讨论](https://discord.gg/your-link)
- 🐦 **Twitter/X**：[@papergraph](https://twitter.com/papergraph)
- 📧 **Email**：contact@papergraph.dev
- 💼 **微信公众号**：PaperGraph（扫码关注）

<p align="center">
  <img src="./screenshots/wechat-qr.png" alt="WeChat" width="150">
</p>

---

## 📄 许可证

本项目基于 [MIT License](./LICENSE) 开源。

---

## 🌟 Star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=delunli/papergraph&type=Date)](https://star-history.com/#delunli/papergraph&Date)

---

<p align="center">
  如果这个项目对你有帮助，请给我们一个 ⭐️ Star！
  <br>
  <i>你的支持是我们持续开发的动力 ❤️</i>
</p>

<p align="center">
  <a href="https://github.com/delunli/papergraph">
    <img src="https://img.shields.io/badge/Star-这个项目-FFD700?style=for-the-badge&logo=github" alt="Star">
  </a>
</p>
