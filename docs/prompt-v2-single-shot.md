# 单次 I2I 提示词改版草案（v2）

> 目标：把四个"Agent 技能"（原本是给 Codex/Claude 的多步工作流）改写成 qwen-image-3.0
> 一次调用就能执行好的提示词。全部保留现有 `build_prompt` 的占位符约定，可直接替换
> `poster_core.py` 中对应函数体，函数签名不变。

## 0. 通用诊断（先说清共性问题）

四个 Skill 的原文我逐字读过了（`tmp/repos/` 下三个 git 克隆 + `~/.dsh/skills/gc-minimal-zine-poster-v0-1`），
它们共同的结构是：**读图 → 建"场景卡/蒸馏卡" → 做创作决策 → 出图 → 检查/修正**。
你的 web 端只保留了最后两环，丢掉了最值钱的前三步。qwen-image-3.0 能看图，所以改版思路是：
把"先分析再生成"写成提示词里的指令（`First inspect the photograph and identify…`），
让图像模型自己在单次调用内完成小规模推理。

三个跨风格的修复点：

1. **文字是最大的败笔来源。** 技能们对文字极其克制（travel 甚至用程序确定性加字、出图阶段明确
   "generate no text"），而图像模型渲染文字经常乱码/拼错/多出内容。改版原则：**能不写进图里的文字
   就不写**；travel-abstract 建议改"出图无文字 + PIL 叠加"（即原技能的设计，改动量很小，见 §6.2）。
2. **`features` 参数几乎永远是空的。** web 端 `features` 是可选字段，不填时落回
   `GENERIC_FEATURES`（现在是一句空话："keep the main subject recognizable"）。
   建议升级默认文案（§6.1），让模型自己看图识别主体——这是把"读图建卡"塞回单次提示词的关键一步。
3. **`position` 只对 zine 有意义。** 其余四风格已由 `patch_styles.py` 去掉了位置注入，保留即可。

---

## 1. photo-panel（源：ZzzLc0405/photo-abstract-editorial）

现状 `_prompt_photo_panel` 已经是技能的好压缩版（双区结构、DECONSTRUCT 方法、标记系统、
象牙面板、标题位置都有了）。缺的是技能里最有用的三条防呆规则：

- **人群画法**（技能 §3）：人 = 一条连续不规则竖向笔触，禁止画头/四肢/脸/衣物——否则下方面板会出现"小人剪影插图"。
- **地标建筑**（技能 §3）：只保留 1-3 个身份线索（外轮廓/檐线/塔身/拱/尖顶/层叠节奏），禁止窗户、砖墙、装饰细节。
- **标题反词库**（技能 §8）：避免 Memory / Dream / Moment 等空词、旅游文案腔。

### 替换后的函数体

```python
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

ABSTRACT PANEL: below the photo, one perfectly uniform light ivory panel near #F3F0E8 with no gradient, texture, shadow, vignette, grain or seam. Method: DECONSTRUCT - SELECTIVE PRESERVATION - ABSTRACT - RECONSTRUCT. First inspect the photograph internally and identify 3-6 decisive spatial facts (dominant masses, axes, counts, intervals, overlaps, depth, color roles, asymmetry, meaningful voids); discard surface texture and low-information detail; then rebuild only the retained relationships as one sparse abstract motif — never a miniature scene, a traced outline, a posterized photo, or a generic icon. Use one primary mark family (flat or softly organic color blocks, soft round masses, arcs or tapered strokes, short bars or stacked bands, simplified architectural masses) plus at most two supporting families (thin lines, small isolated dots, restrained figure ink-marks). Every mark must trace back to a real fact in the photograph; no invented decoration, no regular spacing, no symmetrical diagram. Subject evidence: {feature_block}.
PEOPLE AND LANDMARKS: if the photograph contains people, render each person in the panel as one continuous irregular short vertical ink mark or gently tapered block — never separate heads, limbs, faces, or clothing. If it contains a landmark building or distinctive structure, keep at most one to three identity cues (a distinctive outer contour, an eave line, a tapering tower mass, an arch, a spire, a layered rhythm) and omit windows, masonry, brackets and surface detail.
The motif occupies about 30%-42% of the panel width and at most 28%-34% of its height, leaving 65%-80% clean empty space, positioned by the panel's own balance and the photograph's dominant gesture, with generous poetic negative space.

COLOR SYSTEM: extract colors only from the photograph, lowering saturation and reducing their number: one main color role ({accent_name} {accent_hex} as the primary accent), one dark structural role, one light neutral role, and at most one or two small accents; no neon, no invented complementary colors, no rainbow palette.

TITLE: inside the ivory panel below or beside the motif, {main_line}; {sub_line}. The title must be faithful to a real visual fact of the photograph, concise and poetic — never empty words like Memory, Dream or Moment, never a travel-promotion line. Render it in a restrained elegant editorial serif, in a darker color derived from the photograph (deep blue-grey, dark green, wine red, deep violet or charcoal, never pure black, never the brightest accent), bottom-left aligned or bottom-centered, clearly readable. No other text, numbers, dates, labels or watermarks anywhere.

Avoid: repainting the photograph, scene reconstruction, generative outpainting, filter look, posterized photo, vectorized tracing, complete illustration, dense decoration, invented symmetry, extra words, numbers, dates, color swatches, legend, watermark, gradients, uneven background, paper texture, grain, haze, drop shadows, mockup frames, 3D depth, cartoon style."""
```

