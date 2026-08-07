# Zine 海报生成器（Web 版）

独立本地小工具 / 在线网站：浏览器界面（由 Kimi 设计），注册登录后上传照片 -> 自动分析配色/位置 -> 生成档案风 zine 海报。

## 启动

双击 `启动海报生成器.bat`（或 `python poster_server.py`），自动打开浏览器 `http://127.0.0.1:8765`。
首次使用先注册账号（**第一个注册的账号自动成为管理员**）；手机/电脑浏览器均可访问，界面已做移动端适配。

- 每个生成请求会自动记录：**用户名、时间、IP、上传原图、完整提示词、参数、成品图**
- 「生成记录」面板：管理员可查看所有用户的记录；普通用户只能看到自己的
- 上传原图仅本人与管理员可访问（鉴权接口），成品海报为公开 URL

## 模型 API 配置（右上角「模型设置」）

- **图片生成 · 阿里云百炼**：`qwen-image-3.0`（I2I 出图），Key 必填
- **文案生成 · DeepSeek**：主文案旁「DeepSeek 生成」按钮用它写诗意短句（模型可选 `deepseek-v4-pro` / `deepseek-v4-flash`），Key 可选
- 首次启动会自动迁移旧配置（`~\.codex\skills\vision\.env` 里的百炼 Key）
- Key 只保存在本机 `config.json`，界面掩码显示，不会上传任何服务

## 部署为在线网站（推荐：阿里云轻量服务器）

GitHub Pages 只能托管静态页面，无法运行本项目（需要 Python 后端 + 服务器端 API Key），推荐部署到阿里云轻量应用服务器：国内直连快、数据持久、约 ¥30-50/月。

### 1. 购买服务器

阿里云控制台搜索「轻量应用服务器」：系统选 **Ubuntu 22.04**，地域选离你近的（如华东1-杭州），2核2G 起步即可，确认安全组放行 **80/443 端口**（或 8000）。

### 2. 服务器上执行（SSH 登录后）

```bash
# 安装依赖
apt update && apt install -y python3-pip git
pip3 install pillow

# 拉取代码
git clone https://github.com/YUTATA121386/zine-poster-generator.git /opt/poster-gen
cd /opt/poster-gen

# 数据目录（持久化：账号/记录/图片都在这）
mkdir -p /var/lib/poster-gen

# 写环境变量配置
cat > /etc/poster-gen.env <<'EOF'
POSTER_DEPLOYED=1
POSTER_HOST=0.0.0.0
POSTER_PORT=8000
POSTER_DATA_DIR=/var/lib/poster-gen
POSTER_API_KEY=你的百炼Key
DEEPSEEK_API_KEY=你的DeepSeekKey
EOF

# systemd 守护（开机自启、崩溃自动重启）
cat > /etc/systemd/system/poster-gen.service <<'EOF'
[Unit]
Description=Zine Poster Generator
After=network.target
[Service]
EnvironmentFile=/etc/poster-gen.env
WorkingDirectory=/opt/poster-gen
ExecStart=/usr/bin/python3 poster_server.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now poster-gen
```

3. 浏览器访问 `http://服务器公网IP:8000`，**第一时间注册你自己的账号**（第一个注册的是管理员）
4. 可选：用 Nginx/Caddy 反代 80 端口，或绑定域名 + 阿里云免费 SSL 证书

### 备选：Render 免费（数据不持久，仅试用）

仓库含 `render.yaml`，Render 免费实例约 15 分钟无请求休眠、**磁盘不持久（重启/重新部署会清空账号与记录）**，只适合短期试用。

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
