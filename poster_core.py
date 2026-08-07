# -*- coding: utf-8 -*-
"""poster-core：自适应 zine 海报生成核心（GUI 与命令行共用）。

工作方式（纯程序化，不用任何视觉模型读图）：
1. 分析图片主色相/饱和度 -> 决定强调色（低饱和撞色 / 高饱和适配主色相）
2. 分析边缘能量九宫格 -> 决定主体放置位置
3. 生成 prompt 调用阿里云百炼 qwen-image-3.0（I2I）出图
4. 程序化校验输出（格式/尺寸），返回结果
"""
import argparse
import base64
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

from PIL import Image, ImageFilter

API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
MODEL = "qwen-image-3.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT_DIR = os.path.join(BASE_DIR, "outputs")
LOCAL_ENV_FILE = os.path.join(BASE_DIR, ".env")
DEFAULT_ENV_FILE = os.path.expanduser(r"~\.codex\skills\vision\.env")

ACCENT_COLORS = [
    ("ultramarine blue", "#2549E8"),
    ("deep cyan", "#00A0A0"),
    ("violet", "#5B3FD4"),
    ("cobalt blue", "#0047AB"),
    ("tomato red", "#E8432F"),
    ("vermilion orange", "#E2541B"),
    ("mustard yellow", "#D9A400"),
    ("rust orange", "#B5532B"),
]
POSITIONS = [
    "upper-left", "upper-middle", "upper-right",
    "center-left", "center", "center-right",
    "lower-left", "lower-middle", "lower-right",
]
POSITION_LABELS = {
    "upper-left": "左上", "upper-middle": "上中", "upper-right": "右上",
    "center-left": "左中", "center": "居中", "center-right": "右中",
    "lower-left": "左下", "lower-middle": "下中", "lower-right": "右下",
}
GENERIC_FEATURES = (
    "Keep the main subject of the photograph and its signature details recognizable: "
    "its shape, materials, any readable text, labels, logos, icons and its material palette."
)


def load_api_key():
    """优先级：环境变量 POSTER_API_KEY/DASHSCOPE_API_KEY -> 工具目录 .env -> vision skill .env"""
    for name in ("POSTER_API_KEY", "DASHSCOPE_API_KEY"):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    for env_file in (LOCAL_ENV_FILE, DEFAULT_ENV_FILE):
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("VISION_API_KEY="):
                        v = line.split("=", 1)[1].strip()
                        if v:
                            return v
        except OSError:
            continue
    return ""


def save_api_key(key):
    with open(LOCAL_ENV_FILE, "w", encoding="utf-8") as f:
        f.write("VISION_API_KEY=" + key.strip() + "\n")


def _rgb2hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    sat = d / mx if mx else 0.0
    if d == 0:
        hue = 0.0
    elif mx == r:
        hue = 60 * (((g - b) / d) % 6)
    elif mx == g:
        hue = 60 * ((b - r) / d + 2)
    else:
        hue = 60 * ((r - g) / d + 4)
    return hue, sat, mx


def _hex2hue(hexcode):
    r = int(hexcode[1:3], 16)
    g = int(hexcode[3:5], 16)
    b = int(hexcode[5:7], 16)
    return _rgb2hsv(r, g, b)[0]


def _temp_of(hue):
    if hue < 70 or hue > 320:
        return "warm"
    if 140 <= hue <= 280:
        return "cool"
    return "neutral"