---

## 2. travel-abstract（源：Evianis/travel-photo-abstraction）

这是失真最严重的一个。原技能是**两阶段流水线**（原文白纸黑字）：

> 先生成"干净的抽象面板"临时图 → `finalize_artwork.py` **程序化拼合**原图 + 面板，
> 再确定性叠加 NO. 00X / 日期 / 短语，逐像素校验后输出。

它明确要求：出图阶段 **generate no text**，文字全部由程序加。你现在让 qwen 一次画完
"上面照片 + 下面板 + 三行字"，等于同时违背了原设计的两条核心约束（照片保真、文字确定性）。

给两个版本：

### 2A. 单次直出版（维持现有架构，改动最小）

关键改动只有两处：**删掉文字要求（改为"禁止任何文字"）**；把"上面照片保持原样"的约束写得更强。

```python
def _prompt_travel_abstract(analysis, accent_name, accent_hex, position, verb,
                            feature_block, title, caption):
    return f"""Visual distillation study: the untouched full-frame photograph above, and one sparse abstract reconstruction panel below it on clean ivory paper. The lower panel is a new abstract composition derived ONLY from relationships in the upper photograph; it is never a miniature redraw, a style-converted copy, or a filtered photograph.

UPPER PHOTOGRAPH: preserve the photograph's content, exposure, color and detail as exactly as possible in the upper zone; do not restyle, repaint, filter, recrop or regenerate it; only scale it to fit. Do not let the lower panel bleed into it.

LOWER PANEL: one perfectly uniform flat field at or near #F3F0E8; no gradient, light falloff, glow, shadow, edge darkening, band, seam, grain, noise, paper texture, fibers, haze, vignette, stains or color cast. DECONSTRUCT the photo into dominant masses, axes, boundaries, counts, directions, overlaps, intervals, depth, color roles, asymmetry and meaningful voids; remove photographic surface, literal object outlines, perspective detail and minor objects; RECONSTRUCT only the retained relationships as minimal marks using this mapping: mass or field -> one clean flat block or quiet plane; compact object -> dot, circle, pill, short line or tiny silhouette; horizon or boundary -> one thin line; direction or motion -> taper, streak, aligned bars or directional sequence; repeated objects -> repeated modules preserving source spacing and scale hierarchy; radial structure -> partial arc plus selected spokes and nodes; enclosure or overlap -> nested or overlapping shapes without completing hidden content; reflection or shadow -> shortened, lighter echo aligned to its source. Subject evidence: {feature_block}. Use one primary mark family and at most two supporting families; preserve source asymmetry and irregular spacing; allow measured displacement, compression, separation, overlap and scale change only when they clarify the source relationships; never rearrange arbitrarily. Keep the photograph's 2-3 most recognizable traits clearly readable: the main subject's silhouette, pose or distribution must stay identifiable at a glance — a building, person, tree, mountain, vehicle or object must remain legible in the marks, not dissolve into unreadable dots.
The complete motif occupies about 55%-75% of the panel width and 40%-55% of its height, leaving 30%-45% visually empty; it forms ONE coherent readable research group whose placement follows the panel's negative space and the photograph's dominant gesture — never pinned to a corner, edge or center by rote; scale it as one group without altering internal relationships.

COLOR: extract 3-5 color roles from the photograph preserving their saturation and luminance hierarchy; slightly prefer assigning different sampled color roles to different meaningful marks; use {accent_name} ({accent_hex}) as the main accent role with 2-3 restrained accents at most; no invented neon, rainbow palettes, glossy gradients or global muting.

TEXT: generate NO text, NO numbers, NO letters, NO symbols of any kind anywhere in the image. The application will overlay the archive details afterwards.

Avoid: photograph-like lower panel, miniature scene redraw, recognizable object redraw, generic style transfer, vectorization, posterization, literal tracing, complete illustration, decorative filler, regularized spacing, invented symmetry, invented content, any text, color swatches, watermark, gradient background, uneven background, visible grain, dirty paper, yellow cast, gray veil, haze."""
```

