# Domain Relevance Agent

> 基于 LangGraph 的域名业务相关性判定 Agent —— 给定一批域名，自动探测 / 抓取 / 评估，输出每个域名是否属于目标业务（成人教育课程）以及所属二级类别（兴趣类 / 副业类 / 金融保险类）。

## ✨ 概述

判定一个域名"是否是我们要找的业务站点"是一个多源、多分支、且充满噪声的工程问题：站点可能打不开、被 WAF 拦截、是登录页、是通用平台、内容稀薄……任何单一信号都不可靠。本项目用一条 **LangGraph 状态图流水线** 把这些不确定性组织起来：

- **规则先行，LLM 兜底**：能用规则确定的（域名归一化、HTTP 探测、访问分类、路径恢复、通用平台识别、内容质量打分、历史先验匹配）全部用确定性代码做；只有需要语义理解的（证据提取、相关性裁判、ICP 备案裁判）才调 LLM。
- **纯 verdict 驱动评分**：LLM 只下分类结论（`match` / `no_match` / `uncertain` + 二级 `category`），**不输出数值置信度**。最终分数由确定性公式从结论 + 结构化信号 + 先验 + 惩罚推导，保证可解释、可复现、抗 LLM 抖动。
- **双分支救援**：抓得到正文走 crawl 分支（证据 → 裁判）；抓不到（unreachable / cloud_error / 内容为空）走 ICP 分支，用工信部备案信息兜底。

## 🧭 架构总览

```
START → normalize_domain → http_probe → classify_access_status
                                                │
        ┌───────────────────────────────────────┼───────────────────────┐
        ▼                                       ▼                       ▼
   reachable/login/                         cloud_error             unreachable
   weak_content/unknown                         │                       │
        │                                       ▼                       │
        ▼                                  path_recovery                │
   crawl_content                                  │                       │
        │                            ┌────────────┴────────┐              │
        │                            ▼                     ▼            │
        │                       found_url            not_found ──────────┤
        │                            │                                  │
        ▼                            ▼                                  │
   content_quality_check ←──────────────────────────────────────────────┘
        │
        ▼
   compute_historical_prior
        │
        ▼
   run_generic_filter
        │
        ├── 有抓取正文 → extract_evidence → judge_relevance ──┐
        │                                                      ├──→ calibrate_score → persist → END
        └── 无抓取正文 → icp_query → icp_judge ────────────────┘
```

### 节点职责

| 阶段 | 节点 | 类型 | 说明 |
|---|---|---|---|
| 归一 | `normalize_domain` | 规则 | 从 URL/域名提取 apex，生成 https/http/www 变体 |
| 探测 | `http_probe` | 规则 | 并发探测所有变体的可达性、状态码、首屏正文 |
| 分类 | `classify_access_status` | 规则 | `reachable` / `cloud_error` / `unreachable` / `login_only` / `weak_content` / `unknown` |
| 恢复 | `path_recovery` | 规则 | cloud_error 时遍历候选路径，找回可用页面 |
| 抓取 | `crawl_content` | 工具 | Crawl4AI 抓取，产出 `fit_markdown` |
| 质量 | `content_quality_check` | 规则 | 结构 + 关键词 + 长度 → level(high/medium/low/empty) + score |
| 先验 | `compute_historical_prior` | 规则 | 历史 gold 库匹配，返回 `prior_score` + `matched_category` |
| 过滤 | `run_generic_filter` | 规则 | 通用平台/云/统计域名识别 → penalty |
| 证据 | `extract_evidence` | **LLM** | 结构化抽取课程/师资/联系方式信号（带引用 + strength） |
| 裁判 | `judge_relevance` | **LLM** | 下 `relevance` + `category` 结论 |
| 备案 | `icp_query` → `icp_judge` | 工具 + **LLM** | 调本地 ICP 服务，LLM 据备案主体判相关性 |
| 评分 | `calibrate_score` | 规则 | 确定性公式汇总最终分数与路由 |
| 持久 | `persist` | 工具 | 写盘 artifact，记录 trace |

## 📁 目录结构

