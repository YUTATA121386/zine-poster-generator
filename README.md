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

## 部署为在线网站

项目是标准 Python 服务（仅依赖 Pillow），可部署到任意可跑 Python 的服务器/平台（推荐有长请求超时的平台，如 Render、Railway、自购 VPS；避免 60s 超时的 Serverless 平台）。

```bash
pip install -r requirements.txt
# 线上模式：Key 全部走环境变量，页面设置不可改 Key
export POSTER_DEPLOYED=1
export POSTER_API_KEY=你的百炼Key
export DEEPSEEK_API_KEY=你的DeepSeekKey
export POSTER_OUT_DIR=/data/poster-outputs   # 可选
export POSTER_HOST=0.0.0.0
export POSTER_PORT=8080
python poster_server.py
```

安全说明：
- 仓库不含任何 API Key；本机 Key 存于 `config.json`（已被 `.gitignore` 忽略）
- 线上模式下 `/api/config` 拒绝修改 Key 与输出目录，仅可调模型/命名/尺寸
- 公开部署请注意：生成接口无鉴权，任何人可用你的额度出图，建议加访问控制或限额

本地模式（默认）：`python poster_server.py` 或双击 `启动海报生成器.bat`，`127.0.0.1:8765`。

## 说明

- 生成约 1 分钟，按百炼账号计费；429 限流自动重试
- 403 = 配额/「仅免费额度」问题；DeepSeek 文案失败会在界面提示具体原因
- 强调色/位置为纯程序分析（主色相/饱和度/边缘能量），不使用视觉模型读图
- 输出到 `outputs\`，命名 `poster-<图片名>-<强调色>-<位置>.png`
- 命令行直达：`python poster_core.py --input "图片" [--title "文案"] [--dry-run]`