def analyze_image(path):
    """纯程序分析：主色相、饱和度、冷暖、建议强调色、建议位置。"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    small = im.resize((64, 64))
    px = list(small.getdata())
    top = Counter(px).most_common(4)
    tot_w = sum(c for _, c in top)
    hue_acc = sat_acc = 0.0
    for (r, g, b), c in top:
        hue, sat, _ = _rgb2hsv(r, g, b)
        hue_acc += hue * c
        sat_acc += sat * c
    hue_main = hue_acc / tot_w
    sat_main = sat_acc / tot_w

    if sat_main < 0.35:
        temp = _temp_of(hue_main)
        if temp == "warm":
            pool = ACCENT_COLORS[:4]
        elif temp == "cool":
            pool = ACCENT_COLORS[4:]
        else:
            pool = ACCENT_COLORS[:4] if hue_main < 180 else ACCENT_COLORS[4:]
        idx = int(hashlib.md5(os.path.basename(path).encode("utf-8")).hexdigest(), 16) % len(pool)
        accent_name, accent_hex = pool[idx]
        strategy = "clash"
    else:
        def hue_dist(a, b):
            d = abs(a - b) % 360
            return min(d, 360 - d)
        accent_name, accent_hex = min(
            ACCENT_COLORS, key=lambda x: hue_dist(hue_main, _hex2hue(x[1])))
        strategy = "fit"

    gray = im.convert("L").resize((90, 90))
    edges = gray.filter(ImageFilter.FIND_EDGES)
    ew = edges.load()
    cells = {}
    for r in range(3):
        for c in range(3):
            s = 0
            for y in range(r * 30, (r + 1) * 30):
                for x in range(c * 30, (c + 1) * 30):
                    s += ew[x, y]
            cells[(r, c)] = s
    br, bc = max(cells, key=cells.get)
    position = POSITIONS[br * 3 + bc]

    return {
        "size": (w, h), "hue": hue_main, "sat": sat_main,
        "temp": _temp_of(hue_main), "strategy": strategy,
        "accent_name": accent_name, "accent_hex": accent_hex,
        "position": position, "position_label": POSITION_LABELS[position],
    }


def build_prompt(analysis, title=None, caption=None, features=None,
                 accent_override=None, position_override=None):
    accent_name, accent_hex = analysis["accent_name"], analysis["accent_hex"]
    if accent_override:
        accent_name, accent_hex = accent_override
    position = position_override or analysis["position"]
    if position in ("upper-left", "upper-middle", "upper-right"):
        verb = "placed in the upper area"
    elif position in ("lower-left", "lower-middle", "lower-right"):
        verb = "placed in the lower area"
    else:
        verb = "placed in the center"

    if features and features.strip():
        feature_block = features.strip()
    else:
        feature_block = GENERIC_FEATURES

    if title and title.strip():
        main_line = 'The main text is the line: "%s"' % title.strip()
    else:
        main_line = "One short poetic Chinese line as the main text"
    if caption and caption.strip():
        sub_line = 'a tiny archive microtext line with the date and caption: "%s"' % caption.strip()
    else:
        sub_line = "one date, one small caption"

    return f"""Edit this photograph into a quiet minimal zine poster. Keep the photographed subject and its signature elements clearly recognizable, do not replace the subject, but RESIZE and REDUCE the whole photographed scene into ONE SMALL cutout anchor occupying about 15% of the canvas; fill 80% of the canvas with plain aged paper, do not keep the full-frame photo. Tall vertical 3:5 phone-poster on warm aged cream paper, 75%-85% plain empty paper, no border, no mockup.
