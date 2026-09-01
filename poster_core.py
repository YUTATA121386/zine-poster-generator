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
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
    "First inspect the photograph: identify its 1-2 core subjects, their relative "
    "position, the dominant gesture (the strongest line, path, gaze or silhouette), "
    "and one key spatial relationship. Keep these recognizable in the result."
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


STYLES = {
    "zine": "档案风",
    "photo-panel": "摄影抽象面板",
    "travel-abstract": "旅行抽象研究",
    "paper-collage": "拾景纸拼贴",
    "distillation": "影像蒸馏",
}


def build_prompt(analysis, title=None, caption=None, features=None,
                 accent_override=None, position_override=None, style="zine"):
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

    builder = {
        "zine": _prompt_zine,
        "photo-panel": _prompt_photo_panel,
        "travel-abstract": _prompt_travel_abstract,
        "travel-panel": _prompt_travel_panel,
        "paper-collage": _prompt_paper_collage,
        "distillation": _prompt_distillation,
    }.get(style or "zine", _prompt_zine)
    return builder(analysis, accent_name, accent_hex, position, verb,
                   feature_block, title, caption)


def _prompt_zine(analysis, accent_name, accent_hex, position, verb,
                 feature_block, title, caption):
    if title and title.strip():
        main_line = 'The main text is the line: "%s"' % title.strip()
    else:
        main_line = "One short poetic Chinese line as the main text"
    if caption and caption.strip():
        sub_line = 'a tiny archive microtext line with the date and caption: "%s"' % caption.strip()
    else:
        sub_line = "one date, one small caption"

    return f"""Edit this photograph into a quiet minimal zine poster. First inspect the photograph and identify the main subject and its signature elements, then compose the small cutout anchor around it. Keep the photographed subject and its signature elements clearly recognizable, do not replace the subject, but RESIZE and REDUCE the whole photographed scene into ONE SMALL cutout anchor occupying about 12%-18% of the canvas and never more than 20% of it; fill 80% of the canvas with plain aged paper, do not keep the full-frame photo. Tall vertical 3:5 phone-poster on warm aged cream paper, 75%-85% plain empty paper, no border, no mockup.
One small visual cluster occupying about 10%-16% of the canvas, {verb} ({position}): an old photocopy photo crop with a softened, worn edge. The cluster is a stylized reinterpretation of the photographed subject, not a photographic reproduction. {feature_block} Keep the recognizable signature elements of the photo clearly present, drawn neatly and readable.
Small serif/typewriter typography: small serif letterpress line with tiny archive microtext below. {main_line}; tiny archive microtext lines below it ({sub_line}), one line slightly drifted. All text must be perfectly readable, no gibberish.
The blank paper carries sparse archival decoration in the quiet case-study manner: one small rubber-stamp mark, two or three tiny asterisk or star glyphs, one faint thin ruled line, a small handwritten catalog number or date, and one tiny dotted trail; all small, faint and sepia-toned, printed with the same xerox wear as the paper; never dense, never a second high-chroma color.
One unmistakably high-chroma {accent_name} ({accent_hex}) anchor occupying about 1%-2% of the whole canvas, clearly visible at thumbnail size; it may be part of the subject, a flat silhouette, an irregular cutout, a substantial block, or bold fragmented type; do not reduce it to a tiny dot or hairline. Print quality: xerox grain and misregistration, aged-paper mottling, scan dust.
Flat orthographic scanned-paper view, quiet memory-like archival diary feeling. Avoid: full-bleed scene, large photograph dominating the poster, commercial poster headline, product ad layout, logo and CTA, glossy mockup, clean digital UI, cinematic lighting, 3D depth, neon, cartoon, dense collage, extra colors, long clean text blocks."""

