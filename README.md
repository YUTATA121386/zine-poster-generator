# Zine 海报生成器（Web 版）

独立本地小工具：浏览器界面（由 Kimi 设计），选照片 -> 自动分析配色/位置 -> 生成档案风 zine 海报。

## 启动

双击 `启动海报生成器.bat`（或 `python poster_server.py`），自动打开浏览器 `http://127.0.0.1:8765`。
界面左侧上传照片，右侧调整参数，底部看结果；右上角「模型设置」里配置各模型 API Key。

## 模型 API 配置（右上角「模型设置」）

- **图片生成 · 阿里云百炼**：`qwen-image-3.0`（I2I 出图），Key 必填
- **文案生成 · DeepSeek**：主文案旁「DeepSeek 生成」按钮用它写诗意短句（模型可选 `deepseek-v4-pro` / `deepseek-v4-flash`），Key 可选
- 首次启动会自动迁移旧配置（`~\.codex\skills\vision\.env` 里的百炼 Key）
- Key 只保存在本机 `config.json`，界面掩码显示，不会上传任何服务

## 部署为在线网站（推荐：Render 免费一键部署）

GitHub Pages 只能托管静态页面，无法运行本项目（需要 Python 后端 + 服务器端 API Key），因此使用免费托管平台 Render 自动部署，绑定 GitHub 仓库后 push 即自动上线。

1. 打开 https://render.com 注册（免费，可用 GitHub 账号登录）
2. 点 New + -> Blueprint -> 选择本仓库 `zine-poster-generator`
3. 按提示填入两个环境变量（Render 会让你在部署时填写，不会入库）：
   - `POSTER_API_KEY`：阿里云百炼 API Key
   - `DEEPSEEK_API_KEY`：DeepSeek API Key
4. 点 Apply，约 3 分钟完成，访问 `https://zine-poster-generator.onrender.com`

说明：
- 线上模式下页面设置不可改 Key（由环境变量管理）；生成接口公开，任何人可用你的 Key 额度出图，注意成本
- Render 免费实例无请求约 15 分钟后休眠，再次访问首次加载约需 30-60 秒（冷启动）
- 如需国内直连，可改用阿里云函数计算/轻量服务器（本项目为标准 Python 服务，任意可跑 Python 的环境均可）

其他部署方式（自购 VPS / 任意 PaaS）：

```bash
pip install -r requirements.txt
export POSTER_DEPLOYED=1
export POSTER_API_KEY=你的百炼Key
export DEEPSEEK_API_KEY=你的DeepSeekKey
export POSTER_HOST=0.0.0.0
export POSTER_PORT=8080
python poster_server.py
```
## 说明

- 生成约 1 分钟，按百炼账号计费；429 限流自动重试
- 403 = 配额/「仅免费额度」问题；DeepSeek 文案失败会在界面提示具体原因
- 强调色/位置为纯程序分析（主色相/饱和度/边缘能量），不使用视觉模型读图
- 输出到 `outputs\`，命名 `poster-<图片名>-<强调色>-<位置>.png`
- 命令行直达：`python poster_core.py --input "图片" [--title "文案"] [--dry-run]`