> 注：55%-75% / 40%-55% 的放大比例是你之前有意偏离技能（DEV_NOTES 第 8 节），保留不动。
> 2A 的代价：上面照片仍会被 qwen 轻微重绘（I2I 无法逐像素保真）——这是单次直出的物理上限。

### 2B. 双阶段版（推荐，接近原技能效果）

架构：qwen 只出**无字下面板**，`poster_server.py` 用 PIL 把原图贴上部、面板贴下部、再画三行字。
原图逐像素保真、文字永远不错乱，正是原技能的设计。代码草图见 §6.2；面板提示词：

```python
def _prompt_travel_panel(analysis, accent_name, accent_hex, position, verb,
                         feature_block, title, caption):
    return f"""Create a sparse abstract reconstruction panel, vertical, on one perfectly uniform flat field at or near #F3F0E8 — no gradient, texture, grain, seam, glow or shadow anywhere. DECONSTRUCT the input photograph into dominant masses, axes, boundaries, counts, directions, overlaps, intervals, depth, color roles, asymmetry and meaningful voids; remove photographic surface, literal outlines, perspective detail and minor objects; RECONSTRUCT only the retained relationships as minimal marks using this mapping: mass or field -> one clean flat block or quiet plane; compact object -> dot, circle, pill, short line or tiny silhouette; horizon or boundary -> one thin line; direction or motion -> taper, streak, aligned bars or directional sequence; repeated objects -> repeated modules preserving source spacing and scale hierarchy; radial structure -> partial arc plus selected spokes and nodes; enclosure or overlap -> nested or overlapping shapes without completing hidden content; reflection or shadow -> shortened, lighter echo aligned to its source. Subject evidence: {feature_block}. Use one primary mark family and at most two supporting families; preserve source asymmetry and irregular spacing; allow measured displacement, compression, separation, overlap and scale change only when they clarify the source relationships; never rearrange arbitrarily. Keep the photograph's 2-3 most recognizable traits clearly readable: the main subject's silhouette, pose or distribution must stay identifiable at a glance.
The complete motif occupies about 55%-75% of the canvas width and 40%-55% of its height, leaving 30%-45% visually empty; it forms ONE coherent readable research group placed by negative space, never pinned to a corner, edge or center by rote; scale it as one group without altering internal relationships.
COLOR: extract 3-5 color roles from the photograph preserving their saturation and luminance hierarchy; slightly prefer assigning different sampled color roles to different meaningful marks; use {accent_name} ({accent_hex}) as the main accent role with 2-3 restrained accents at most; no invented neon, rainbow palettes, glossy gradients or global muting.
TEXT: generate NO text, NO numbers, NO letters, NO symbols anywhere.
Avoid: photograph-like panel, miniature scene redraw, recognizable object redraw, generic style transfer, vectorization, posterization, literal tracing, complete illustration, decorative filler, regularized spacing, invented symmetry, invented content, any text, color swatches, watermark, gradient background, uneven background, visible grain, dirty paper, yellow cast, gray veil, haze."""
```

---

## 3. paper-collage（源：Zeejay0/gathered-scenes-zine-skill · 实景拼贴）