def _prompt_photo_panel(analysis, accent_name, accent_hex, position, verb,
                        feature_block, title, caption):
    if title and title.strip():
        main_line = 'The title is the line: "%s"' % title.strip()
    else:
        main_line = "create one original poetic title of 2-5 words distilled from the photograph"
    if caption and caption.strip():
        sub_line = 'a short subtitle: "%s"' % caption.strip()
    else:
        sub_line = "no subtitle"

    return f"""Create a vertical editorial artwork from this photograph, composed of two cleanly joined zones: the faithful photograph above and an abstract memory panel below. The input photograph is the ONLY content source — do not invent new scenes, objects, colors or symbols. This is not a filter, a posterized photo, or a style transfer.

PHOTOGRAPHIC ZONE: keep the photograph itself truthful and unchanged in the upper main area; do not repaint, redraw, extend, add effects or reinterpret it; only scale it to fit. Adapt the split by the source: tall or architectural photos keep photography about 55%-68% of the height, wide photos about 38%-52%, balanced photos about 48%-58%. Join the photo and panel directly with no frame, shadow, collage, tape or mockup effect.

ABSTRACT PANEL: below the photo, one perfectly uniform light ivory panel near #F3F0E8 with no gradient, texture, shadow, vignette, grain or seam. Method: DECONSTRUCT - SELECTIVE PRESERVATION - ABSTRACT - RECONSTRUCT. First inspect the photograph internally and identify 3-6 decisive spatial facts (dominant masses, axes, counts, intervals, overlaps, depth, color roles, asymmetry, meaningful voids); discard surface texture and low-information detail; then rebuild only the retained relationships as one sparse abstract motif — never a miniature scene, a traced outline, a posterized photo, or a generic icon. Use one primary mark family (flat or softly organic color blocks, soft round masses, arcs or tapered strokes, short bars or stacked bands, simplified architectural masses) plus at most two supporting families (thin lines, small isolated dots, restrained figure ink-marks). Every mark must trace back to a real fact in the photograph; no invented decoration, no regular spacing, no symmetrical diagram, no flat vector-diagram look, no literal object icons (a triangle, circle or rectangle standing for an object). The motif must read as a quiet memory of the photograph first, an abstract composition second. Subject evidence: {feature_block}.
PEOPLE AND LANDMARKS: if the photograph contains people, render each person in the panel as one continuous irregular short vertical ink mark or gently tapered block — never separate heads, limbs, faces, or clothing. If it contains a landmark building or distinctive structure, keep at most one to three identity cues (a distinctive outer contour, an eave line, a tapering tower mass, an arch, a spire, a layered rhythm) and omit windows, masonry, brackets and surface detail.
The motif occupies about 30%-42% of the panel width and at most 28%-34% of its height, leaving 65%-80% clean empty space, positioned by the panel's own balance and the photograph's dominant gesture, with generous poetic negative space.

COLOR SYSTEM: extract colors only from the photograph, lowering saturation and reducing their number: one main color role ({accent_name} {accent_hex} as the primary accent), one dark structural role, one light neutral role, and at most one or two small accents; no neon, no invented complementary colors, no rainbow palette.

TITLE: inside the ivory panel below or beside the motif, {main_line}; {sub_line}. The title must be faithful to a real visual fact of the photograph, concise and poetic — never empty words like Memory, Dream or Moment, never a travel-promotion line. Render it in a restrained elegant editorial serif, in a darker color derived from the photograph (deep blue-grey, dark green, wine red, deep violet or charcoal, never pure black, never the brightest accent), bottom-left aligned or bottom-centered, clearly readable. No other text, numbers, dates, labels or watermarks anywhere.

Avoid: repainting the photograph, scene reconstruction, generative outpainting, filter look, posterized photo, vectorized tracing, complete illustration, dense decoration, invented symmetry, extra words, numbers, dates, color swatches, legend, watermark, gradients, uneven background, paper texture, grain, haze, drop shadows, mockup frames, 3D depth, cartoon style, flat diagram look, literal object icons, floating bright pills."""


