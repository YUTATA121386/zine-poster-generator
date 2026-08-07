# -*- coding: utf-8 -*-
"""poster-server：Zine 海报生成器本地 Web 服务（Python 标准库，零依赖）。

启动: python poster_server.py [端口]   默认 8765，自动打开浏览器。
前端: web/index.html（Kimi 设计）; 输出: outputs/; 配置: config.json（Key 仅存本机）。
"""
import base64
import hashlib
import hmac
import io
import json
import secrets
import shutil
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, parse_qs

import poster_core

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("POSTER_DATA_DIR") or BASE_DIR
WEB_DIR = os.path.join(BASE_DIR, "web")
OUT_DIR = os.path.join(DATA_DIR, "outputs")
TMP_DIR = os.path.join(DATA_DIR, "tmp")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.jsonl")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
SESSIONS = {}  # token -> {"username": str, "expires": float}
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
TASKS = {}  # task_id -> {state, progress, message, url, info, error, ip}
_TASK_LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "bailian_key": "",
    "deepseek_key": "",
    "image_model": "qwen-image-3.0",
    "deepseek_model": "deepseek-v4-pro",
    "deepseek_models": ["deepseek-v4-pro", "deepseek-v4-flash"],
    "output_dir": "",
    "naming": "auto",
    "admin_token": "",
    "size": "900*1500",
}

POS_FE = ["top-left", "top-center", "top-right",
          "middle-left", "center", "middle-right",
          "bottom-left", "bottom-center", "bottom-right"]
POS_CORE = ["upper-left", "upper-middle", "upper-right",
            "center-left", "center", "center-right",
            "lower-left", "lower-middle", "lower-right"]
POS_FE2CORE = dict(zip(POS_FE, POS_CORE))
POS_CORE2FE = dict(zip(POS_CORE, POS_FE))

_LOCK = threading.Lock()


def load_sessions():
    """启动时从磁盘恢复会话，保证服务重启不登出。"""
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        SESSIONS.clear()
        for k, v in data.items():
            if v.get("expires", 0) > now:
                SESSIONS[k] = v
    except (OSError, ValueError):
        pass


def save_sessions():
    try:
        now = time.time()
        for k in [k for k, v in SESSIONS.items() if v.get("expires", 0) <= now]:
            SESSIONS.pop(k, None)
        tmp = SESSIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(SESSIONS, f, ensure_ascii=False)
        os.replace(tmp, SESSIONS_FILE)
        try:
            os.chmod(SESSIONS_FILE, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def load_config():
    with _LOCK:
        cfg = dict(DEFAULT_CONFIG)
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
        for env_name, cfg_key in (("POSTER_API_KEY", "bailian_key"),
                                  ("DEEPSEEK_API_KEY", "deepseek_key")):
            v = os.environ.get(env_name, "").strip()
            if v:
                cfg[cfg_key] = v
        v = os.environ.get("POSTER_OUT_DIR", "").strip()
        if v:
            cfg["output_dir"] = v
        if not cfg.get("bailian_key"):
            k = poster_core.load_api_key()
            if k:
                cfg["bailian_key"] = k
                save_config(cfg)
        return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def mask_key(k):
    if not k:
        return None
    return "%s…%s（%d 位）" % (k[:4], k[-4:], len(k))


def is_deployed():
    return os.environ.get("POSTER_DEPLOYED", "").strip().lower() in ("1", "true", "yes")


def admin_token_of(cfg):
    return (os.environ.get("POSTER_ADMIN_TOKEN") or cfg.get("admin_token") or "").strip()


def new_history_id():
    return "%x" % int(time.time() * 1000) + secrets.token_hex(3)


def append_history(rec):
    rec.setdefault("id", new_history_id())
    try:
        with _LOCK:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_history(limit=200):
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []
    return list(reversed(recs[-limit:]))


def load_all_history():
    """读取全部记录（文件原顺序），供管理操作使用。"""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []


def rewrite_history(recs):
    """原子重写 history.jsonl（调用方需持 _LOCK）。"""
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, HISTORY_FILE)


def ensure_history_ids():
    """为旧记录补齐 id 字段（删除需要稳定定位）。"""
    with _LOCK:
        recs = load_all_history()
        changed = False
        for r in recs:
            if not r.get("id"):
                r["id"] = new_history_id()
                changed = True
        if changed:
            rewrite_history(recs)


