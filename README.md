# 知乎阅读工作台

[![CI](https://github.com/Jandersway/zhihu-reading-workbench/actions/workflows/ci.yml/badge.svg)](https://github.com/Jandersway/zhihu-reading-workbench/actions/workflows/ci.yml)

在一个页面发现知乎文章、读正文、看评论、保存资料并追问原文。Nuxt 与 Vue 负责网页交互，FastAPI 复用 Python Agent 的检索与排序，SQLite 按账号保存阅读库、评分和问答记录。

## 快速开始

Windows 上准备 Python 3.12、Node.js 24 和 PowerShell 7。克隆后可直接安装仓库内的核心包。

```powershell
git clone https://github.com/Jandersway/zhihu-reading-workbench.git
Set-Location zhihu-reading-workbench
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ./core -r backend/requirements.txt
npm --prefix web ci
npm --prefix web run build
pwsh -NoProfile -File .\start.ps1
```

打开 <http://127.0.0.1:3000/>。API 监听 `127.0.0.1:8765`，浏览器经 Nuxt 的同源代理请求 API。启动脚本默认使用仓库根目录下的 `.venv`，也可通过 `ZHIHU_WEB_PYTHON` 指定解释器。

阅读与规则排序可以直接使用。启用收藏语义排序时，运行 `.\.venv\Scripts\python.exe -m backend.embeddings`，下载并校验 BGE Small CPU 权重。工作台通过 [zhihu-toolkit 0.5.0](https://github.com/KrisTHL181/zhihu-toolkit) 只读访问知乎，网页登录与 CLI 的登录状态分开。

在知乎官网登录后，打开浏览器开发者工具「网络」，右键一条知乎请求，选择「复制为 cURL（cmd）」并粘贴到工作台。也可以粘贴 Cookie 请求头的值。Windows 上的工作台会话由系统加密保护，重启后可恢复，连续 30 天未使用才会过期。

## 代码结构

| 目录 | 职责 |
| --- | --- |
| `web/` | Nuxt、Vue 阅读界面和同源 API 代理 |
| `backend/` | FastAPI、SQLite、按账号读取知乎、同批重排和盲评 |
| `core/` | Python Agent、检索、证据核验、评审和 Skills |

发现页先从知乎取候选，阅读目标决定相关性档位，再用收藏和人工评分调整档内顺序。单篇问答直接读取选中的正文，模型回答带可核对的原文引文。跨资料问答由 Agent 搜索和读取阅读库。

## 日常流程

1. 在「发现文章」看首页推荐，或按主题搜索。同一标签页会保留当前这批首页推荐，点击「刷新推荐」才从知乎取新候选。阅读目标可以手动调整，首页会按新目标重排同一批。给文章评分后，下次推荐会参考它。
2. 在列表内打开原文，查看评论或保存。读文章时可以就当前正文提问。
3. 在「资料问答」对阅读库或指定文章提问。问题建议来自当前资料的标题、章节和正文，可以点选后修改。问答结果包含原文摘录与出处，运行记录可重新打开结果。

收藏同步用当前知乎会话核对账号，只读该账号自己创建的收藏夹。当前候选批次会显示逐篇对照，比较时保持候选、目标和评分一致。规则分中的官网收藏和阅读库只取较高的词项相似加分，最多加 0.10 分。

「不想读」会降低同篇文章和标题高度相似的新候选的顺序。相似度计算忽略宽泛的专题和类型词，仅取最相近的一条负反馈，相似降权上限为 0.12 分。手动阅读目标和明确的搜索词仍用于优先分档。文章的「推荐理由与评分」会展示触发降权的原评分记录。

问题建议、本地摘录和发现页的重排均在本机执行。推荐先检查知乎返回的推广标记、赞助声明和明确的商业引导，再对保留的候选重排。线索较弱的内容可标为「疑似推广」。可用「隐藏推广」过滤本账号后续首页候选。

打开一篇文章或回答后，在「问这篇」提问。配置了可用模型的账号默认选择模型问答，点击「调用模型回答」才会发送问题和相关正文。也可切换到本地原文摘录。结果会标明回答方式。问答结果可编辑后「存为笔记」，保存到该账号的本地阅读库并保留原文入口。后续推荐可参考笔记标题及小结，当前阅读目标仍然优先。

「问这篇」会在当前浏览器标签页临时保留各篇问答。切换文章或刷新页面后可回来查看。每篇最多保留最近 20 条，最多保留最近问过的 20 篇，距上次回答 24 小时后清除。退出工作台或点击「清空本篇记录」也会清除相应的临时记录。需要长期保留的回答可以存为笔记。

首次打开「推荐给你」会尝试同步当前知乎账号自己的收藏，也可点「更新知乎收藏」手动同步。同步最多读取 8 个收藏夹，每夹 20 条。准备模型命令下载并校验 BGE Small 的 CPU ONNX 权重。同步后的向量和评分都保存在该账号的本地数据库。模型缺失时继续用词项规则。推荐先按本轮目标相关性分档，BGE 收藏相似度和人工训练的权重只在同档内调整顺序。

「同一批候选的三种排序」展示知乎原序、现有规则、收藏语义或训练模型。评分绑定当时显示的批次和冻结特征。累计至少 6 批、18 对同档不同分的评价后尝试训练，按时间留出最后两批，并过滤训练中与留出项重复的文章和晚于留出批次的评分。训练模型只有在已评分候选 NDCG@3 超过规则与收藏语义两条基线时才用于推荐。该指标只衡量被评分的子集。详细实验见[模型选型报告](模型选型报告.md)。

运行 `python -m backend.embeddings --e5` 可准备四路评测所需的 E5 权重。同步收藏后，在推荐结果点「评测这批」。评测固定本轮目标、候选和收藏快照，比较知乎原序、规则、规则 + BGE、规则 + E5。两种模型的余弦相似度使用同一种批内相对位置折算，最高加分一致。这里的盲评保存到当前账号的评测表，单独计算指标。可保留评测地址以继续打分。

先标四路各自前三篇的并集。并集评完且有正收益后计算并集 NDCG@3。每种方法的前三评完后，再按「明确想读」计算该方法的 Precision@3 和 MRR@3。缺失标签保持未知。正式比较用多个后续未参与训练的批次，按批计算相对知乎原序和现有规则的成对差值。

在「模型设置」选择 Kimi Coding、OpenAI、DeepSeek、通义千问（百炼北京）、Gemini、Claude 兼容接口或 OpenRouter，并填入自己的 API Key 和支持工具调用的模型 ID。部署方也可以用 `ZHIHU_WEB_COMPATIBLE_BASE_URL` 注册额外的 HTTPS Chat Completions 兼容服务。Windows 用当前系统账号的 DPAPI 加密保存配置，其他系统暂存在后端进程内存。网页账号各自配置，CLI 使用的 `KIMI_API_KEY` 环境变量独立。实时问答需要逐次确认，请求可能产生费用。部署方可以设置 `ZHIHU_WEB_ENABLE_LIVE=0` 关闭实时模型。

## 本地验证

```powershell
.\.venv\Scripts\python.exe -m pip install httpx
.\.venv\Scripts\python.exe -B -m unittest discover -s core/tests
.\.venv\Scripts\python.exe -B -m backend.check
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
```

后端用临时工作台数据库和模拟知乎上游验证账号隔离、会话恢复、推荐和引用。排序评测与 Agentic RL 仍可在开发时分别访问 `/lab` 和 `/agent-rl`。排序评测使用 `ZHIHU_WEB_COHORT` 指定评测集，并依赖配套运行档案。

## 数据与许可

工作台面向本机运行。会话、模型配置和 SQLite 数据保存在忽略提交的 `.runtime/` 下。从本机工作台导入自己的知乎登录状态，API Key 在「模型设置」里单独配置。只有主动运行模型问答或推荐时，相关问题与资料才会发送到所选模型接口。

项目源码使用 [MIT 许可证](LICENSE)。知乎内容与第三方模型及依赖适用各自的许可或使用条款。