def _prompt_travel_abstract(analysis, accent_name, accent_hex, position, verb,
                            feature_block, title, caption):
    return f"""Visual distillation study: the untouched full-frame photograph above, and one sparse abstract reconstruction panel below it on clean ivory paper. The lower panel is a new abstract composition derived ONLY from relationships in the upper photograph; it is never a miniature redraw, a style-converted copy, or a filtered photograph.

UPPER PHOTOGRAPH: preserve the photograph's content, exposure, color and detail as exactly as possible in the upper zone; do not restyle, repaint, filter, recrop or regenerate it; only scale it to fit. Do not let the lower panel bleed into it.

LOWER PANEL: one perfectly uniform flat field at or near #F3F0E8; no gradient, light falloff, glow, shadow, edge darkening, band, seam, grain, noise, paper texture, fibers, haze, vignette, stains or color cast. DECONSTRUCT the photo into dominant masses, axes, boundaries, counts, directions, overlaps, intervals, depth, color roles, asymmetry and meaningful voids; remove photographic surface, literal object outlines, perspective detail and minor objects; RECONSTRUCT only the retained relationships as minimal marks using this mapping: mass or field -> one clean flat block or quiet plane; compact object -> dot, circle, pill, short line or tiny silhouette; horizon or boundary -> one thin line; direction or motion -> taper, streak, aligned bars or directional sequence; repeated objects -> repeated modules preserving source spacing and scale hierarchy; radial structure -> partial arc plus selected spokes and nodes; enclosure or overlap -> nested or overlapping shapes without completing hidden content; reflection or shadow -> shortened, lighter echo aligned to its source. Subject evidence: {feature_block}. Use one primary mark family and at most two supporting families; preserve source asymmetry and irregular spacing; allow measured displacement, compression, separation, overlap and scale change only when they clarify the source relationships; never rearrange arbitrarily. Keep the photograph's 2-3 most recognizable traits clearly readable: the main subject's silhouette, pose or distribution must stay identifiable at a glance — a building, person, tree, mountain, vehicle or object must remain legible in the marks, not dissolve into unreadable dots. Express these traits as abstract masses, fields and rhythms rather than a literal outline of the objects.
The complete motif occupies about 55%-75% of the panel width and 40%-55% of its height, leaving 30%-45% visually empty; it forms ONE coherent readable research group whose placement follows the panel's negative space and the photograph's dominant gesture — never pinned to a corner, edge or center by rote; scale it as one group without altering internal relationships.

COLOR: extract 3-5 color roles from the photograph preserving their saturation and luminance hierarchy; slightly prefer assigning different sampled color roles to different meaningful marks; use {accent_name} ({accent_hex}) as the main accent role with 2-3 restrained accents at most; no invented neon, rainbow palettes, glossy gradients or global muting.

TEXT: generate NO text, NO numbers, NO letters, NO symbols of any kind anywhere in the image. The application will overlay the archive details afterwards.

Avoid: photograph-like lower panel, miniature scene redraw, recognizable object redraw, generic style transfer, vectorization, posterization, literal tracing, complete illustration, decorative filler, regularized spacing, invented symmetry, invented content, any text, color swatches, watermark, gradient background, uneven background, visible grain, dirty paper, yellow cast, gray veil, haze, flat object icons, literal diagrams."""