```
Domain-Relevance-Agent/
├── app/
│   ├── cli.py                       # CLI 入口（批量跑 domains.txt → results.jsonl）
│   ├── graph/
│   │   ├── builder.py               # LangGraph 装配（边与条件路由）
│   │   ├── state.py                 # DomainGraphState + add_trace
│   │   └── nodes/                   # 各图节点（一节点一文件）
│   ├── rules/
│   │   ├── scene_config.yaml        # 场景信号、通用平台策略、类别、评分常量
│   │   ├── path_candidates.yaml     # 路径恢复候选路径
│   │   └── loader.py                # 配置 / 评分常量 / 类别 加载器
│   └── tools/
│       ├── llm_client.py            # 百炼 OpenAI 兼容客户端
│       ├── crawl4ai_client.py       # Crawl4AI 包装（含 requests 降级）
│       ├── http_probe.py            # HTTP 探测
│       ├── historical_domain_loader.py  # gold 库匹配索引
│       └── icp_provider.py          # 本地 ICP API 客户端
├── data/
│   ├── historical_domain_library.jsonl   # 历史 gold 域名库（domain/category/industry）
│   └── crawl_artifacts/             # 每个域名的原始抓取产物（按 batch_id/hash 分目录）
├── icp_query/                       # 本地 ymicp ICP 查询服务（icpApi.exe + config.yml）
├── domains.txt                      # 待判定域名清单（一行一个）
├── requirements.txt                 # 依赖清单
└── README.md
```

## 🛠 技术选型

| 关注点 | 选型 | 理由 |
|---|---|---|
| 工作流编排 | **LangGraph** | 显式状态图 + 条件路由，节点即纯函数，易测试、易追溯 |
| 网页抓取 | **Crawl4AI** | 抗反爬、JS 渲染，输出干净 markdown；无依赖时自动降级 requests |
| LLM | **百炼 / 通义千问**（qwen，OpenAI 兼容） | 国内可达、支持 `response_format=json_object` 严格 JSON |
| 域名解析 | **tldextract** | 可靠提取 registrable domain，先验匹配基础 |
| ICP 兜底 | **ymicp**（本地 exe，`127.0.0.1:16181`） | 离线查工信部备案，无第三方在线依赖 |
| 配置 | **PyYAML** | 信号词、评分常量、阈值全部外置可调 |
| 语言 | **Python ≥ 3.10** | |

## 🎯 评分模型（核心设计）

LLM **不输出数值置信度**——经验上 LLM 的 0–100 分标定差且抖动大。最终分数完全由确定性公式推导（crawl / ICP 两分支共用）：

```
final = clamp(
    base(relevance)            # match=60 / uncertain=30 / no_match=0
  + signal_bonus              # 仅 crawl：Σ正向strength − Σ负向strength，权重×5，clamp[−20,+25]
  + category_bonus            # LLM 给了非 null 类别：+8
  + consistency_bonus         # LLM 类别 == 历史先验类别：+10
  + prior_bonus               # 历史先验，封顶 15
  − generic_penalty           # 通用平台/云/统计域名：−35
)
```

**路由规则**（`calibrate_score`）：

1. `no_match` → **直接 `done`**（清晰负向终态，不依赖内容质量，先短路，避免登录页被拖去人工复核）
2. crawl 分支内容 `empty/low` 或质量分 < 阈值 → `uncertain` + `human_review`（内容不足以信任正判）
3. `match` 且 final ≥ 阈值(70) → `match` + `done`
4. `match` 但分不足 → `uncertain` + `human_review`
5. `uncertain` → `human_review`
6. ICP 分支若 ICP 查询本身失败 → `next_action=icp_query`（留待后续富化）

> 所有常量集中在 `scene_config.yaml` 的 `scoring:` 节，`loader.get_scoring_config()` 读取并叠加默认值。

## 🚀 快速开始

### 1. 环境依赖

```bash
# 安装全部依赖（含 crawl4ai 抓取主力）
pip install -r requirements.txt
```

