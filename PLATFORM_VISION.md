# PaperGraph - 开源学术文献平台愿景

## 核心定位

> **"学术界的 WordPress"** —— 一个开源、可扩展、数据自主的文献管理与研究协作平台

### 与现有平台的根本区别

| 维度 | Zotero/Mendeley | SciSpace/Elicit | PaperGraph (目标) |
|------|-----------------|-----------------|-------------------|
| **数据主权** | 供应商锁定 | SaaS 封闭 | 用户完全拥有数据 |
| **扩展性** | 有限插件 | 无 | 插件生态 + API |
| **AI 集成** | 基础 | 深度但封闭 | 开放模型 + 可替换 |
| **协作** | 基础共享 | 无 | 研究组协作空间 |
| **成本** | 免费/付费 | 订阅制 | 完全免费开源 |

## 平台架构设计

### 1. 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                     应用层 (Applications)                    │
│  Web UI │ Desktop App │ Mobile App │ Browser Extension     │
├─────────────────────────────────────────────────────────────┤
│                     平台层 (Platform Core)                   │
│  文献管理 │ 搜索聚合 │ AI 引擎 │ 知识图谱 │ 协作空间         │
├─────────────────────────────────────────────────────────────┤
│                     扩展层 (Extensions)                      │
│  插件系统 │ 主题市场 │ 工作流模板 │ 数据源适配器            │
├─────────────────────────────────────────────────────────────┤
│                     数据层 (Data Layer)                      │
│  本地 SQLite │ 远程 PostgreSQL │ 对象存储 │ IPFS(可选)      │
└─────────────────────────────────────────────────────────────┘
```

### 2. 核心平台能力

#### A. 数据自主 (Data Sovereignty)
```yaml
数据所有权:
  - 本地优先: 所有数据默认存储在本地 SQLite
  - 开放格式: 数据导出为标准 JSON/BibTeX/CSL-JSON
  - 无供应商锁定: 随时可迁移到自建服务器
  - 加密同步: 端到端加密的多设备同步

存储适配器:
  - LocalStorage: 本地文件系统
  - S3Compat: MinIO/AWS S3/阿里云 OSS
  - WebDAV: Nextcloud/坚果云
  - IPFS: 去中心化存储 (实验性)
```

#### B. 插件生态系统
```yaml
插件类型:
  数据源:
    - ieee-xplore-adapter
    - pubmed-adapter
    - semantic-scholar-adapter
    - cnki-adapter (中文文献)
    
  AI 模型:
    - openai-provider
    - local-llm-provider (Ollama)
    - azure-openai-provider
    - custom-api-provider
    
  导出格式:
    - latex-citation-plugin
    - word-plugin
    - notion-export-plugin
    - markdown-export-plugin
    
  工作流:
    - auto-download-pdf
    - smart-tagging
    - citation-alert
    - reading-schedule

插件市场:
  - 官方认证插件
  - 社区贡献插件
  - 评分系统
  - 一键安装
```

#### C. 多租户与协作
```yaml
部署模式:
  个人版 (Personal):
    - 单用户
    - 本地 SQLite
    - 可选云同步
    
  团队版 (Team):
    - 多用户协作
    - PostgreSQL 后端
    - 共享文献库
    - 权限管理
    
  机构版 (Institution):
    - SSO/LDAP 集成
    - 机构知识库
    - 统计分析仪表板
    - API 限额管理

协作功能:
  - 共享文献集合
  - 批注与讨论
  - 阅读进度同步
  - 协作笔记
  - 引用推荐
```

## 技术路线图

### Phase 1: 核心平台 (当前 - 3个月)
- [ ] 完成个人版核心功能
- [ ] 设计插件 API 规范
- [ ] 实现 3 个官方数据源插件
- [ ] 文档站点建设

### Phase 2: 生态启动 (3-6个月)
- [ ] 发布插件 SDK
- [ ] 建立插件市场
- [ ] 团队版多租户支持
- [ ] 移动端 PWA 应用

### Phase 3: 社区扩张 (6-12个月)
- [ ] 机构版发布
- [ ] 学术社交网络功能
- [ ] 第三方集成生态 (Notion/Obsidian)
- [ ] 基金会/赞助体系建立

## 差异化功能规划

### 1. 开放知识图谱
```markdown
不只是引用关系，而是构建开放的学术图谱:

- 实体抽取: 方法、数据集、基准测试结果
- 关系推理: 谁改进了谁的方法
- 趋势分析: 领域发展方向预测
- 开放 API: 允许第三方查询图谱数据

对标: Connected Papers + Semantic Scholar，但是开源的
```

### 2. 可替换 AI 引擎
```markdown
用户可以选择:
- 云端 API: OpenAI/Claude/DeepSeek
- 本地模型: Ollama/Llama/Mistral
- 混合模式: 敏感数据本地处理，其他云端处理

对比 SciSpace/Elicit 的封闭 AI，这是核心卖点
```

### 3. 研究工作流引擎
```markdown
可编程的研究自动化:

示例工作流:
1. 每周一自动抓取 arXiv CS.AI 新论文
2. 筛选与我研究相关的 (基于关键词+AI)
3. 生成每周阅读清单
4. 发送邮件通知

可视化工作流编辑器 + 社区模板市场
```

### 4. 开放数据贡献
```markdown
用户可以选择贡献匿名化的学术数据:
- 引用模式
- 阅读行为
- 标注数据

形成一个开放的学术数据集，反哺研究社区
```

## 社区治理模式

### 组织架构
```
PaperGraph Project
├── Core Team (核心维护者)
│   ├── 项目发起人 (你)
│   ├── 核心开发者
│   └── 社区经理
│
├── Special Interest Groups (SIGs)
│   ├── SIG-Plugins (插件生态)
│   ├── SIG-AI (AI 功能)
│   ├── SIG-UX (用户体验)
│   └── SIG-I18n (国际化)
│
└── Contributors
    ├── Code Contributors
    ├── Plugin Developers
    ├── Documentation Writers
    └── Translators
```

### 决策流程
```markdown
1. RFC 提案: 重大功能通过 RFC 讨论
2. 社区投票: 关键决策社区参与
3. 核心团队: 日常维护决策
4. 外部贡献: PR 欢迎，遵循贡献者公约
```

## 可持续运营

### 资金来源
```yaml
可持续开源模式:
  捐赠:
    - GitHub Sponsors
    - Open Collective
    - 爱发电 (国内)
    
  商业服务:
    - 托管版 (Managed Hosting)
    - 企业支持合同
    - 定制开发服务
    
   grants:
    - 学术机构资助
    - 开源基金会 (Apache/NumFOCUS)
```

### 成功指标
```yaml
6个月目标:
  - GitHub Stars: 5,000+
  - 活跃用户: 1,000+
  - 插件数量: 20+
  - 贡献者: 50+

12个月目标:
  - GitHub Stars: 15,000+
  - 活跃用户: 5,000+
  - 插件数量: 100+
  - 商业客户: 5+
```

## 立即行动清单

### 本周
- [ ] 创建 `ROADMAP.md` 公开路线图
- [ ] 设计项目 Logo 和品牌色
- [ ] 创建 Discord/微信群 社区
- [ ] 写博客宣布愿景：《我要做一个开源的学术平台》

### 本月
- [ ] 发布 v0.2.0: 稳定的核心功能
- [ ] 设计插件 API 接口草案
- [ ] 招募 2-3 名核心贡献者
- [ ] 建立文档站点 (Docusaurus)

### 本季度
- [ ] 实现第一个插件系统原型
- [ ] 支持 3 个新数据源
- [ ] 在学术社区发布 (知乎/V2EX/Twitter)
- [ ] 申请加入开源基金会

## 对标学习

### 成功的开源平台案例
```markdown
1. Obsidian
   - 核心免费，增值收费
   - 强大的插件生态
   - 活跃社区

2. Cal.com
   - 开源替代 Calendly
   - 清晰的商业模式
   - 良好的文档

3. PostHog
   - 开源产品分析
   - 团队/企业版收费
   - 高质量技术博客

4. Zotero
   - 非营利组织运营
   - 学术社区认可
   - 基金会资助模式
```

## 结语

PaperGraph 不应该只是一个工具，而应该成为：

> **学术工作者数字工作流的开放基础设施**

这意味着：
- 数据是用户的，不是平台的
- 功能是可扩展的，不是封闭的
- 社区是共治的，不是独断的
- 发展是可持续的，不是烧钱的

这个愿景足够宏大，值得开发者投入，也值得用户信赖。
