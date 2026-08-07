# 开发与部署笔记（新会话必读）

## 线上环境（当前）
- 服务器：阿里云轻量应用服务器（Ubuntu），公网 IP `59.110.224.189`；域名 `yutata.online`（审核中，审核通过后绑 A 记录到此 IP）
- 服务：systemd 单元 `poster-gen`，监听 80 端口（`POSTER_PORT=80`）
- 代码目录：`/opt/poster-gen`（git clone 自 GitHub `YUTATA121386/zine-poster-generator`）
- 数据目录：`/var/lib/poster-gen`（`users.json`、`sessions.json`、`history.jsonl`、`uploads/`、`outputs/`）
- 环境变量文件：`/etc/poster-gen.env`（`POSTER_API_KEY`、`DEEPSEEK_API_KEY`、`POSTER_DATA_DIR` 等）
- **凭据纪律**：服务器密码、API Key 一律不写进任何入库文件；部署密码通过 `ALI_PWD` 环境变量临时传入

## 部署
1. 本机改代码 -> commit -> `git push origin main`
2. 服务器上：`cd /opt/poster-gen && git pull && systemctl restart poster-gen`
3. 或只改前端时：`python scripts/deploy_web.py`（sftp 直传 `web/index.html`，本机需 `pip install paramiko`，密码放 `ALI_PWD`）
4. 部署后自检：`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/` 应返回 200

## 已修复问题记录（改相关代码前先看这里）
### 5. 海报风格选择（已上线）
- 生成参数新增「风格」下拉，单选一种：zine 档案风（原模板）/ photo-panel 摄影抽象面板 / 	ravel-abstract 旅行抽象研究 / paper-collage 拾景纸拼贴（gathered-scenes 实景拼贴）/ distillation 影像蒸馏（gathered-scenes 影像蒸馏）
- 前端 style 随 /api/generate 提交，后端校验合法值后透传 poster_core.generate(style=...)；历史记录新增 style 字段，旧记录无此字段按 zine 展示
- 风格 prompt 模板在 poster_core.py 的 STYLES 与 _prompt_* 系列函数中
### 1. 手机端容易登出（已修复上线）
- 根因：登录态只存内存（`SESSIONS = {}`），服务一重启全部失效；有效期仅 7 天
- 修复：`load_sessions`/`save_sessions` 落盘 `DATA_DIR/sessions.json`，有效期 30 天
- 注意：**每次服务重启，所有已登录用户都要重新登录一次**（旧 token 是内存态）；上线前要提醒用户
- 验证：`test_sessions.py`（重启后 token 仍有效）

### 2. 生成后网页端不显示图片（已修复）
- 根因：前端拿到结果后没把图片插入页面（本地能看到是因为直接看文件）
- 修复：前端生成完成后展示成品图 URL；历史记录里也展示缩略图

### 3. Kimi 新前端（已上线，含配套修复）
- 调用方式：**流式 SSE**（Kimi 长输出非流式会超时/截断）；`temperature` 必须为 1
- 流程：Kimi 生成 HTML+CSS 设计稿 -> 人工合入现有已测试 JS（页面交互逻辑不要让 Kimi 重写）
- 合入必须逐 id 核对：Kimi 输出的 DOM 与 JS 引用一一对应（曾踩坑：`kimiKeywords` 应为 `dsKeywords`；`kimSel` 未定义）
- 移动端：Kimi 的 CSS 会把退出按钮隐藏，需改为“只隐藏用户名徽章、保留退出按钮”
- 页面必须保留的交互：登录/注册、上传生成+进度条、刷新恢复进度、历史记录（每人只能看自己的）、模型设置（Key 掩码保存）

### 4. 生成慢 / 刷新会怎样 / 进度条（已实现）
- 生成是异步任务：后台线程执行，前端轮询进度，刷新页面后任务继续执行，进度可恢复
- 429 限流自动重试；403 = 配额/免费额度问题；API 欠费会报错，界面给出明确提示（充值后恢复）

## 图片生成（qwen-image-3.0）
- I2I 流程：上传原图 -> 程序分析强调色/构图位置（`poster_core.py`，纯程序分析）-> 拼接提示词 -> 百炼 API 生成
- 输出命名：`poster-<图片名>-<强调色>-<位置>.png`
- 验证：PIL 检查 `format=PNG` / `mode=RGB` / 尺寸符合预期；**禁止用视觉模型读图验证**

## 早期调试脚本
早期会话的调试/补丁脚本散落在旧会话工作目录（如 `C:\Users\beppi\Documents\Codex\2026-08-07\wo\work\`），可参考但不依赖；正式逻辑都已合入 `poster_server.py` / `poster_core.py` / `web/index.html`。README 里的启动/部署章节同样有效。