# ---------- 异步生成任务 ----------

def _task_progress(task, p, msg):
    with _TASK_LOCK:
        task["progress"] = max(int(p), task.get("progress") or 0)
        task["message"] = msg
        task["state"] = "running"


def _task_say(task, msg):
    p = task.get("progress") or 0
    if "分析完成" in msg:
        p = max(p, 30)
    elif "正在调用" in msg:
        p = max(p, 45)
    elif "限流" in msg:
        p = max(p, 45)
    elif "下载结果" in msg:
        p = max(p, 75)
    elif "已保存并校验" in msg:
        p = max(p, 88)
    _task_progress(task, p, msg)


def _run_generate_task(task_id, username, data, cfg, tmp):
    task = TASKS.get(task_id)
    if task is None:
        safe_unlink(tmp)
        return
    try:
        title = (data.get("title") or "").strip() or None
        caption = (data.get("caption") or "").strip() or None
        features = (data.get("features") or "").strip() or None
        pos_fe = data.get("position")
        pos_core = POS_FE2CORE.get(pos_fe) if pos_fe else None
        accent = None
        if data.get("accent_hex"):
            name = (data.get("accent_name") or "custom").strip() or "custom"
            accent = (name, str(data["accent_hex"]).strip().upper())
        size = str(data.get("size") or cfg.get("size") or "900*1500")
        if not re.match(r"^\d+\*\d+$", size):
            raise RuntimeError("输出尺寸格式应为 宽*高，如 900*1500")
        style = str(data.get("style") or "zine").strip()
        if style not in poster_core.STYLES:
            raise RuntimeError("未知风格：%s" % style)
        name = (data.get("name") or "")
        name_base = os.path.splitext(os.path.basename(name))[0] if name else None
        out_dir = out_dir_of(cfg)
        input_url = ""
        try:
            os.makedirs(UPLOADS_DIR, exist_ok=True)
            iname = "in_" + os.path.basename(tmp)
            ipath = os.path.join(UPLOADS_DIR, iname)
            if not os.path.exists(ipath):
                shutil.copyfile(tmp, ipath)
            input_url = "/uploads/" + iname
        except OSError:
            pass
        _task_progress(task, 10, "正在分析图片…")
        out, _, info, prompt = poster_core.generate(
            tmp, title=title, caption=caption, features=features,
            accent_override=accent, position_override=pos_core, style=style,
            size=size, out_dir=out_dir, key=cfg["bailian_key"],
            progress=lambda msg: _task_say(task, msg),
            out_name_base=name_base)
        theme = ""
        if (cfg.get("naming") or "auto") == "auto":
            _task_progress(task, 92, "正在智能命名…")
            theme = name_from_image(tmp, cfg) or ""
            if theme:
                analysis = poster_core.analyze_image(tmp)
                acc_part = accent[0].split()[0] if accent else analysis["accent_name"].split()[0]
                pos_part = pos_core or analysis["position"]
                base_name = "poster-%s-%s-%s" % (theme, acc_part, pos_part)
                new_path = os.path.join(out_dir, base_name + ".png")
                i = 1
                while os.path.exists(new_path):
                    new_path = os.path.join(out_dir, "%s-%d.png" % (base_name, i))
                    i += 1
                os.rename(out, new_path)
                out = new_path
        append_history({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user": username,
            "ip": task.get("ip") or "",
            "file": name_base or "",
            "theme": theme,
            "title": title or "",
            "accent": accent[0] if accent else "",
            "position": pos_fe or "",
            "size": size,
            "style": style,
            "input_url": input_url,
            "prompt": prompt,
            "url": "/outputs/" + os.path.basename(out),
        })
        with _TASK_LOCK:
            task["state"] = "done"
            task["progress"] = 100
            task["message"] = "生成完成"
            task["url"] = "/outputs/" + os.path.basename(out)
            task["info"] = info
    except RuntimeError as e:
        with _TASK_LOCK:
            task["state"] = "error"
            task["error"] = str(e)
    except Exception as e:
        with _TASK_LOCK:
            task["state"] = "error"
            task["error"] = "服务器错误：" + str(e)


# ---------- 用户系统 ----------

