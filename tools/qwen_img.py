# -*- coding: utf-8 -*-
"""qwen_img：用千问 qwen-image-3.0 生成一张图（单次 I2I/文生图）。

给「真正跑 Skills」用：agent 按技能流程编译好提示词后，用这个命令出图。

用法：
    python tools/qwen_img.py -p "<提示词>" [--input <参考图>] [--size 900*1500] [--out out.png]

Key 读取顺序：环境变量 POSTER_API_KEY/DASHSCOPE_API_KEY -> 仓库 config.json（bailian_key）
            -> ~/.codex/skills/vision/.env（VISION_API_KEY）
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

from PIL import Image

API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
MODEL = "qwen-image-3.0"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # poster-gen 仓库根
DEFAULT_ENV = os.path.expanduser(r"~\.codex\skills\vision\.env")


def load_key():
    for name in ("POSTER_API_KEY", "DASHSCOPE_API_KEY"):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    cfg = os.path.join(BASE, "config.json")
    if os.path.exists(cfg):
        try:
            k = json.load(open(cfg, encoding="utf-8")).get("bailian_key", "").strip()
            if k:
                return k
        except Exception:
            pass
    if os.path.exists(DEFAULT_ENV):
        for line in open(DEFAULT_ENV, encoding="utf-8"):
            if line.strip().startswith("VISION_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--prompt", default=None, help="生成提示词（与 --prompt-file 二选一）")
    ap.add_argument("--prompt-file", default=None, help="从 UTF-8 文件读取提示词")
    ap.add_argument("--input", default=None, help="可选参考图（I2I）")
    ap.add_argument("--size", default="900*1500", help="输出尺寸，如 900*1500")
    ap.add_argument("--out", required=True, help="输出图片路径")
    ap.add_argument("--n", type=int, default=1, help="连出几张（默认 1）")
    args = ap.parse_args(argv)

    if args.prompt_file:
        prompt = open(args.prompt_file, encoding="utf-8").read().strip()
    else:
        prompt = (args.prompt or "").strip()
    if not prompt:
        print("EMPTY_PROMPT")
        return 1

    key = load_key()
    if not key:
        print("NO_KEY: 未找到千问 Key（POSTER_API_KEY / config.json / vision .env）")
        return 1

    content = [{"text": prompt}]
    if args.input:
        im = Image.open(args.input).convert("RGB")
        im.thumbnail((1536, 1536), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95)
        content = [{"image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}] + content

    body = {
        "model": MODEL,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"size": args.size, "n": args.n},
    }
    res = None
    for attempt in range(4):
        req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                res = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < 3:
                wait = 5 * (2 ** attempt)
                print("HTTP_429 限流，%d 秒后重试…" % wait, flush=True)
                time.sleep(wait)
                continue
            if "Arrearage" in err or "overdue" in err.lower() or "欠费" in err:
                print("HTTP_%s 账号欠费/逾期：请到阿里云百炼控制台充值并确认账户状态正常。详情: %s" % (e.code, err[:200]))
            else:
                print("HTTP_ERR", e.code, err[:300])
            return 1
    if res is None:
        print("多次重试仍限流，请稍后再试")
        return 1
    try:
        img_url = res["output"]["choices"][0]["message"]["content"][0]["image"]
    except (KeyError, IndexError, TypeError):
        print("接口返回格式异常（未取到图片），请重试")
        return 1
    raw = urllib.request.urlopen(img_url, timeout=120).read()
    with open(args.out, "wb") as f:
        f.write(raw)
    print("OK", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
