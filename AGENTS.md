# AGENTS.md — Zine 海报生成器（Codex 协作规则）

本规则适用于本仓库内的所有 Codex 会话；新会话在项目目录打开时自动加载。改动任何文件前先读 `docs/DEV_NOTES.md`（部署与踩坑记录）。

## 项目是什么
档案风 zine 海报生成器 Web 版：注册登录 -> 上传照片 -> 程序自动分析配色/构图 -> 调用大模型生成海报。
纯标准库 Python 后端（`http.server`，无 Web 框架）+ 原生 HTML/JS 单页前端（无构建步骤）。前端 UI 由 Kimi 设计，页面逻辑不要交给 Kimi 重写。

## 铁律（违反即返工）
1. **禁止用视觉模型/AI 读取生成图片做验证**（早期会话因此出过错）。验证图片一律用 PIL 检查 format/mode/size，或直接查看文件。
2. 界面/文案禁止出现“管理员”“仅本人可见”“第一个注册的是管理员”等字样；管理员能力静默存在。
3. 绝不提交/暴露任何 API Key、密码、token：`config.json`、`.env`、`users.json`、`history.jsonl`、`sessions.json`、`uploads/`、`outputs/` 均已被 `.gitignore` 排除；commit 前检查 `git status`。
4. 对外报错信息必须脱敏（不打印 Key、密码、token）。
5. Kimi API：必须用**流式 SSE** 调用（非流式会超时/截断）；`temperature` 必须为 1；Key 从环境变量读取。
6. 阿里云百炼 `qwen-image-3.0`：生成约 1 分钟，429 自动重试；403 = 配额问题；欠费会报错（充值后恢复）。
7. PowerShell 下跑 Python 中文输出需 `$env:PYTHONIOENCODING='utf-8'`；脚本先写成 UTF-8 文件再执行，避免管道转义乱码。

## 常用命令
- 本地启动：`python poster_server.py`（默认 `http://127.0.0.1:8765`，可用 `PORT` 环境变量改端口）
- 命令行出图：`python poster_core.py --input "图片" [--title "文案"] [--dry-run]`
- 部署线上：`python scripts/deploy_web.py`（需 `ALI_PWD` 环境变量，见 `docs/DEV_NOTES.md`）

## 关键文件
- `poster_server.py`：后端。账号、会话、异步生成任务、历史记录、API 代理（qwen-image-3.0 / DeepSeek）
- `poster_core.py`：出图核心。强调色/构图分析 + 海报合成（纯程序分析，不用视觉模型）
- `web/index.html`：Kimi 设计的单文件前端（桌面 + 移动端）
- `scripts/deploy_web.py`：单文件线上部署脚本（密码走环境变量）
- `docs/DEV_NOTES.md`：部署环境与全部踩坑记录

## 架构要点
- 会话持久化：`SESSIONS` 落盘到 `DATA_DIR/sessions.json`，有效期 30 天；改会话逻辑要同时处理 `load_sessions`/`save_sessions`
- 生成是异步任务：后台线程 + 进度接口 + 刷新可恢复；前端轮询进度
- 数据文件：`config.json`（模型 Key，界面掩码保存）、`users.json`、`history.jsonl`、`uploads/`、`outputs/`
- 本地 `DATA_DIR` 默认当前目录；线上 `POSTER_DATA_DIR=/var/lib/poster-gen`