def _prompt_travel_panel(analysis, accent_name, accent_hex, position, verb,
                         feature_block, title, caption):
    return f"""Create a sparse abstract reconstruction panel, vertical, on one perfectly uniform flat field at or near #F3F0E8 — no gradient, texture, grain, seam, glow or shadow anywhere. DECONSTRUCT the input photograph into dominant masses, axes, boundaries, counts, directions, overlaps, intervals, depth, color roles, asymmetry and meaningful voids; remove photographic surface, literal outlines, perspective detail and minor objects; RECONSTRUCT only the retained relationships as minimal marks using this mapping: mass or field -> one clean flat block or quiet plane; compact object -> dot, circle, pill, short line or tiny silhouette; horizon or boundary -> one thin line; direction or motion -> taper, streak, aligned bars or directional sequence; repeated objects -> repeated modules preserving source spacing and scale hierarchy; radial structure -> partial arc plus selected spokes and nodes; enclosure or overlap -> nested or overlapping shapes without completing hidden content; reflection or shadow -> shortened, lighter echo aligned to its source. Subject evidence: {feature_block}. Use one primary mark family and at most two supporting families; preserve source asymmetry and irregular spacing; allow measured displacement, compression, separation, overlap and scale change only when they clarify the source relationships; never rearrange arbitrarily. Keep the photograph's 2-3 most recognizable traits clearly readable: the main subject's silhouette, pose or distribution must stay identifiable at a glance. Express these traits as abstract masses, fields and rhythms rather than a literal outline of the objects.
The complete motif occupies about 55%-75% of the canvas width and 40%-55% of its height, leaving 30%-45% visually empty; it forms ONE coherent readable research group placed by negative space, never pinned to a corner, edge or center by rote; scale it as one group without altering internal relationships.
COLOR: extract 3-5 color roles from the photograph preserving their saturation and luminance hierarchy; slightly prefer assigning different sampled color roles to different meaningful marks; use {accent_name} ({accent_hex}) as the main accent role with 2-3 restrained accents at most; no invented neon, rainbow palettes, glossy gradients or global muting.
TEXT: generate NO text, NO numbers, NO letters, NO symbols anywhere.
Avoid: photograph-like panel, miniature scene redraw, recognizable object redraw, generic style transfer, vectorization, posterization, literal tracing, complete illustration, decorative filler, regularized spacing, invented symmetry, invented content, any text, color swatches, watermark, gradient background, uneven background, visible grain, dirty paper, yellow cast, gray veil, haze, flat object icons, literal diagrams."""