One small visual cluster occupying about 10%-16% of the canvas, {verb} ({position}): an old photocopy photo crop with a softened, worn edge. The cluster is a stylized reinterpretation of the photographed subject, not a photographic reproduction. {feature_block} Keep the recognizable signature elements of the photo clearly present, drawn neatly and readable.
Small serif/typewriter typography: small serif letterpress line with tiny archive microtext below. {main_line}; tiny archive microtext lines below it ({sub_line}), one line slightly drifted. All text must be perfectly readable, no gibberish.
The blank paper carries sparse archival decoration in the quiet case-study manner: one small rubber-stamp mark, two or three tiny asterisk or star glyphs, one faint thin ruled line, a small handwritten catalog number or date, and one tiny dotted trail; all small, faint and sepia-toned, printed with the same xerox wear as the paper; never dense, never a second high-chroma color.
One unmistakably high-chroma {accent_name} ({accent_hex}) anchor occupying about 1%-2% of the whole canvas, clearly visible at thumbnail size; it may be part of the subject, a flat silhouette, an irregular cutout, a substantial block, or bold fragmented type; do not reduce it to a tiny dot or hairline. Print quality: xerox grain and misregistration, aged-paper mottling, scan dust.
Flat orthographic scanned-paper view, quiet memory-like archival diary feeling. Avoid: full-bleed scene, commercial poster headline, product ad layout, logo and CTA, glossy mockup, clean digital UI, cinematic lighting, 3D depth, neon, cartoon, dense collage, extra colors, long clean text blocks."""


def _friendly_error(code, body, reason):
    body = (body or "").strip().replace("\n", " ")
    if code == 403:
        if "AllocationQuota.FreeTierOnly" in body:
            return ("403：账号当前处于「仅免费额度」模式或免费额度已用完。"
                    "请到阿里云百炼控制台检查「仅免费额度」开关或开通付费后重试。")
        return "403：接口拒绝访问（配额或权限问题）。" + (body[:160] if body else "")
    if code == 400 and "model not exist" in body.lower():
        return "400：模型不存在，请检查 MODEL 配置（应为 qwen-image-3.0）。"
    return f"接口错误 {code}：{reason}。" + (body[:160] if body else "")


def generate(path, title=None, caption=None, features=None,
             accent_override=None, position_override=None,
             size="900*1500", out_dir=DEFAULT_OUT_DIR, key=None,
             progress=None, out_name_base=None):
    def say(msg):
        if progress:
            progress(msg)

    key = key or load_api_key()
    if not key:
        raise RuntimeError("未找到 API Key：请在工具里粘贴保存，或配置 POSTER_API_KEY 环境变量")

    analysis = analyze_image(path)
    say(f"分析完成：强调色 {analysis['accent_name']} / 位置 {analysis['position_label']}")

    prompt = build_prompt(analysis, title, caption, features, accent_override, position_override)

    im = Image.open(path).convert("RGB")
    ph = im.copy()
    ph.thumbnail((1536, 1536), Image.LANCZOS)
    buf = io.BytesIO()
    ph.save(buf, "JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode()

    body = {
        "model": MODEL,
        "input": {"messages": [{"role": "user", "content": [
            {"image": "data:image/jpeg;base64," + b64},
            {"text": prompt}]}]},
        "parameters": {"size": size, "n": 1},
    }
    data = json.dumps(body).encode()
    say("正在调用 qwen-image-3.0 生成（约 1 分钟）…")

    result = None
    for attempt in range(4):
        req = urllib.request.Request(API_URL, data=data, headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                result = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code == 429 and attempt < 3:
                wait = 5 * (2 ** attempt)
                say(f"触发限流(429)，{wait} 秒后自动重试…")
                time.sleep(wait)
                continue
            raise RuntimeError(_friendly_error(e.code, err_body, e.reason))
        except urllib.error.URLError as e:
            raise RuntimeError(f"网络错误：{e.reason}")
    if result is None:
        raise RuntimeError("多次重试仍限流，请稍后再试")

    try:
        img_url = result["output"]["choices"][0]["message"]["content"][0]["image"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("接口返回格式异常（未取到图片），请重试")
    say("正在下载结果…")
    raw = urllib.request.urlopen(img_url, timeout=120).read()

    os.makedirs(out_dir, exist_ok=True)
    base = out_name_base or os.path.splitext(os.path.basename(path))[0]
    acc = accent_override[0].split()[0] if accent_override else analysis["accent_name"].split()[0]
    pos = position_override or analysis["position"]
    out_path = os.path.join(out_dir, f"poster-{base}-{acc}-{pos}.png")
    with open(out_path, "wb") as f:
        f.write(raw)

    check = Image.open(out_path)
    info = {"format": check.format, "mode": check.mode, "size": check.size}
    say(f"已保存并校验：{os.path.basename(out_path)}")
    return out_path, analysis, info, prompt


def main(argv=None):
    ap = argparse.ArgumentParser(description="自适应 zine 海报生成（命令行模式）")
    ap.add_argument("--input", required=True, help="输入图片路径")
    ap.add_argument("--title", default=None, help="主文案（留空自动生成）")
    ap.add_argument("--caption", default=None, help="日期/小字（留空自动）")
    ap.add_argument("--accent", default=None, help="强调色：色名或 #HEX（留空自动）")
    ap.add_argument("--position", default=None, choices=POSITIONS, help="主体位置（留空自动）")
    ap.add_argument("--features-file", default=None, help="主体特征描述文件（可选）")
    ap.add_argument("--size", default="900*1500", help="输出尺寸，如 900*1500")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="输出目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印分析与提示词，不调用 API")
    args = ap.parse_args(argv)

    analysis = analyze_image(args.input)
    print(f"图片: {args.input} ({analysis['size'][0]}x{analysis['size'][1]}) "
          f"主色相={analysis['hue']:.0f}° 饱和度={analysis['sat']:.2f} "
          f"冷暖={analysis['temp']} 策略={analysis['strategy']}")
    print(f"建议强调色: {analysis['accent_name']} {analysis['accent_hex']}  "
          f"建议位置: {analysis['position_label']} ({analysis['position']})")

    accent_override = None
    if args.accent:
        a = args.accent.strip()
        if a.startswith("#"):
            accent_override = ("custom", a.upper())
        else:
            for name, hexcode in ACCENT_COLORS:
                if a == name or a == name.split()[0]:
                    accent_override = (name, hexcode)
                    break
            if not accent_override:
                raise SystemExit(f"未知强调色: {a}")

    features = None
    if args.features_file:
        with open(args.features_file, encoding="utf-8") as f:
            features = f.read().strip()

    prompt = build_prompt(analysis, args.title, args.caption, features,
                          accent_override, args.position)
    if args.dry_run:
        print("\n----- PROMPT -----\n" + prompt)
        return 0

    key = load_api_key()
    if not key:
        raise SystemExit("未找到 API Key")
    out, _, info, _ = generate(args.input, args.title, args.caption, features,
                              accent_override, args.position, args.size,
                              args.out_dir, key, progress=print)
    print(f"已保存: {out}  {info['format']}/{info['mode']} {info['size'][0]}x{info['size'][1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
