# PaperGraph（知脉）🧠📚

<div align="center">

**AI 驱动的开源文献管理与研究助手**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-green.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue-3.x-brightgreen.svg" alt="Vue">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
  <a href="https://github.com/DeLunnLi/PaperGraph/stargazers">
    <img src="https://img.shields.io/github/stars/DeLunnLi/PaperGraph?style=social" alt="Stars">
  </a>
</p>

<p align="center">
  <a href="#-快速开始">🚀 快速开始</a> •
  <a href="#-功能演示">📸 功能演示</a> •
  <a href="#-为什么选择-papergraph">🎯 为什么选择</a> •
  <a href="#-技术架构">🏗️ 架构</a> •
  <a href="#-加入社区">💬 社区</a>
</p>

*把找论文、读论文、管理论文和沉淀研究脉络串成一个连续工作流*

</div>

---

## ✨ 核心亮点

<table>
<tr>
<td width="25%" align="center">

### 🔍 智能搜索
多源聚合搜索  
arXiv + OpenAlex + DBLP

</td>
<td width="25%" align="center">

### 🤖 AI 阅读
PDF 智能问答  
方法总结 + 引用追踪

</td>
<td width="25%" align="center">

### 📚 文献管理
本地优先存储  
标签分类 + 阅读日历

</td>
<td width="25%" align="center">

### 🕸️ 知识图谱
可视化关系网络  
研究脉络一目了然

</td>
</tr>
</table>

---

## 🚀 快速开始

### 方式一：Docker 一键部署（推荐）

```bash
git clone https://github.com/DeLunnLi/PaperGraph.git
cd PaperGraph
docker-compose up -d
# 访问 http://localhost:5173
```

### 方式二：本地开发

```bash
# 后端
cd backend && pip install -r requirements.txt && python run.py

# 前端
cd frontend && npm install && npm run dev
```

<details>
<summary>📖 详细配置说明</summary>

在 `backend/.env` 中配置 LLM：
```env
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_ID=deepseek-chat
```

支持：DeepSeek / OpenAI / Azure OpenAI / Ollama 本地模型

</details>

---

## 📸 功能演示

<p align="center">
  <img src="./screenshots/search-agent-results.png" alt="文献搜索" width="700">
  <br>
  <i>🔍 自然语言搜索，多源召回智能排序</i>
</p>

<p align="center">
  <img src="./screenshots/paper-reader-assistant.png" alt="阅读助手" width="700">
  <br>
  <i>📖 AI 阅读助手：问答、总结、引用追踪</i>
</p>

<p align="center">
  <img src="./screenshots/knowledge-graph.png" alt="知识图谱" width="700">
  <br>
  <i>🕸️ 知识图谱：可视化论文关系网络</i>
</p>

---

## 🎯 为什么选择 PaperGraph？

| 特性 | PaperGraph | Zotero | Mendeley | SciSpace |
|:------|:----------:|:------:|:--------:|:--------:|
| **AI 智能搜索** | ✅ 内置 | ❌ 需插件 | ❌ 无 | ✅ 有 |
| **AI 阅读助手** | ✅ 内置 | ❌ 无 | ❌ 无 | ✅ 有 |
| **知识图谱** | ✅ 内置 | ❌ 无 | ❌ 无 | ⚠️ 有限 |
| **数据主权** | ✅ 本地优先 | ⚠️ 可选本地 | ❌ 云端锁定 | ❌ SaaS |
| **开源免费** | ✅ 完全开源 | ✅ 开源 | ❌ 商业 | ❌ 订阅制 |
| **自托管** | ✅ 完整支持 | ❌ 不支持 | ❌ 不支持 | ❌ 不支持 |
| **多源聚合** | ✅ 5+ 数据源 | ⚠️ 需配置 | ⚠️ 有限 | ⚠️ 有限 |

**PaperGraph 的核心优势：**
- 🔒 **数据完全自主** —— 本地存储，无需担心隐私泄露或服务关停
- 🤖 **AI 原生设计** —— 从搜索到阅读，AI 能力贯穿全流程
- 🔌 **可扩展架构** —— 支持自定义数据源和 LLM 模型
- 💰 **永久免费** —— 开源 MIT 协议，无任何功能限制

---