def _prompt_paper_collage(analysis, accent_name, accent_hex, position, verb,
                          feature_block, title, caption):
    if title and title.strip():
        main_line = 'the micro-text line: "%s"' % title.strip()
    else:
        main_line = "one short quiet micro-phrase"
    if caption and caption.strip():
        sub_line = 'a second smaller line: "%s"' % caption.strip()
    else:
        sub_line = "a tiny date or catalogue mark only when the scene or user text justifies it"

    return f"""Turn this photograph into a calm tactile paper collage zine poster, vertical 3:5. Keep the photographed scene truthful as the anchor while a larger abstract illustration field reinterprets selected source elements instead of tracing them. The photo provides facts; the illustration decides how to keep them. The input photograph is the ONLY content source.

FIRST INSPECT THE PHOTOGRAPH: identify the 1-2 core subjects that make the scene identifiable, their relative position, and the dominant gesture (the strongest horizon, path, gaze, diagonal or silhouette). Preserve these as the scene's identity; everything else may be simplified, merged or omitted.

LAYOUT: photographic anchor about 25%-50% of the poster; illustration field about 45%-70%; choose the split from the source's dominant gesture, horizon, path, gaze or silhouette; never default to a centered photo with text beneath it.

PHOTOGRAPHIC ANCHOR: preserve the scene's identity and one key spatial relationship; keep the photographic portion truthful; compress foliage, branches, leaves, crowds and micro-detail into a few large quiet forms; remove roughly 60%-80% of small descriptive detail; do not repaint the photo. If the scene is foliage-dense, merge trees and shrubs into ONE dominant canopy mass plus at most one subordinate counter-mass and one to three directional branch gestures; omit 85%-95% of individual leaves, needles and fine twigs; keep a source-specific lean, canopy opening, branch direction or light gap instead of botanical detail.

ILLUSTRATION FIELD: choose ONE primary grammar according to the source - silhouette-led (one broad dark or gray mass), contour-led (a few broken lines), field-led (one irregular ink or halftone atmosphere), rhythm-led (repeated marks compressing recurring elements), or cut-paper-led (one or two simplified organic or geometric cutouts) - plus at most one supporting grammar. Subject evidence: {feature_block}. Build an abstraction map: retain no more than 1-2 defining forms, merge repeated or adjacent elements into larger masses, omit clutter, transform forms into flat ink, broken contour or cut-paper shapes, expose blank paper. Keep about 55%-75% of the illustrated field quiet (65%-85% for intricate scenes); active ink about 15%-35% of the whole poster; one dominant mass large enough to affect the overall silhouette, plus one or two supporting marks and one restrained texture field; at most two neutral ink values besides paper and the single hue. Keep the illustration light and airy with generous negative space; avoid one heavy dark mass filling the whole field or a single traced silhouette; the photographic anchor stays about 25%-40% of the poster.

PAPER EDGE: the photographic anchor must flow into the illustration and paper through a visible hand-torn fibrous transition, never a clean cut: irregular hand-ripped contour with shallow notches, uneven rises, soft scallops and occasional longer fiber pulls; a feathered fringe of exposed paper fibers 3%-8% of the short edge wide, visible along 55%-90% of the photo perimeter; slight local abrasion, dry pigment loss and ink bleeding into the paper; illustration marks must continue across the boundary into the photo area and photo tones dissolve into the paper - the two halves must read as ONE collage, not two pasted layers; asymmetric tearing with one or two stronger pressure points; flat scan behavior, no lifted-paper depth, no uniform frame, no sticker border, no drop shadows.

COLOR STRUCTURE: exactly one added high-chroma hue {accent_name} ({accent_hex}) as compositional structure - balance, direction or visual weight - sharing a source-derived shape with the illustration. Give it a real area: opaque replacement or cut-paper form about 2%-6% of the poster, or a translucent/halftone underprint about 6%-15%, or a large structural color field about 10%-20% when the source is subdued. It must pass this test: removing the hue must weaken the composition. Natural colors inside the photograph do not count as added hues; introduce no second chromatic hue; typography and neutral marks use charcoal, warm gray, faded brown-black or a very restrained echo of the hue.

MICRO-TEXT: one restrained paper-integrated line in an existing quiet-paper area beneath, beside or inside a quiet pocket of the illustration: {main_line}; {sub_line}; small typewriter/letterpress or faint handwriting treatment with slightly uneven ink pressure, broken ink and soft edge wear; clearly subordinate to the photo and illustration; legible, correctly spelled, no serial numbers or stamps unless essential.

PAPER & MOOD: warm cream paper with visible fibers, dry ink, grain, slight print misregistration and scan dust; quiet, tactile, intentionally unfinished at normal size, clear at thumbnail. Avoid: clean digital clipping paths, crisp rectangular masks, uniform white outlines, sticker borders, decorative deckled frames, heavy drop shadows, curled corners, thick layered-paper depth, dense collage, lace-like filigree, glossy digital UI, neon, cartoon, commercial headline layout, busy muddy mixed-media look, a single heavy traced silhouette."""


