# 知乎阅读工作台

[![CI](https://github.com/Jandersway/zhihu-reading-workbench/actions/workflows/ci.yml/badge.svg)](https://github.com/Jandersway/zhihu-reading-workbench/actions/workflows/ci.yml)

本地阅读工具。把知乎推荐、正文、评论和问答放在同一个工作台。

- **发现** · 按阅读目标、收藏和评分重排，点「刷新推荐」才换一批。
- **阅读** · 打开原文、看评论，把值得留的内容存到阅读库。
- **问答** · 根据正文回答并标注引文，回答可存为笔记。

## Windows 启动

需要 Python 3.12、Node.js 24 和 PowerShell 7。

```powershell
git clone https://github.com/Jandersway/zhihu-reading-workbench.git
Set-Location zhihu-reading-workbench
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ./core -r backend/requirements.txt
npm --prefix web ci
npm --prefix web run build
pwsh -NoProfile -File .\start.ps1
```

打开 <http://127.0.0.1:3000/>。在知乎官网登录后，从浏览器「开发者工具 → 网络」复制一条知乎请求的 cURL（cmd），粘贴到本机工作台。需要模型问答时，在「模型设置」填自己的 API Key。

## 源码与文档

`web/` 是 Nuxt 前端，`backend/` 是 FastAPI 和 SQLite，`core/` 是 Python Agent。

工作台通过 KrisTHL181 发布的 `zhihu-toolkit==0.5.0` 只读访问知乎。 [知乎 CLI 源码（Fork · v0.5.0）](https://github.com/Jandersway/zhihu-toolkit/tree/v0.5.0) · [上游仓库](https://github.com/KrisTHL181/zhihu-toolkit)

[使用与开发说明](docs/guide.md) · [排序实验](模型选型报告.md) · [MIT 许可证](LICENSE)
