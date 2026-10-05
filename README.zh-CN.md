# site-kg

[![ci](https://github.com/CallMeHFK/site-kg/actions/workflows/ci.yml/badge.svg)](https://github.com/CallMeHFK/site-kg/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

给一个网站链接，自动爬取、创建知识图谱，并通过 MCP 接口提供给 AI 访问。

English: [README.md](README.md)

## 工作原理

```
URL ──► 清单探测 ──► 爬取 ──► markdown 语料 ──► graph.json + search.json ──► MCP 服务
        （objects.inv →    （httpx+bs4+html2text，   （链接导出的边、           （search / get_page /
         sitemap.xml → BFS）  遵守 robots.txt、限速）     倒排索引、可行性判定）       neighbors / site_stats）
```

- **清单探测**：Sphinx 站用 `objects.inv` 拿权威页面清单，Doxygen 站用 `navtreeindex0.js`（其 `index.html` 是 JS 空壳、无静态链接），其次 `sitemap.xml`，最后同站 BFS 兜底。不从本地文件名反猜。
- **边只来自语料**：站点没有交叉引用时判定 `NOT-READY`，明说"这是目录树不是图"，绝不为了好看造边。
- **结构层零 LLM**：检索、正文、图遍历全部不需要 key。可选语义层（实体关系抽取，用于问答）在配置 `LLM_API_KEY` 后经 cognee 启用。
- **轮子优先**：MCP 用官方 `mcp` SDK；JS 重站点可选 Crawl4AI 深爬；语义层用 cognee。只有"清单→语料→图谱→MCP"的编排是自研。

## 快速开始

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[crawl]"

# 摄入一个站点（有界）
python -m site_kg.cli ingest https://example.org/docs/index.html --max-pages 200

# streamable HTTP 起 MCP 服务（供网络客户端）
python -m site_kg.cli serve --transport http --port 8766

# 或 stdio（供启动子进程的 agent 宿主）
python -m site_kg.cli serve
```

MCP 客户端配置（streamable HTTP）：

```json
{"mcpServers": {"site-kg": {"url": "http://127.0.0.1:8766/mcp"}}}
```

## MCP 工具

| 工具 | 用途 |
|---|---|
| `ingest_url(url, max_pages, max_depth)` | 爬取+建图；返回 `site_id` 与 `verdict` |
| `list_sites()` | 列出已摄入站点及判定 |
| `site_stats(site_id)` | 文档/边计数、边类型、top hub |
| `search(site_id, query)` | idf 加权全文检索 |
| `get_page(site_id, page_id)` | 完整 markdown 正文+源 URL |
| `neighbors(site_id, page_id, depth)` | 子图扩展（多跳上下文） |
| `render_site(site_id)` | 自包含 HTML 图谱查看器（人工巡检） |

资源：`site://<site_id>/graph` —— 完整图 JSON。

## 实测

在 某嵌入式厂商文档站（静态 Sphinx）上：40 页有界摄入 → 40 文档、32 条 `ref` 边、1709 个索引词、判定 `READY`；七个 MCP 工具全部经真实 streamable-HTTP 会话调用通过（2026-10-06）。这同时证明：原始网站（不只是 wiki 导出）自带足以成图的链接结构。

`ask` 已对内网 vLLM 网关（qwen3.5-122b 语言模型 + bge-m3 嵌入 + bge-reranker-v2-m3 重排序）实测：英文问题返回带引用的分步答案；中文跨语言问题在关键词检索落空时经嵌入兜底正常作答。Doxygen API Reference 站另测一组：navtree 清单 300 页 → 301 文档、527 条 ref 边、READY，`ask` 返回带 4 条引用的正确答案。

## 站点类型覆盖（实测）

| 引擎 | 用到的清单层 | 结果 |
|---|---|---|
| Sphinx（嵌入式厂商指南） | `objects.inv` | READY，40 文档 / 32 边 |
| Doxygen（vendor platform API Reference） | `navtreeindex0.js` | READY，301 文档 / 527 边 |
| MkDocs Material（squidfunk） | `sitemap.xml` | READY，60 文档 / 2801 边 |
| Docusaurus（Checkly） | `sitemap.xml` 索引→子图展平 | READY，60 文档 / 460 边 |
| VitePress（Vue 指南） | `sitemap.xml` | READY，50 文档 / 2402 边 |
| MediaWiki（Arch Wiki） | 同站 BFS | READY，44 文档 / 1201 边 |
| robots 禁止（docs.astral.sh、docusaurus.io） | — | 明确报 `blocked by robots.txt`，绝不静默交空图 |

pretty URL 三形态（尾斜杠、无扩展名、`/title/X`）全部处理；seed 传成叶子目录导致只发现 ≤2 页时返回 `seed_hint` 提示改传文档根。

`ask` 采用**按块嵌入选段**而非截正文开头：同一语料同一问题，修前"信息不足"，修后给出带引用的完整答案（答案段落原本落在长 FAQ 页中部，被截断丢掉了）。

纯客户端渲染的 SPA：抓回来是空框架壳的页面会被识别（空的 `#app`/`#root` 根节点，或可见文本近零且无内链），再用 headless Chromium（Playwright）补渲染。静态抓取永远先跑，只有壳页才付浏览器开销。已用本地 CSR 对照件验证：BFS 能发现 JS 注入的链接、图谱达 READY、渲染后的正文进入语料。

## 常见问题

- **摄入返回 `blocked by robots.txt`**：站点 robots 禁了这个 UA。联系站点所有者，或仅在你有权爬取时才传 `respect_robots=false`。
- **只发现 1–2 页**：seed URL 大概是叶子节点，改传文档根目录（结果里的 `seed_hint` 会明说）。纯客户端渲染的 SPA 壳页由 Playwright 兜底自动处理。
- **必须要 LLM 吗？** 爬取/检索/图遍历都不需要。只有 `ask` 需要 `SITE_KG_LLM_BASE` + `SITE_KG_LLM_KEY`（任意 OpenAI 兼容网关；vLLM/new-api 已实测）。
- **判定 NOT-READY**：该站没有内部交叉引用——是目录树不是图。全文检索和正文仍可用，图遍历如实禁用而不是画噪声。

## 部署

本机优先。免 sudo 的 systemd 用户单元：

```ini
# ~/.config/systemd/user/site-kg.service
[Service]
WorkingDirectory=%h/.qwenpaw/workspaces/default/site-kg
ExecStart=%h/.qwenpaw/workspaces/default/site-kg/.venv/bin/python -m site_kg.cli serve --transport http --port 8766
Restart=on-failure
[Install]
WantedBy=default.target
```

## 路线图

- 语义增强（可选）：配置 `LLM_API_KEY` 后用 cognee `cognify` 在结构边之上叠加实体/关系边。
- 壳页兜底失效的 JS 站点（无限滚动、重反爬）走 Crawl4AI 深爬，经 `.[crawl]` extra 启用。
- ~~复用 an earlier internal prototype 的可视化~~已完成：`render_site` 产出零依赖 canvas 查看器。

## 许可

MIT。爬取内容的版权归原站点所有。