> `crawl4ai` 首次安装后还需下载浏览器内核：`crawl4ai-setup`（或在运行时自动拉取）。
> 若不装 crawl4ai，`crawl4ai_client` 会自动降级为轻量 `requests` 抓取，便于本地快速试跑。

```bash
# 可选：仅开发
pip install pytest
```

Python ≥ 3.10。

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY
```

```ini
DASHSCOPE_API_KEY=sk-your-dashscope-key-here
# 可选覆盖
# DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_MODEL=qwen3.7-plus
```

### 3.（可选）启动 ICP 查询服务

> 仅当需要 ICP 兜底分支时启动；不启动不影响 crawl 分支运行（ICP 查询会返回失败，相关域名走 human_review）。

`icp_query/icpApi.exe` 监听 `127.0.0.1:16181`，配置见 `icp_query/config.yml`。

### 4. 运行

```bash
# 准备域名清单（一行一个）
# domains.txt
#   https://taiji.xingqudao.cn
#   baijiahao.baidu.com

python -m app.cli \
    --domains domains.txt \
    --scene-config app/rules/scene_config.yaml \
    --output results.jsonl
```

## 📤 输出说明

`results.jsonl` 每行一个域名的完整判定记录，关键字段：

| 字段 | 含义 |
|---|---|
| `domain` / `normalized` / `access_status` | 原始域名、归一化、访问分类 |
| `content_quality` | 抓取正文质量 level/score |
| `historical_prior` | 历史先验：match_type / prior_score / matched_category |
| `generic_tool_result` | 通用平台命中与 penalty |
| `evidence` | LLM 抽取的结构化证据（正/负信号 + 引用 + strength） |
| `relevance_judgement` | crawl 分支：`relevance` + `category` + `reasoning` |
| `icp_judgement` | ICP 分支：同上 schema |
| `calibrated_score` | 最终分数、`match_result`、`next_action`、`needs_human_review`、`components`(各项加减分明细)、`branch` |
| `trace` | 每个节点的输入/输出时间线，便于审计与排错 |

原始抓取产物另存于 `data/crawl_artifacts/{batch_id}/{domain_hash}/`（probe / crawl / quality / evidence / judgement / markdown / state.json）。

## ⚙️ 配置说明（scene_config.yaml）

- **`primary_scene` / `description`**：目标业务定义。
- **`categories`**：权威二级类别集合（兴趣类 / 副业类 / 金融保险类）。
- **`global_*_signals`**：全局正/负向关键词。
- **`generic_tool_policy`**：通用平台信号词与默认 penalty。
- **`scenes`**：各子场景的强/弱信号、受众画像、负向信号、合规备注（**仅作 prompt 参考，不再用于三级分类输出**）。
- **`scoring`**：评分公式全部常量（base / weight / bonus / 阈值）。

## 🗺 项目状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | MVP：归一化 → 探测 → 访问分类 → CLI | ✅ |
| Phase 2 | Crawl4AI 抓取 + 路径恢复 + 内容质量 + 持久化 | ✅ |
| Phase 3 | LLM 评级（证据抽取 + 相关性裁判 + 评分标定） | ✅ |
| 评分重构 | 纯 verdict 驱动 + 三级→二级类别 + 阈值外置 | ✅ |
| ICP 兜底 | 本地 ymicp 接入 + 备案裁判分支 | ✅ |
| 历史库迁移 | gold 库重建为二级 category（兴趣类/副业类） | ✅ |

**待办 / 未实现**：
- `eval.py`：在 gold 集上批量评估 `match_result` 准确率（尚未编写）。
- FastAPI 服务、人工复核界面、向量检索等能力尚未规划接入。

## ⚠️ 上传/部署注意

- **切勿提交** `.env`、`*.db`、`icp_query/icpApi.exe`、`*.xlsx`、`results.jsonl`、`data/crawl_artifacts/`（见 `.gitignore`）。
- LLM 调用默认 `temperature=0.0`，但 qwen 在边界 case 仍可能非完全确定；评分体系已保证对任意 verdict 结论的路由一致。

## 📄 许可

内部项目，未指定开源许可。