def _prompt_distillation(analysis, accent_name, accent_hex, position, verb,
                         feature_block, title, caption):
    text_material = ""
    if title and title.strip():
        text_material += ' Available authorial text material: "%s".' % title.strip()
    if caption and caption.strip():
        text_material += ' Additional text material: "%s".' % caption.strip()

    return f"""Create an original minimalist zine illustration distilled from this photograph, NOT a reproduction of it. Treat the photo as a semantic and emotional reference only; do not preserve its composition.

EXPRESSION: First inspect the photograph and build an internal distillation card: semantic nucleus (the smallest subject or relationship that gives it meaning), core subject, dominant gesture, one spatial cue, material and weather, and emotional residue. Formulate one expressive proposition, choose ONE central tension that already exists or credibly emerges from the source (intimacy/distance, shelter/confinement, movement/stillness, smallness/vastness, warmth/coldness, memory/disappearance, order/growth, visibility/concealment, permanence/fragility), transform one source-derived object, spatial relationship or material behavior into ONE central visual metaphor, embody it through scale, direction, edge, color and material, and leave one relationship deliberately unanswered as an interpretive opening. Preserve 2-4 source anchors and make them instantly recognizable: the core subject's silhouette, pose or characteristic shape, its dominant gesture, one color relationship and one spatial cue must stay clearly readable in the final artwork - anyone who knows the photograph must identify it at a glance. Recomposition, simplification and exaggeration are allowed but must never erase the subject's identity or reduce it to an unreadable abstraction. Do not preserve the original composition by default. Subject evidence: {feature_block}. Remove any element whose only function is to look artistic.

CANVAS: 3:5 portrait or 5:3 landscape following the source orientation; warm paper with paper fibers, dry ink, grain and flat scan texture; use dry-print, risograph or letterpress ink with a flat matte finish, never pencil hatching, scribble shading or sketchy line texture; generous quiet space; no mockup, no frame, no photographic material. The core subject stays the protagonist: give it a clear, substantial presence and keep its shape, count and relationships legible; atmosphere, decoration and metaphor must serve it, never replace it.

COLOR MODE: Standard Accent Mode. Use one high-chroma accent {accent_name} ({accent_hex}) as an emotional event - warmth arriving, a signal calling, distance deepening, life persisting - in a form derived from the source. Choose the hue by its relationship to the source (source resonance, temperature counterpoint, focused complement or quiet harmony), never by a default palette. Every other printed form in neutral charcoal, graphite, warm gray or off-black ink; no other chromatic color anywhere. If the source contains a meaningful repeatable supporting element (flowers, leaves, fruit, birds, small lights, stones, windows), you may disperse a few instances of the SAME accent hue around the core subject with unequal scale, interval, orientation and density as one color system; keep the combined saturated area about 1%-3% of the poster.

TEXT: text is free authorial material used only where it deepens the proposition, tension or metaphor; it may be tiny, oversized, cropped, rotated, fragmented, stacked, or absent; it may be a caption, countervoice, title or the primary subject; no preset language, word count or placement.{text_material}

MANDATORY: Do not reproduce, embed, crop, collage, trace, or retain photographic pixels or photorealistic regions from the reference. The final image must contain original illustration, paper, and typography only.

Avoid: photographic regions, photorealistic rendering, style transfer, literal tracing, generic "quiet/dreamy/nostalgic" decoration, universal-symbol cliches, over-symbolization, invented surreal additions, dense clutter, multiple competing themes, neon palettes, glossy 3D depth, clean digital UI, pencil sketch look, scratchy hatching."""


def _friendly_error(code, body, reason):
    body = (body or "").strip().replace("\n", " ")
    low = body.lower()
    if "arrearage" in low or "insufficient balance" in low or "欠费" in body:
        return ("接口提示账号欠费或余额不足，请先到阿里云百炼控制台充值，"
                "并确认已开通 qwen-image-3.0 付费后重试。")
    if code == 403:
        if "AllocationQuota.FreeTierOnly" in body:
            return ("403：账号当前处于「仅免费额度」模式或免费额度已用完。"
                    "请到阿里云百炼控制台检查「仅免费额度」开关或开通付费后重试。")
        return "403：接口拒绝访问（配额或权限问题）。" + (body[:160] if body else "")
    if code == 401:
        return "401：API Key 无效或无权限，请检查密钥与模型开通状态。"
    if code == 429:
        return ("429：触发限流或调用额度不足，请稍后再试；"
                "若持续出现，请检查阿里云百炼账户余额与配额。")
    if code == 400 and "model not exist" in low:
        return "400：模型不存在，请检查 MODEL 配置（应为 qwen-image-3.0）。"
    return f"接口错误 {code}：{reason}。" + (body[:160] if body else "")