现状 `_prompt_paper_collage` 已覆盖技能大部分（布局/锚点/插画场/撕纸边/单色结构/微文字/纸面情绪）。
四个缺口：

- **没有"先读图定身份"一步**：加一句"先识别 1-2 个核心主体 + 主导手势"，作为场景保真的锚。
- **密集植被压缩太笼统**：技能对 foliage 场景有精确规则（删 85-95% 细枝末节、合并成一个主冠层 +
  1-3 个枝向手势、最多一个从属反块）——当前只有一句 60-80%。
- **色彩结构没有面积**：技能给了面积档（不透明 2-6% / 半透明 6-15% / 大色场 10-20%），
  并明确要求"去掉这抹色构图就垮"的测试。当前提示词没说面积。
- **微文字默认值偏离技能**：技能默认英文、≤5 词 / ≤8 汉字、**默认不用日期/地点**（除非用户提供）；
  当前默认"微短语 + 日期或档案标记"。建议日期只在用户填了 caption 时才出现。

### 替换后的函数体

```python
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

ILLUSTRATION FIELD: choose ONE primary grammar according to the source - silhouette-led (one broad dark or gray mass), contour-led (a few broken lines), field-led (one irregular ink or halftone atmosphere), rhythm-led (repeated marks compressing recurring elements), or cut-paper-led (one or two simplified organic or geometric cutouts) - plus at most one supporting grammar. Subject evidence: {feature_block}. Build an abstraction map: retain no more than 1-2 defining forms, merge repeated or adjacent elements into larger masses, omit clutter, transform forms into flat ink, broken contour or cut-paper shapes, expose blank paper. Keep about 55%-75% of the illustrated field quiet (65%-85% for intricate scenes); active ink about 15%-35% of the whole poster; one dominant mass large enough to affect the overall silhouette, plus one or two supporting marks and one restrained texture field; at most two neutral ink values besides paper and the single hue.

PAPER EDGE: the photographic anchor must flow into the illustration and paper through a visible hand-torn fibrous transition, never a clean cut: irregular hand-ripped contour with shallow notches, uneven rises, soft scallops and occasional longer fiber pulls; a feathered fringe of exposed paper fibers 3%-8% of the short edge wide, visible along 55%-90% of the photo perimeter; slight local abrasion, dry pigment loss and ink bleeding into the paper; illustration marks must continue across the boundary into the photo area and photo tones dissolve into the paper - the two halves must read as ONE collage, not two pasted layers; asymmetric tearing with one or two stronger pressure points; flat scan behavior, no lifted-paper depth, no uniform frame, no sticker border, no drop shadows.

COLOR STRUCTURE: exactly one added high-chroma hue {accent_name} ({accent_hex}) as compositional structure - balance, direction or visual weight - sharing a source-derived shape with the illustration. Give it a real area: opaque replacement or cut-paper form about 2%-6% of the poster, or a translucent/halftone underprint about 6%-15%, or a large structural color field about 10%-20% when the source is subdued. It must pass this test: removing the hue must weaken the composition. Natural colors inside the photograph do not count as added hues; introduce no second chromatic hue; typography and neutral marks use charcoal, warm gray, faded brown-black or a very restrained echo of the hue.

MICRO-TEXT: one restrained paper-integrated line in an existing quiet-paper area beneath, beside or inside a quiet pocket of the illustration: {main_line}; {sub_line}; small typewriter/letterpress or faint handwriting treatment with slightly uneven ink pressure, broken ink and soft edge wear; clearly subordinate to the photo and illustration; legible, correctly spelled, no serial numbers or stamps unless essential.

PAPER & MOOD: warm cream paper with visible fibers, dry ink, grain, slight print misregistration and scan dust; quiet, tactile, intentionally unfinished at normal size, clear at thumbnail. Avoid: clean digital clipping paths, crisp rectangular masks, uniform white outlines, sticker borders, decorative deckled frames, heavy drop shadows, curled corners, thick layered-paper depth, dense collage, lace-like filigree, glossy digital UI, neon, cartoon, commercial headline layout."""
```

---

## 4. distillation（源：Zeejay0/gathered-scenes-zine-skill · 影像蒸馏）