def load_users():
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                            salt.encode("utf-8"), 120000).hex()
    return salt + "$" + h


def verify_password(password, stored):
    try:
        salt, h = stored.rsplit("$", 1)
    except ValueError:
        return False
    calc = hash_password(password, salt).split("$", 1)[1]
    return hmac.compare_digest(calc, h)


def new_session(username):
    tok = secrets.token_hex(32)
    SESSIONS[tok] = {"username": username, "expires": time.time() + 30 * 86400}
    save_sessions()
    return tok


def session_user(self):
    hdr = self.headers.get("Authorization") or ""
    if hdr.startswith("Bearer "):
        sess = SESSIONS.get(hdr[7:].strip())
        if sess and sess["expires"] > time.time():
            return sess["username"]
    return None


def out_dir_of(cfg):
    return cfg.get("output_dir") or OUT_DIR


def safe_filename(s):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", s or "")
    return s.strip(" .，。、\"'“”")


def name_from_image(img_path, cfg):
    """用 qwen-vl-max 概括图片主题（2-5 汉字）用于文件名；失败返回 None。"""
    try:
        from PIL import Image as PILImage
        ph = PILImage.open(img_path).convert("RGB")
        ph.thumbnail((1024, 1024), PILImage.LANCZOS)
        buf = io.BytesIO()
        ph.save(buf, "JPEG", quality=88)
        b64 = base64.b64encode(buf.getvalue()).decode()
        body = {"model": "qwen-vl-max", "input": {"messages": [{"role": "user", "content": [
            {"image": "data:image/jpeg;base64," + b64},
            {"text": "用 2-5 个汉字概括这张照片的主题，适合用作文件名。只输出词语本身，不要标点、引号或解释。"}]}]},
            "parameters": {}}
        req = urllib.request.Request(poster_core.API_URL, data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + cfg["bailian_key"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode())
        text = d["output"]["choices"][0]["message"]["content"][0]["text"]
        text = safe_filename(text)
        return text[:12] or None
    except Exception:
        return None


def save_temp_image(data_url):
    """dataURL -> 临时图片文件，返回路径；失败抛 ValueError。"""
    m = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.*)$", data_url or "", re.S)
    if not m:
        raise ValueError("图片数据格式不正确")
    raw = base64.b64decode(m.group(1))
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("图片过大（超过 25MB）")
    os.makedirs(TMP_DIR, exist_ok=True)
    tmp = os.path.join(TMP_DIR, "up_%s.jpg" % hashlib.md5(raw).hexdigest()[:16])
    with open(tmp, "wb") as f:
        f.write(raw)
    try:
        from PIL import Image
        Image.open(tmp).verify()
    except Exception as e:
        safe_unlink(tmp)
        raise ValueError("无法读取图片：%s" % e)
    return tmp