def _encode_input_image(path, max_side=1536):
    im = Image.open(path).convert("RGB")
    ph = im.copy()
    ph.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    ph.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _call_image_api(path, prompt, size, key, say=None):
    """调用 qwen-image-3.0 一次，返回原始图片字节（内置 429 重试与下载）。"""
    b64 = _encode_input_image(path)
    body = {
        "model": MODEL,
        "input": {"messages": [{"role": "user", "content": [
            {"image": "data:image/jpeg;base64," + b64},
            {"text": prompt}]}]},
        "parameters": {"size": size, "n": 1},
    }
    data = json.dumps(body).encode()
    if say:
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
                if say:
                    say(f"触发限流(429)，{wait} 秒后自动重试…")
                time.sleep(wait)
                continue
            raise RuntimeError(_friendly_error(e.code, err_body, e.reason))
        except urllib.error.URLError as e:
            raise RuntimeError(f"网络错误：{e.reason}")
    if result is None:
        raise RuntimeError("多次重试仍触发限流，请稍后再试；"
                             "若持续出现，请检查阿里云百炼账户余额与配额")

    try:
        img_url = result["output"]["choices"][0]["message"]["content"][0]["image"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("接口返回格式异常（未取到图片），请重试")
    if say:
        say("正在下载结果…")
    return urllib.request.urlopen(img_url, timeout=120).read()


# ── travel-abstract 双阶段（2B）辅助 ─────────────────────────────────────────
_IVORY = (239, 240, 232)


def _travel_phrase(title):
    """面板左下角短语：优先取用户标题（大写、截 1-3 词），否则用默认档案短语。"""
    if title and title.strip():
        words = title.strip().upper().split()
        return " ".join(words[:3])
    return "SILENT STUDY"


def _next_travel_no(out_dir):
    """档案序号：当前 outputs 已有海报数 + 1（个人用途的简单确定性规则）。"""
    n = 0
    try:
        for name in os.listdir(out_dir):
            if name.startswith("poster-") and name.lower().endswith(".png"):
                n += 1
    except OSError:
        pass
    return n + 1


def _pick_font(size):
    for p in (r"C:\Windows\Fonts\times.ttf",
              r"C:\Windows\Fonts\georgia.ttf",
              r"C:\Windows\Fonts\arial.ttf",
              r"C:\Windows\Fonts\segoeui.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _compose_travel(photo_path, panel_raw, out_path, title, say=None):
    """双阶段合成：原图在上（缩放保真），抽象面板在下，程序化叠加三行档案字。"""
    photo = Image.open(photo_path).convert("RGB")
    pw, ph = photo.size
    h_photo = min(int(ph * 900 / pw), 825)          # 照片最多占画布 55% 高
    photo = photo.resize((900, h_photo), Image.LANCZOS)

    rem = 1500 - h_photo
    panel = Image.open(io.BytesIO(panel_raw)).convert("RGB")
    pw2, ph2 = panel.size
    panel_w = min(900, max(1, int(pw2 * rem / ph2)))
    panel_h = max(1, int(ph2 * panel_w / pw2))
    panel = panel.resize((panel_w, panel_h), Image.LANCZOS)

    # 用面板背景的主色铺画布，避免固定象牙色与面板自带渐变撞出接缝
    corners = [panel.getpixel((0, 0)), panel.getpixel((panel_w - 1, 0)),
               panel.getpixel((0, panel_h - 1)), panel.getpixel((panel_w - 1, panel_h - 1))]
    panel_bg = tuple(int(sum(c[i] for c in corners) / 4) for i in range(3))

    canvas = Image.new("RGB", (900, 1500), panel_bg)
    canvas.paste(photo, (0, 0))
    px = (900 - panel_w) // 2
    py = h_photo
    canvas.paste(panel, (px, py))

    draw = ImageDraw.Draw(canvas)
    ink = (70, 66, 60)                              # 深灰档案墨
    no_text = "NO. %03d" % _next_travel_no(os.path.dirname(out_path))
    date_text = datetime.now().strftime("%d %b %Y").upper()
    phrase = _travel_phrase(title)
    f_no = _pick_font(22)
    f_small = _pick_font(16)
    draw.text((px + panel_w - 24, py + 20), no_text, font=f_no, fill=ink, anchor="ra")
    draw.text((px + 24, py + panel_h - 64), date_text, font=f_small, fill=ink, anchor="la")
    draw.text((px + 24, py + panel_h - 36), phrase, font=f_small, fill=ink, anchor="la")

    canvas.save(out_path, "PNG")
    if say:
        say(f"双阶段合成完成（照片 {h_photo}px + 面板 {panel_h}px，{no_text}）")


def generate(path, title=None, caption=None, features=None,
             accent_override=None, position_override=None, style="zine",
             size="900*1500", out_dir=DEFAULT_OUT_DIR, key=None,
             progress=None, out_name_base=None, out_name=None):
    def say(msg):
        if progress:
            progress(msg)

    key = key or load_api_key()
    if not key:
        raise RuntimeError("未找到 API Key：请在工具里粘贴保存，或配置 POSTER_API_KEY 环境变量")

    analysis = analyze_image(path)
    style = style or "zine"
    if style == "zine":
        say(f"分析完成：强调色 {analysis['accent_name']} / 建议位置 {analysis['position_label']}")
    else:
        say(f"分析完成：强调色 {analysis['accent_name']} / 构图由「{STYLES[style]}」风格决定")

    os.makedirs(out_dir, exist_ok=True)
    if out_name:
        out_path = os.path.join(out_dir, out_name)
    else:
        base = out_name_base or os.path.splitext(os.path.basename(path))[0]
        acc = accent_override[0].split()[0] if accent_override else analysis["accent_name"].split()[0]
        pos = position_override or analysis["position"]
        out_path = os.path.join(out_dir, f"poster-{base}-{acc}-{pos}.png")

    if style == "travel-abstract":
        # 双阶段（2B）：qwen 只出无字抽象面板，PIL 合成原图 + 面板 + 档案字
        panel_prompt = build_prompt(analysis, title, caption, features, accent_override,
                                    position_override, style="travel-panel")
        say("travel-abstract：双阶段生成（先出无字抽象面板…）")
        panel_raw = _call_image_api(path, panel_prompt, "900*1500", key, say)
        _compose_travel(path, panel_raw, out_path, title, say)
        check = Image.open(out_path)
        info = {"format": check.format, "mode": check.mode, "size": check.size}
        say(f"已保存并校验：{os.path.basename(out_path)}")
        return out_path, analysis, info, panel_prompt

    prompt = build_prompt(analysis, title, caption, features, accent_override,
                          position_override, style=style)
    if style == "distillation" and analysis["size"][0] > analysis["size"][1]:
        eff_size = "1500*900"                       # 横图源跟随方向（5:3）
    else:
        eff_size = size
    raw = _call_image_api(path, prompt, eff_size, key, say)

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
    ap.add_argument("--style", default="zine", choices=list(STYLES),
                    help="风格：" + " / ".join("%s=%s" % (k, v) for k, v in STYLES.items()))
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
                          accent_override, args.position, style=args.style)
    if args.dry_run:
        print("\n----- PROMPT -----\n" + prompt)
        return 0

    key = load_api_key()
    if not key:
        raise SystemExit("未找到 API Key")
    out, _, info, _ = generate(args.input, args.title, args.caption, features,
                              accent_override, args.position, args.style,
                              args.size, args.out_dir, key, progress=print)
    print(f"已保存: {out}  {info['format']}/{info['mode']} {info['size'][0]}x{info['size'][1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