## 🏗️ 技术架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────────────┐
│   Vue 3     │◀───▶│  FastAPI    │◀───▶│  Multi-Source Search        │
│  Frontend   │     │   Backend   │     │  (arXiv/OpenAlex/DBLP/...)  │
└─────────────┘     └─────────────┘     └─────────────────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  AI Agent Layer │
                    │ (Search/Reader/ │
                    │  Knowledge)     │
                    └─────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  Local SQLite   │
                    │  (Your Data)    │
                    └─────────────────┘
```

### 三类智能体

| 智能体 | 职责 | 核心能力 |
|:-------|:-----|:---------|
| **SearchAgent** | 文献搜索 | 意图解析、多源召回、LLM 精排 |
| **PaperAnalysisAgent** | 论文分析 | 摘要生成、阅读问答、引用查找 |
| **KnowledgeGraphAgent** | 知识图谱 | 关系抽取、图谱构建、可视化 |

### 技术栈

- **后端**：FastAPI + SQLite + Pydantic
- **前端**：Vue 3 + Vite + Ant Design Vue + PDF.js
- **AI**：支持 DeepSeek / OpenAI / Azure / Ollama
- **数据源**：arXiv + OpenAlex + DBLP + Tavily

---

## 💬 加入社区

- 💬 **Discord**：[加入讨论](https://discord.gg/your-link)（国际用户）
- 🐦 **Twitter/X**：[@PaperGraph](https://twitter.com/papergraph)
- 📧 **Email**：contact@papergraph.dev

<p align="center">
  <img src="./screenshots/wechat-qr.png" alt="微信群" width="150">
  <br>
  <i>扫码加入微信群（国内用户）</i>
</p>

---

## 📖 详细功能

### 1. 自然语言文献搜索

输入研究问题、论文标题、作者或会议线索，系统自动：
- 解析搜索意图生成 SearchRecipe
- 并行检索 arXiv、DBLP、OpenAlex、Tavily
- 智能去重、过滤、排序
- 流式返回阶段状态和工具调用摘要

### 2. 论文阅读助手

- PDF 正文抽取与结构化解析
- AI 导读：自动生成摘要和方法总结
- 智能问答：围绕方法、实验、局限继续追问
- 参考文献查找与上下文辅助
- 表格内容提取与问答

### 3. 每日论文推荐

- 根据兴趣选择 arXiv 分类
- 个性化候选论文筛选
- 生成推荐理由
- 支持反馈优化推荐

### 4. 我的文献库

- 论文保存与 PDF 下载
- 分类管理与标签系统
- 阅读记录与进度追踪
- 阅读日历可视化

### 5. 知识图谱构建

- 从已保存论文抽取主题、方法、引用关系
- 交互式图谱可视化
- 节点详情解释
- 研究脉络追踪

---

## 🗺️ 开发路线图

### v1.0（当前）✅
- [x] 自然语言文献搜索
- [x] 多源召回与 LLM 精排
- [x] PDF 阅读助手
- [x] 每日论文推荐
- [x] 我的文献库与阅读日历
- [x] 知识图谱可视化

### v1.1（进行中）🚧
- [ ] Docker 一键部署优化
- [ ] 搜索结果缓存
- [ ] 知识图谱编辑与导出
- [ ] 移动端适配

### v2.0（规划中）📋
- [ ] 插件系统架构
- [ ] 团队协作功能
- [ ] 向量语义检索
- [ ] 浏览器插件

---

## 🤝 参与贡献

⭐ 如果这个项目对你有帮助，请给我们一个 Star！

🐛 发现问题？提交 [Issue](https://github.com/DeLunnLi/PaperGraph/issues)

🔀 想贡献代码？查看 [CONTRIBUTING.md](./CONTRIBUTING.md)

### 贡献者

<a href="https://github.com/DeLunnLi/PaperGraph/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=DeLunnLi/PaperGraph" alt="Contributors" />
</a>

---

## 📄 许可证

[MIT License](./LICENSE)

---

## 🙏 致谢

感谢 Datawhale 社区和 Hello-Agents 项目的技术支持。

---

<p align="center">
  <b>Star 趋势</b>
</p>

<p align="center">
  <a href="https://star-history.com/#DeLunnLi/PaperGraph&Date">
    <img src="https://api.star-history.com/svg?repos=DeLunnLi/PaperGraph&type=Date" alt="Star History">
  </a>
</p>

<p align="center">
  <a href="https://github.com/DeLunnLi/PaperGraph">
    <img src="https://img.shields.io/badge/⭐_Star_这个项目-ff69b4?style=for-the-badge" alt="Star">
  </a>
</p>
