# site-kg

[![ci](https://github.com/CallMeHFK/site-kg/actions/workflows/ci.yml/badge.svg)](https://github.com/CallMeHFK/site-kg/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

给一个网站链接或本地目录，自动把文档**和代码**建成知识图谱，并通过 MCP 接口提供给 AI 访问。

English: [README.md](README.md)

## 工作原理

```
URL ──► 清单探测 ──► 爬取 ──► markdown 语料 ──► graph.json + search.json ──► MCP 服务
        （objects.inv →    （httpx+bs4+html2text，   （链接导出的边、           （search / get_page /
         sitemap.xml → BFS）  遵守 robots.txt、限速）     倒排索引、可行性判定）       neighbors / site_stats）

本地目录 ──► 遍历（识别 .gitignore） ──► 同一语料格式 ──► 同一图谱 + MCP
            （md/txt/wiki 文档 + 代码文件；
             边：md 链接、[[wikilink]]、import 依赖、README hub）
```

- **清单探测**：Sphinx 站用 `objects.inv` 拿权威页面清单，Doxygen 站用 `navtreeindex0.js`（其 `index.html` 是 JS 空壳、无静态链接），其次 `sitemap.xml`，最后同站 BFS 兜底。不从本地文件名反猜。
- **边只来自语料**：站点没有交叉引用时判定 `NOT-READY`，明说"这是目录树不是图"，绝不为了好看造边。
- **结构层零 LLM**：检索、正文、图遍历全部不需要 key。可选问答层 `ask` 走任意 OpenAI 兼容网关（`SITE_KG_LLM_BASE` + `SITE_KG_LLM_KEY`）。
- **轮子优先**：MCP 用官方 `mcp` SDK；JS 重站点可选 Crawl4AI 深爬；语义层用 cognee。只有"清单→语料→图谱→MCP"的编排是自研。

## 快速开始

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[crawl]"

# 摄入一个站点（有界）
python -m site_kg.cli ingest https://example.org/docs/index.html --max-pages 200

# 或摄入本地目录（wiki 文档 + 代码仓库）
python -m site_kg.cli ingest-local /path/to/repo-or-wiki

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
| `ingest_url(url, max_pages, max_depth, relations)` | 爬取+建图；返回 `site_id` 与 `verdict` |
| `ingest_local(path, max_files, respect_gitignore, relations)` | 本地目录（文档+代码）→ 图；产出同上 |
| `list_sites()` | 列出已摄入站点及判定 |
| `site_stats(site_id)` | 文档/边计数、边类型、top hub |
| `search(site_id, query)` | idf 加权全文检索 |
| `get_page(site_id, page_id)` | 完整 markdown 正文+源 URL |
| `neighbors(site_id, page_id, depth)` | 子图扩展（多跳上下文） |
| `ask(site_id, question)` | LLM 带引用问答（需配置 LLM） |
| `render_site(site_id)` | 自包含 **3D** Three.js 图谱查看器（人工巡检） |
| `render_build(site_id)` | 自包含 **WebGPU** 构建回放查看器：图谱现场"生长"——节点按语料顺序降生到章环上，随后证据边（ref/imports/contains），再按每个派生关系维度各飞来一波；每条边从源节点射出并绑定到目标节点（带脉冲），HUD 实时计数节点/边/各维度边。倍速 0.25×–16×、重播/暂停/跳过。需支持 WebGPU 的浏览器 |

资源：`site://<site_id>/graph` —— 完整图 JSON。

## 本地目录（文档 + 代码）

`ingest_local` 把本地目录遍历成与爬虫完全同构的语料，下游所有工具（search/get_page/neighbors/ask/render）无需改动即可用。

- **文档**（`.md/.markdown/.mdx/.txt/.wiki/.rst/.adoc`）正文原样保留。边来自相对 markdown 链接（含无扩展名与根绝对路径形式）和 `[[wikilink]]`（Obsidian/MediaWiki 风格，大小写/下划线不敏感按基名匹配）。解析不了的引用计入 `dropped_refs`，绝不成边。
- **代码**（37 种扩展名：py、js/ts、go、rust、c/c++、java/kotlin、ruby…… 外加内核/构建生态——yaml、汇编 `.S/.s`、perl、awk、链接脚本、makefile 片段、设备树 `.dts/.dtsi`）生成可检索文档，正文开头是散文头（路径、语言、检测到的顶层符号），标识符因此能进倒排索引。知名无扩展名构建文件（`Makefile`、`Kconfig`、`Kbuild`、`COPYING`、`LICENSE`、`README`、`MAINTAINERS` 等）按基名入图，同样带散文头。边来自真实依赖语句：Python `import`/`from…import`（含相对导入与括号折行）、JS/TS `import`/`require`、C/C++ 引号 `#include`、Go `import`（识别 go.mod）、Rust `mod`/`use crate::`、JVM `import`、Ruby `require_relative`。
- **README/index 是 hub**：向同目录成员发 `contains` 边，与站点侧 hub 语义一致。
- **遵循 `.gitignore`**（经 `pathspec`），并内置噪音清单（`node_modules`、`__pycache__`、lockfile、`*.min.js` 等）；>512 KB 与二进制文件跳过。每项跳过都有归因而非静默：`scan_stats` 分别计数 `skipped_noise_file` / `skipped_ext` / `skipped_oserror`（三者之和仍记在 `skipped_kind`，老消费者不变），NOISE_DIRS 剪枝额外报告 `pruned_noise_dirs` 与被剪路径样本——无法对账的覆盖率声明就是错声明。
- **正文分桶存储，不搞单文件**：`data/sites/<site_id>/docs/<bucket>.json` 为 1–256 个 sha 分桶（数量随语料规模分层，记入 `meta.json` 的 `docBuckets`），无论语料形状如何 `get_page` 只读一个小分桶（实测 7.5 万文档的内核树：整份 975MB `docs.json` 读取 8.3 秒 → 分桶后冷读约 12ms；`drivers/` 这类 3 万文件的章也不会再造出巨大分片）。分桶前的旧站仍带 `docs.json`，读取侧透明兼容。重新摄入时同一遍替换全部分桶并删除旧单文件。

环境变量（调用时读取，常驻 MCP 服务无需重启即可生效）：

| 变量 | 效果 |
|---|---|
| `SITE_KG_MAX_BYTES` | 单文件大小上限，单位字节（默认 512 KiB） |
| `SITE_KG_NOISE_EXEMPT` | 逗号分隔的 posix 相对路径前缀，其目录豁免 NOISE_DIRS 剪枝（如 `drivers/target`——真实源码目录会撞上噪音名） |
| `SITE_KG_RELATIONS` | 衍生关系维度（逐节点动态选择）：`auto`（默认）/ `llm` / `all` / `off` / 逗号列表，见 [关系维度](#关系维度) |
- **PageRank** 跑在边表上（aider repo-map 式），落到每个节点的 `r` 字段，`site_stats`/查看器可据此排出结构中心文件。

设计取舍（调研 aider、Serena、CocoIndex、gitingest、GraphRAG、LightRAG、cognee、khoj 及 GraphCoder/CGM 论文后）：结构层坚持**零 LLM、诚实边**——每条边都能回溯到显式链接或 import 语句。LLM 抽取实体/关系边（GraphRAG/LightRAG 路线）换来召回但引入幻觉边与构建成本，因此只放在可选 cognee 语义层。编译器级精度（LSP/SCIP、Serena 路线）与 tree-sitter 符号图列入路线图，作为可选 extra 而非硬依赖。

实测（2026-10-09，自举本仓库）：22 个文件（7 文档 + 15 代码）→ 30 条 `imports` + 2 条 `ref` + 2 条 `contains`，判定 `READY`；按标识符检索（`pagerank`、`gitignore`）命中正确源文件。

实测（2026-10-13，语料规模级：DriveOS SDK 源码树 115,598 文档 / 7 站）：逐站重摄入，报告数字与摄入前完全一致；最大站（75,415 文档）`get_page` 冷读约 12ms（单文件格式同一页面需 8.3s）。分片/meta 缓存以文件 mtime+size 为键，任何进程（cli、爬虫任务、另一个 agent）重摄入后下一次读到的就是新数据，无需失效钩子；所有写入走临时文件 + `os.replace`，重摄入期间 4 线程并发读零撕裂。

## 实时查看器

`site-kg live` 启动本地"地址优先"的实时会话：先给地址，再在浏览器里选场景。

```bash
# URL：打印回环地址，启动器已预填该地址
python -m site_kg.cli live https://docs.example.com

# 带场景跳过启动器：爬取 -> 流式建图 -> 沉降一次完成
python -m site_kg.cli live https://docs.example.com --scene neural

# 本地文档/代码目录
python -m site_kg.cli live --local ./docs --scene planets

# 回放已摄入站点的存储图
python -m site_kg.cli live my-site --replay
```

页面通过 SSE 实时接收构建过程，服务端边爬取边做增量布局：节点按语料顺序到达，边随页面解析绑定；派生关系维度默认隐藏且从不参与布局弹簧，因此不会扭曲构图。场景：**Planets**（1）章节化作轨道上的星球、**Neural**（2）密集的突触场视图、**City**（3）每个页面一栋楼——楼层由页面权重决定，亮起的窗户就是它发出的链接。`#scene=`、localStorage 里上次选择、MCP 的 `scene=` 都只是"已选过场景"之后的预设，不会取代地址优先的启动流程。

规模与兜底：

- 规模下限是实测的 75,415 节点 / 61,615 边；布局全部在服务端完成（该规模下浏览器不做力导向布局）。`--max-nodes` 只裁剪预览。
- 标签有预算（章节 ≤40 / 刻度 ≤12），场景是一张合并点云加合并线批——没有逐节点对象或 DOM 标签。
- 没有 WebGL2 时自动退回 canvas 2D，最多画 5,000 个节点，并提示用离线归档查看全图；WebGL 上下文丢失会原地恢复。
- 归档查看器（`site-kg render <site_id>`）是单个离线 HTML（默认内嵌二进制载荷；`--external-data` 则在旁边写 `sk-*.bin`），同样支持这套场景回放。

安全：服务只绑定 `127.0.0.1`，不发 CORS 头，拒绝非回环 `Host`、拒绝路径穿越、不提供语料文件，并可选共享令牌（`--token`）。带令牌时打印的 URL 自带 `?token=…`，页面会在 snapshot、SSE 与 start 请求上转发它。Three.js r186 bundle 已提交并锁定；日常开发与离线使用都不需要 npm——需要刻意升级时见 `tools/build_viewer_bundle.mjs`。

## 关系维度

链接边与 import 边诚实但稀疏：绝大多数页面无人链接，因而在图上是孤立的。`--relations`（CLI）、`relations=`（MCP）或 `SITE_KG_RELATIONS`（环境变量）加入**衍生**维度，并按**每个节点自身的内容**选择适用维度，而非对全语料跑同一套抽取：

| 维度 | 条件 | 含义 |
|---|---|---|
| `mentions_symbol` | 代码 | 本文件引用了另一个文件定义的标识符 |
| `similar_to` | 正文 ≥ 220 字符 | 稀疏 tf-idf 余弦（正文 + 标题词元）：同主题但无显式链接 |
| `sibling_of` | 同目录 1–12 个同伴 | 同一源码目录 / URL 目录的同伴 |
| `supersedes` | id/URL 含版本号 | 2.0 取代 1.4（同一页面族） |
| `shares_heading` | ≥ 3 个标题 | 文档骨架重叠（标题集合 Jaccard ≥ 0.35）：同一生成器产出的 API 页 |
| `shares_term` | ≥ 2 个策展词元 | 共享作者标注的词汇：行内代码、标题、声明的符号 |
| `duplicate_of` | frontmatter sha256 | 内容完全相同（精确哈希层，链式连接） |
| `coupled_with` | 证据链接 | 文献耦合：两页引用了相同的目标 |
| `co_cited_with` | 证据链接 | 共引：相同的来源同时引用两页（可关联stub页） |
| `shares_entity` | 需 chat 模型 | 两页共享 ≥ 2 个 LLM 抽取的命名实体 |
| `semantic_similar` | 需 embed 模型 | 标题 + 正文头部的嵌入余弦 |

存根页不参与词汇相似，正文页不参与符号引用，发布说明得到版本沿革，源码文件得到符号引用。模式：`auto`（无 LLM，默认）、`llm`（`auto` + LLM 路由只可*收窄*每节点维度集，并启用两个模型维度）、`all`、`off`，或显式逗号列表。

两条不变量防止它变成"毛线球制造机"：已被语料链接的节点对绝不会被改写为衍生类型；一切维度按节点门控且扇出有上限。内容衍生维度**在零交叉引用的语料上也会运行**——内容是这种语料唯一的信号——但判定仍然如实报告 `NOT-READY`（无链接结构），存根语料依旧门控到 0 条边。无需 LLM 或嵌入模型；缺失时这些维度跳过并在 `relationNotes` 里留一行说明。

设计依据与文献（AutoSchemaKG arXiv:2505.23628、KGGen arXiv:2502.09956、EDC arXiv:2404.03868、Adaptive-RAG arXiv:2403.14403）：[docs/RELATIONS.md](docs/RELATIONS.md)。

## 实测

在 某嵌入式厂商文档站（静态 Sphinx）上：40 页有界摄入 → 40 文档、32 条 `ref` 边、1709 个索引词、判定 `READY`；七个 MCP 工具全部经真实 streamable-HTTP 会话调用通过（2026-10-06）。这同时证明：原始网站（不只是 wiki 导出）自带足以成图的链接结构。

`ask` 已对内网 vLLM 网关（qwen3.5-122b 语言模型 + bge-m3 嵌入 + bge-reranker-v2-m3 重排序）实测：英文问题返回带引用的分步答案；中文跨语言问题在 ASCII 索引零召回时由嵌入通道（与关键词命中取并集，嵌入文本为标题+正文开头而非摘要）正常作答。Doxygen API Reference 站另测一组：navtree 清单 300 页 → 301 文档、527 条 ref 边、READY，`ask` 返回带 4 条引用的正确答案。

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

- **摄入返回 `blocked by robots.txt`**：站点 robots 禁了这个 UA。联系站点所有者，或仅在你有权爬取时才传 `respect_robots=false`。对自己拥有或获授权测试的站点做深度爬取时，用 `--dev-crawl`（CLI）/ `dev=true`（MCP）：跳过 robots.txt 与限速延迟，默认上限加深（`--max-pages 2000 --max-depth 8`，并发 8）。
- **只发现 1–2 页**：seed URL 大概是叶子节点，改传文档根目录（结果里的 `seed_hint` 会明说）。纯客户端渲染的 SPA 壳页由 Playwright 兜底自动处理。
- **必须要 LLM 吗？** 爬取/检索/图遍历都不需要。只有 `ask` 需要 `SITE_KG_LLM_BASE` + `SITE_KG_LLM_KEY`（任意 OpenAI 兼容网关；vLLM/new-api 已实测）。
- **NOT-READY 判定**：站点没有内部交叉引用——它是目录树，不是图。你仍然拿到全文检索、页面正文（frontmatter 里带抽取的标题与描述），且无钥匙的内容维度（`similar_to`、`shares_heading` 等）依旧会把有实质关联的页面连起来；缺的只是对链接结构的声明。

## 部署

本机优先。免 sudo 的 systemd 用户单元：

```ini
# ~/.config/systemd/user/site-kg.service
[Service]
WorkingDirectory=%h/develop/site-kg
ExecStart=%h/develop/site-kg/.venv/bin/python -m site_kg.cli serve --transport http --port 8766
Restart=on-failure
[Install]
WantedBy=default.target
```

## 路线图

- 语义增强（可选）：配置 `SITE_KG_LLM_KEY` 后用 cognee `cognify` 在结构边之上叠加实体/关系边。
- 本地代码摄入的 tree-sitter 符号/引用边（aider 式 tags），作为正则层之上的可选 extra。
- 壳页兜底失效的 JS 站点（无限滚动、重反爬）走 Crawl4AI 深爬，经 `.[crawl]` extra 启用。
- 可视化已完成：`render_site` 产出自包含 3D Three.js 查看器（bundle 内嵌可离线，布局在渲染期用固定种子计算）；`render_build` 再用 WebGPU 回放构建过程本身——节点与各维度边波次动态落上 3D 章壳，HUD 实时计数。

## 许可

MIT。爬取内容的版权归原站点所有。
