"""Prompt templates for deep search: query decomposition, iteration expansion, synthesis."""

DEEP_SEARCH_DECOMPOSE_PROMPT = """你是学术文献检索专家。用户希望对以下主题进行深度检索，请将原始查询分解为 {max_subqueries} 个互补的子问题，用于多角度并行召回学术论文。

## 原始查询
{user_query}

## 上下文（意图解析结果）
- 关键词: {keywords}
- 会议/期刊约束: {venues}
- 年份范围: {year_from}-{year_to}
- 排序偏好: {sort}

## 分解原则
1. **互补不重叠**：每个子问题覆盖原始查询的不同侧面（方法/应用/理论/评测/对比）
2. **可检索**：子问题应是学术搜索引擎能返回结果的短语或问句，5-15 词
3. **英文化**：输出英文子问题（学术库以英文为主）
4. **覆盖广度**：包含 1 个综述性子问题、1 个最新进展子问题、其余为具体技术点
5. **数量上限**：不超过 {max_subqueries} 个

## 示例
原始: "扩散模型在图像生成中的最新进展"
分解:
1. "diffusion models image generation survey"
2. "score-based generative models recent advances 2024"
3. "latent diffusion models high-resolution synthesis"
4. "diffusion model sampling efficiency acceleration"

## 输出格式（只输出 JSON）
{{
  "sub_queries": ["子问题1", "子问题2", ...],
  "rationale": "分解理由（一句话）"
}}"""

DEEP_SEARCH_EXPAND_PROMPT = """你正在协助深度学术检索。已进行 {round} 轮检索，累积 {accumulated} 篇候选。判断是否需要追加新的子问题以补充覆盖盲区。

## 原始查询
{user_query}

## 已用子问题
{used_subqueries}

## 已检索论文标题样本（前10篇）
{sample_titles}

## 判断原则
- 若已覆盖主要方法/应用维度且候选数充足（≥{threshold}）→ 返回 done=true
- 若有明显盲区（如某子方向无结果、缺少经典论文、缺少对比方法）→ 返回新子问题
- 新子问题不超过 {max_new} 个，且不与已用子问题重复

## 输出格式（只输出 JSON）
{{
  "done": false,
  "new_sub_queries": ["新子问题1", ...],
  "reason": "判断理由"
}}"""

DEEP_SEARCH_SYNTHESIS_PROMPT = """你是学术综述撰写专家。根据以下检索到的论文，为用户撰写一段结构化综述（300-500字），帮助用户快速理解该领域全貌。

## 用户查询
{user_query}

## 检索到的论文（按相关性排序）
{papers_with_abstracts}

## 撰写要求
1. 开头一句话总结领域现状
2. 按 2-4 个技术脉络分组介绍代表性工作（引用论文标题或作者+年份）
3. 指出当前趋势与可能的空白
4. 中文撰写，术语保留英文
5. 不得虚构未列出的论文

## 输出
直接输出综述段落（Markdown）。"""