def safe_unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def call_deepseek(cfg, prompt, max_tokens=4000):
    body = {
        "model": cfg.get("deepseek_model") or "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "你是给复古档案风海报写中文文案的诗人。用户会给出主题词，你只回复一句中文短句（8-14 字），安静、有画面感、不煽情。不要任何解释、不要引号。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + cfg["deepseek_key"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode())
        line = d["choices"][0]["message"]["content"].strip()
        return line.strip("\"'\u201c\u201d\n")
    except urllib.error.HTTPError as e:
        e.read()
        if e.code == 401:
            raise RuntimeError("DeepSeek Key 无效，请到「模型设置」检查")
        if e.code == 402:
            raise RuntimeError("DeepSeek 账户余额不足，请到 DeepSeek 平台充值后重试")
        if e.code == 429:
            raise RuntimeError("DeepSeek 触发限流，请稍后再试")
        raise RuntimeError("DeepSeek 接口错误（%d）" % e.code)
    except urllib.error.URLError as e:
        raise RuntimeError("DeepSeek 网络错误：%s" % e.reason)


class Handler(BaseHTTPRequestHandler):
    server_version = "PosterGen/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---------- 基础 ----------
    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self, limit=30 * 1024 * 1024):
        ln = int(self.headers.get("Content-Length") or 0)
        if ln <= 0:
            return b"{}"
        if ln > limit:
            raise ValueError("请求体过大")
        return self.rfile.read(ln)

    def _serve_file(self, fp, ctype):
        if not os.path.isfile(fp):
            self._json({"error": "not found"}, 404)
            return
        with open(fp, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ---------- GET ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/config":
            cfg = load_config()
            self._json({
                "bailian": {"key_set": bool(cfg["bailian_key"]), "key_mask": mask_key(cfg["bailian_key"])},
                "deepseek": {"key_set": bool(cfg["deepseek_key"]), "key_mask": mask_key(cfg["deepseek_key"])},
                "image_model": cfg["image_model"],
                "deepseek_model": cfg["deepseek_model"],
                "deepseek_models": cfg["deepseek_models"],
                "output_dir": cfg.get("output_dir") or "",
                "naming": cfg.get("naming") or "auto",
                "deployed": is_deployed(),
                "admin_required": bool(admin_token_of(cfg)),
                "size": cfg["size"],
            })
        elif path.startswith("/api/task/"):
            self._get_task(path)
        elif path.startswith("/api/file"):
            username = session_user(self)
            if not username:
                self._json({"error": "请先登录"}, 401)
                return
            qs = parse_qs(urlparse(self.path).query)
            name = os.path.basename(qs.get("name", [""])[0])
            fp = os.path.join(UPLOADS_DIR, name)
            if not os.path.isfile(fp):
                self._json({"error": "文件不存在"}, 404)
                return
            users = load_users()
            is_admin = bool(users.get(username, {}).get("admin"))
            allowed = is_admin or any(
                r.get("user") == username and r.get("input_url", "").endswith("/" + name)
                for r in load_history())
            if not allowed:
                self._json({"error": "无权访问"}, 403)
                return
            self._serve_file(fp, "image/jpeg")
        elif path.startswith("/outputs/"):
            name = os.path.basename(unquote(urlparse(self.path).path))
            ctype = "image/png" if name.lower().endswith(".png") else "application/octet-stream"
            self._serve_file(os.path.join(out_dir_of(load_config()), name), ctype)
        elif path in ("/", "/index.html"):
            self._serve_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._json({"error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            raw = self._read_body()
        except ValueError as e:
            self._json({"error": str(e)}, 413)
            return
        try:
            if path == "/api/config":
                self._post_config(raw)  # 公开（仅掩码状态）
            elif path == "/api/register":
                self._post_register(raw)
            elif path == "/api/login":
                self._post_login(raw)
            elif path == "/api/me":
                self._post_me(raw)
            elif path == "/api/history":
                self._post_history(raw)
            elif path == "/api/admin/users":
                self._post_admin_users(raw)
            elif path == "/api/admin/users/create":
                self._post_admin_users_create(raw)
            elif path == "/api/admin/users/delete":
                self._post_admin_users_delete(raw)
            elif path == "/api/admin/users/set-password":
                self._post_admin_users_set_password(raw)
            elif path == "/api/admin/history":
                self._post_admin_history(raw)
            elif path == "/api/admin/history/delete":
                self._post_admin_history_delete(raw)
            elif path == "/api/analyze":
                self._post_analyze(raw)
            elif path == "/api/copy":
                self._post_copy(raw)
            elif path == "/api/generate":
                self._post_generate(raw)
            elif path == "/api/open-output-folder":
                self._post_open_folder(raw)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": "服务器错误：" + str(e)}, 500)

    def _load_json(self, raw):
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise RuntimeError("JSON 格式错误")

    def _get_task(self, path):
        if not self._require_user():
            return
        task_id = os.path.basename(path)
        with _TASK_LOCK:
            t = TASKS.get(task_id)
        if not t:
            self._json({"error": "任务不存在或已过期"}, 404)
            return
        self._json({k: t.get(k) for k in ("state", "progress", "message", "url", "info", "error")})

    def _post_config(self, raw):
        data = self._load_json(raw)
        cfg = load_config()
        deployed = is_deployed()
        if not deployed:
            if data.get("bailian_key"):
                cfg["bailian_key"] = str(data["bailian_key"]).strip()
            if data.get("deepseek_key"):
                cfg["deepseek_key"] = str(data["deepseek_key"]).strip()
            if data.get("output_dir"):
                cfg["output_dir"] = str(data["output_dir"]).strip()
            if data.get("admin_token") is not None:
                cfg["admin_token"] = str(data["admin_token"]).strip()
        for k in ("image_model", "deepseek_model", "size"):
            if data.get(k):
                cfg[k] = str(data[k]).strip()
        if data.get("naming") in ("auto", "file"):
            cfg["naming"] = data["naming"]
        save_config(cfg)  # 非密钥字段（模型/命名/尺寸）始终持久化
        self._json({"ok": True, "deployed": deployed})

    def _require_user(self):
        username = session_user(self)
        if not username:
            self._json({"error": "请先登录"}, 401)
            return None
        return username

    def _post_register(self, raw):
        data = self._load_json(raw)
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not re.match(r"^[\w\u4e00-\u9fa5-]{2,20}$", username):
            self._json({"error": "用户名需 2-20 位（中文/字母/数字/下划线/连字符）"}, 400)
            return
        if len(password) < 6:
            self._json({"error": "密码至少 6 位"}, 400)
            return
        users = load_users()
        if username in users:
            self._json({"error": "用户名已存在"}, 400)
            return
        is_admin = len(users) == 0  # 第一个注册用户自动成为管理员
        users[username] = {"pwd": hash_password(password), "admin": is_admin,
                           "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        save_users(users)
        self._json({"ok": True, "token": new_session(username),
                    "username": username, "is_admin": is_admin})

    def _post_login(self, raw):
        data = self._load_json(raw)
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        users = load_users()
        stored = users.get(username)
        if not stored or not verify_password(password, stored.get("pwd", "")):
            self._json({"error": "用户名或密码错误"}, 401)
            return
        self._json({"ok": True, "token": new_session(username),
                    "username": username, "is_admin": bool(stored.get("admin"))})

    def _post_me(self, raw):
        username = self._require_user()
        if not username:
            return
        users = load_users()
        self._json({"username": username, "is_admin": bool(users.get(username, {}).get("admin"))})

    def _post_history(self, raw):
        username = self._require_user()
        if not username:
            return
        recs = [r for r in load_history() if r.get("user") == username]
        self._json({"records": recs})

    def _require_admin(self):
        username = self._require_user()
        if not username:
            return None
        users = load_users()
        if not users.get(username, {}).get("admin"):
            self._json({"error": "无权访问"}, 403)
            return None
        return username

    def _post_admin_users(self, raw):
        if not self._require_admin():
            return
        users = load_users()
        recs = load_all_history()
        counts = {}
        for r in recs:
            counts[r.get("user", "")] = counts.get(r.get("user", ""), 0) + 1
        lst = [{"username": u, "created": v.get("created", ""),
                "gen_count": counts.get(u, 0)}
               for u, v in users.items()]
        lst.sort(key=lambda x: x["created"])
        self._json({"users": lst})

    def _post_admin_users_create(self, raw):
        if not self._require_admin():
            return
        data = self._load_json(raw)
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not re.match(r"^[\w\u4e00-\u9fa5-]{2,20}$", username):
            self._json({"error": "用户名需 2-20 位（中文/字母/数字/下划线/连字符）"}, 400)
            return
        if len(password) < 6:
            self._json({"error": "密码至少 6 位"}, 400)
            return
        users = load_users()
        if username in users:
            self._json({"error": "用户名已存在"}, 400)
            return
        users[username] = {"pwd": hash_password(password), "admin": False,
                           "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        save_users(users)
        self._json({"ok": True})

    def _post_admin_users_delete(self, raw):
        username = self._require_admin()
        if not username:
            return
        data = self._load_json(raw)
        target = str(data.get("username") or "").strip()
        users = load_users()
        if target == username:
            self._json({"error": "不能删除当前账号"}, 400)
            return
        if target not in users:
            self._json({"error": "账号不存在"}, 404)
            return
        users.pop(target)
        save_users(users)
        # 连带删除该用户的所有生成记录
        with _LOCK:
            recs = load_all_history()
            recs = [r for r in recs if r.get("user") != target]
            rewrite_history(recs)
        # 使其所有会话失效
        for tok in [k for k, v in SESSIONS.items() if v.get("username") == target]:
            SESSIONS.pop(tok, None)
        save_sessions()
        self._json({"ok": True})

    def _post_admin_users_set_password(self, raw):
        if not self._require_admin():
            return
        data = self._load_json(raw)
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if len(password) < 6:
            self._json({"error": "密码至少 6 位"}, 400)
            return
        users = load_users()
        if username not in users:
            self._json({"error": "账号不存在"}, 404)
            return
        users[username]["pwd"] = hash_password(password)
        save_users(users)
        self._json({"ok": True})

    def _post_admin_history(self, raw):
        if not self._require_admin():
            return
        data = self._load_json(raw)
        ensure_history_ids()
        recs = load_all_history()
        user_filter = str(data.get("user") or "").strip()
        if user_filter:
            recs = [r for r in recs if r.get("user") == user_filter]
        recs.reverse()
        self._json({"records": recs})

    def _post_admin_history_delete(self, raw):
        if not self._require_admin():
            return
        data = self._load_json(raw)
        rid = str(data.get("id") or "")
        if not rid:
            self._json({"error": "缺少记录 id"}, 400)
            return
        with _LOCK:
            recs = load_all_history()
            keep = [r for r in recs if r.get("id") != rid]
            if len(keep) == len(recs):
                self._json({"error": "记录不存在"}, 404)
                return
            rewrite_history(keep)
        self._json({"ok": True})

    def _post_open_folder(self, raw):
        if not self._require_user():
            return
        os.startfile(out_dir_of(load_config()))
        self._json({"ok": True})

    def _post_analyze(self, raw):
        if not self._require_user():
            return
        data = self._load_json(raw)
        try:
            tmp = save_temp_image(data.get("image"))
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        try:
            a = poster_core.analyze_image(tmp)
        except Exception as e:
            self._json({"error": "分析失败：" + str(e)}, 400)
            return
        finally:
            safe_unlink(tmp)
        a["position"] = POS_CORE2FE.get(a["position"], a["position"])
        a["sat"] = round(a["sat"], 2)
        a["hue"] = round(a["hue"], 1)
        self._json(a)

    def _post_copy(self, raw):
        if not self._require_user():
            return
        data = self._load_json(raw)
        kw = (data.get("keywords") or "").strip()
        if not kw:
            self._json({"error": "请先填写主题词"}, 400)
            return
        cfg = load_config()
        if not cfg.get("deepseek_key"):
            self._json({"error": "未配置 DeepSeek API Key，请先到「模型设置」填写"}, 400)
            return
        prompt = ("为一张照片写一句诗意中文短句，8-14 字，安静、有画面感、不煽情，"
                  "贴合复古档案风海报美学；只输出这一句，不要引号、不要解释。主题：%s" % kw)
        try:
            line = call_deepseek(cfg, prompt)
        except RuntimeError as e:
            self._json({"error": str(e)}, 502)
            return
        self._json({"line": line})

    def _post_generate(self, raw):
        username = self._require_user()
        if not username:
            return
        data = self._load_json(raw)
        cfg = load_config()
        if not cfg.get("bailian_key"):
            self._json({"error": "未配置百炼 API Key，请先到「模型设置」填写"}, 400)
            return
        try:
            tmp = save_temp_image(data.get("image"))
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        task_id = secrets.token_hex(8)
        task = {"state": "queued", "progress": 0, "message": "任务已提交，准备开始…",
                "url": None, "info": None, "error": None,
                "ip": self.client_address[0]}
        with _TASK_LOCK:
            TASKS[task_id] = task
            if len(TASKS) > 200:
                for k in [k for k, v in TASKS.items()
                          if v.get("state") in ("done", "error")][:60]:
                    TASKS.pop(k, None)
        threading.Thread(target=_run_generate_task,
                         args=(task_id, username, data, cfg, tmp),
                         daemon=True).start()
        self._json({"task_id": task_id})


def main():
    deployed = is_deployed()
    host = os.environ.get("POSTER_HOST") or ("0.0.0.0" if deployed else "127.0.0.1")
    port = int(os.environ.get("POSTER_PORT") or os.environ.get("PORT") or
               (sys.argv[1] if len(sys.argv) > 1 else 8765))
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    load_config()  # 首次启动自动迁移百炼 Key
    load_sessions()  # 恢复持久化会话
    srv = None
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer((host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        sys.exit("端口 %d-%d 均被占用" % (port, port + 9))
    url = "http://127.0.0.1:%d" % port
    print("Zine 海报生成器已启动: %s  （Ctrl+C 退出）" % url)
    if not deployed:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