现状 `_prompt_distillation` 是四者中最忠实技能的（表达式引擎/张力/隐喻/标准强调色/禁照片材质全都在）。
三个小改进：

- **文字自由度**：技能说文字"可缺席、可巨大、可裁切、可旋转、可堆叠"，当前只在用户给 title 时
  出现——给一段显式的自由度声明。
- **选色不默认**：技能明确"不要默认蓝/固定调色板，按源关系选色"——补一句。
- **分布式点缀色**（技能 §Distributed Supporting Accent）：源里有可重复元素（花/叶/灯/窗）时，
  允许同一高彩度色系的几个不等距点缀围绕主体。

### 替换后的函数体

```python
def _prompt_distillation(analysis, accent_name, accent_hex, position, verb,
                         feature_block, title, caption):
    text_material = ""
    if title and title.strip():
        text_material += ' Available authorial text material: "%s".' % title.strip()
    if caption and caption.strip():
        text_material += ' Additional text material: "%s".' % caption.strip()

    return f"""Create an original minimalist zine illustration distilled from this photograph, NOT a reproduction of it. Treat the photo as a semantic and emotional reference only; do not preserve its composition.

EXPRESSION: First inspect the photograph and build an internal distillation card: semantic nucleus (the smallest subject or relationship that gives it meaning), core subject, dominant gesture, one spatial cue, material and weather, and emotional residue. Formulate one expressive proposition, choose ONE central tension that already exists or credibly emerges from the source (intimacy/distance, shelter/confinement, movement/stillness, smallness/vastness, warmth/coldness, memory/disappearance, order/growth, visibility/concealment, permanence/fragility), transform one source-derived object, spatial relationship or material behavior into ONE central visual metaphor, embody it through scale, direction, edge, color and material, and leave one relationship deliberately unanswered as an interpretive opening. Preserve 2-4 source anchors and make them instantly recognizable: the core subject's silhouette, pose or characteristic shape, its dominant gesture, one color relationship and one spatial cue must stay clearly readable in the final artwork - anyone who knows the photograph must identify it at a glance. Recomposition, simplification and exaggeration are allowed but must never erase the subject's identity or reduce it to an unreadable abstraction. Do not preserve the original composition by default. Subject evidence: {feature_block}. Remove any element whose only function is to look artistic.

CANVAS: 3:5 portrait or 5:3 landscape following the source orientation; warm paper with paper fibers, dry ink, grain and flat scan texture; generous quiet space; no mockup, no frame, no photographic material. The core subject stays the protagonist: give it a clear, substantial presence and keep its shape, count and relationships legible; atmosphere, decoration and metaphor must serve it, never replace it.

COLOR MODE: Standard Accent Mode. Use one high-chroma accent {accent_name} ({accent_hex}) as an emotional event - warmth arriving, a signal calling, distance deepening, life persisting - in a form derived from the source. Choose the hue by its relationship to the source (source resonance, temperature counterpoint, focused complement or quiet harmony), never by a default palette. Every other printed form in neutral charcoal, graphite, warm gray or off-black ink; no other chromatic color anywhere. If the source contains a meaningful repeatable supporting element (flowers, leaves, fruit, birds, small lights, stones, windows), you may disperse a few instances of the SAME accent hue around the core subject with unequal scale, interval, orientation and density as one color system; keep the combined saturated area about 1%-3% of the poster.

TEXT: text is free authorial material used only where it deepens the proposition, tension or metaphor; it may be tiny, oversized, cropped, rotated, fragmented, stacked, or absent; it may be a caption, countervoice, title or the primary subject; no preset language, word count or placement.{text_material}

MANDATORY: Do not reproduce, embed, crop, collage, trace, or retain photographic pixels or photorealistic regions from the reference. The final image must contain original illustration, paper, and typography only.

Avoid: photographic regions, photorealistic rendering, style transfer, literal tracing, generic "quiet/dreamy/nostalgic" decoration, universal-symbol cliches, over-symbolization, invented surreal additions, dense clutter, multiple competing themes, neon palettes, glossy 3D depth, clean digital UI."""
```

---

## 5. zine（源：LiamGvchi/gc-minimal-zine-poster）——微调即可

当前 `_prompt_zine` 已经不错，不动结构。两个可选项：

1. 依赖 §6.1 的 `GENERIC_FEATURES` 升级——它让所有风格（含 zine）先看图再构图。
2. 可选：在开头补一句 `First inspect the photograph and identify the main subject, then compose the cutout anchor around it.` 帮助 qwen 锁定锚点主体。

---

## 6. 集成注意事项（代码配合）

### 6.1 升级 GENERIC_FEATURES 默认值（一行，收益最大）

`poster_core.py` 第 51 行，把空话换成"让模型自己读图建卡"的指令：

```python
GENERIC_FEATURES = (
    "First inspect the photograph: identify its 1-2 core subjects, their relative "
    "position, the dominant gesture (the strongest line, path, gaze or silhouette), "
    "and one key spatial relationship. Keep these recognizable in the result."
)
```

### 6.2 travel-abstract 双阶段方案（2B）——已实现

> 状态：已落地到 `poster_core.py`。`generate()` 内 `style == "travel-abstract"` 自动走双阶段：
> `_call_image_api` 出无字面板（900×1500）→ `_compose_travel` 用 PIL 合成（照片最多占 55% 高、
> 面板铺满余下区域、背景 #F3F0E8 无缝）→ 程序化叠加 `NO. 00X`（面板右上）与日期 + 短语
> （面板左下两行）。序号规则：outputs 现有海报数 + 1；短语：用户标题大写截 1-3 词，否则
> 默认 `SILENT STUDY`。CLI 与 web 共用同一入口。

```python
# poster_core.py 新增（或独立函数）：
def generate_panel(path, title=None, caption=None, features=None,
                   accent_override=None, style="travel-panel", size=(900, 900)):
    analysis = analyze_image(path)
    prompt = build_prompt(analysis, title, caption, features, accent_override,
                          style="travel-panel")   # style="travel-panel" -> _prompt_travel_panel
    # 调用百炼 qwen-image-3.0，size 传面板尺寸（如 900x900）
    # 返回面板文件路径
```

```python
# poster_server.py 的生成任务里，style=="travel-abstract" 时分叉：
# 1) panel = generate_panel(...)                     # 无字下面板
# 2) 用 PIL 合成：
#    canvas = 900x1500 白底
#    photo 按宽 900 缩放（保持比例）贴上部
#    面板缩放到与 photo 同宽，贴下部（面板背景 #F3F0E8，接缝处正好同色）
# 3) PIL 画三行字：NO. 00X（面板右上）、DD MON YYYY + 短语（另一空角）
#    （字体：Windows 自带 simsun/segoe 或打包一个开源衬线字体；字号小、深灰）
# 4) 保存到 outputs/
```

收益：上区照片逐像素保真（这正是原技能"绝不重绘原图"的硬约束），文字永远不乱码。

### 6.3 distillation 方向自适应

### 6.3 distillation 方向自适应——已实现

> 状态：`generate()` 内 `style == "distillation"` 且源图宽>高时，size 自动切换为 `1500*900`（5:3），
> 其余保持 `900*1500`。

`config.json` 的固定 `900*1500`（3:5）会把横图源压扁，而技能要求跟随源方向（5:3 横版）。
改法：`generate()` 里按 `analysis["size"]`（宽>高 → `1500*900`，否则 `900*1500`）覆盖传给百炼的 size。
（photo-panel / travel-abstract / paper-collage 保持 3:5 竖版不变。）

---

## 7. 改动前后对照速查

| 风格 | 现状问题 | 改版要点 |
|---|---|---|
| photo-panel | 缺人群/地标防呆、标题反词库 | +PEOPLE AND LANDMARKS 段、标题空词禁令 |
| travel-abstract | 文字交给图像模型渲染（必乱码）、照片被重绘 | 2A：删文字要求；2B：面板无字 + PIL 合成（推荐） |
| paper-collage | 无先读图、植被压缩笼统、色彩无面积、日期默认出现 | +FIRST INSPECT、foliage 精确规则、色面积档、日期改为条件性 |
| distillation | 已较忠实，缺文字自由度/选色规则/分布式点缀 | +文字自由度声明、选色不默认、分布式点缀 |
| zine | 基本没问题 | 依赖 GENERIC_FEATURES 升级（可选加一句引导） |
