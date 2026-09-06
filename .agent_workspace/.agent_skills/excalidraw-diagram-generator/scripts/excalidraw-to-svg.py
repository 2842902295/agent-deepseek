#!/usr/bin/env python3
"""
Convert .excalidraw JSON files to SVG format.

Usage:
    python excalidraw-to-svg.py <input.excalidraw> [output.svg] [OPTIONS]

Options:
    --padding PX          Padding around the diagram (default: 40)
    --bg COLOR            Override background color (default: from appState or #ffffff)
    --no-bg               Transparent background
    --font-family NAME    Fallback font family for SVG text (default: "Segoe UI, Helvetica, Arial, sans-serif")
    --scale FACTOR        Scale factor for the output (default: 1.0)
    --pretty              Pretty-print the SVG XML

Examples:
    python excalidraw-to-svg.py diagram.excalidraw
    python excalidraw-to-svg.py diagram.excalidraw output.svg
    python excalidraw-to-svg.py diagram.excalidraw --padding 60 --bg "#f8f9fa"
    python excalidraw-to-svg.py diagram.excalidraw --scale 2.0 --pretty
"""

import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── Font mapping ──────────────────────────────────────────────────────────────
# 给每条字体链尾巴挂上 CJK 兜底：按 Win → macOS → 通用开源 CJK → Docker 里 apt 装的
# 微米黑/正黑/Droid 排序，先命中系统已有字体，命中不到时落到 Linux 容器里那几个。
CJK_FALLBACK = (
    "'Microsoft YaHei', 'PingFang SC', 'Hiragino Sans GB', "
    "'Source Han Sans SC', 'Noto Sans CJK SC', "
    "'WenQuanYi Micro Hei', 'WenQuanYi Zen Hei', 'Droid Sans Fallback'"
)

EXCALIDRAW_FONT_MAP = {
    1: f"'Segoe Print', 'Bradley Hand', 'Comic Sans MS', {CJK_FALLBACK}, cursive",       # Virgil
    2: f"'Helvetica Neue', Helvetica, Arial, {CJK_FALLBACK}, sans-serif",                # Helvetica
    3: f"'Cascadia Code', 'Fira Code', 'Consolas', {CJK_FALLBACK}, monospace",           # Cascadia
    4: f"'Comic Neue', 'Comic Sans MS', {CJK_FALLBACK}, cursive",                        # Comic Shanns
    5: f"'Excalifont', 'Segoe Print', 'Bradley Hand', {CJK_FALLBACK}, cursive",          # Excalifont
    6: f"'Nunito', 'Segoe UI', {CJK_FALLBACK}, sans-serif",                              # Nunito
    7: f"'Lora', 'Georgia', {CJK_FALLBACK}, serif",                                      # Lora
    8: f"'Code New Roman', 'Courier New', {CJK_FALLBACK}, monospace",                    # Code New Roman
}

DEFAULT_FONT_FALLBACK = f"'Segoe UI', Helvetica, Arial, {CJK_FALLBACK}, sans-serif"


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_font_family(element: dict, fallback: str = DEFAULT_FONT_FALLBACK) -> str:
    """Resolve Excalidraw fontFamily ID to CSS font-family string."""
    fid = element.get("fontFamily", 1)
    if fid == 5:
        # Excalifont is a custom web font; fall back to system hand-drawn font
        return fallback if fallback != DEFAULT_FONT_FALLBACK else EXCALIDRAW_FONT_MAP[1]
    return EXCALIDRAW_FONT_MAP.get(fid, fallback)


def stroke_dasharray(style: str, width: float) -> Optional[str]:
    """Convert Excalidraw strokeStyle to SVG stroke-dasharray."""
    if style == "dashed":
        return f"{width * 6},{width * 4}"
    elif style == "dotted":
        return f"{width * 2},{width * 3}"
    return None


def resolve_fill(bg_color: str, fill_style: str) -> str:
    """Return SVG fill value."""
    if bg_color == "transparent" or not bg_color:
        return "none"
    return bg_color


def opacity_val(raw: Any) -> float:
    """Normalise opacity from 0-100 to 0-1."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if v > 1:
        v /= 100.0
    return max(0.0, min(1.0, v))


def escape_xml(text: str) -> str:
    """Escape text for XML content."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


# ── Bounding box ──────────────────────────────────────────────────────────────

def compute_bbox(elements: List[dict]) -> Tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) covering all visible elements."""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    for el in elements:
        if el.get("isDeleted", False):
            continue
        etype = el.get("type", "")
        ex, ey = el.get("x", 0), el.get("y", 0)
        ew, eh = el.get("width", 0), el.get("height", 0)

        if etype in ("arrow", "line") and "points" in el:
            pts = el["points"]
            for px, py in pts:
                ax, ay = ex + px, ey + py
                min_x = min(min_x, ax)
                min_y = min(min_y, ay)
                max_x = max(max_x, ax)
                max_y = max(max_y, ay)
            # account for arrowhead size
            max_x += 20
            max_y += 20
        else:
            min_x = min(min_x, ex)
            min_y = min(min_y, ey)
            max_x = max(max_x, ex + ew)
            max_y = max(max_y, ey + eh)

    if min_x == float("inf"):
        return 0, 0, 100, 100
    return min_x, min_y, max_x, max_y


# ── Element renderers ─────────────────────────────────────────────────────────

def render_rectangle(el: dict, font_fallback: str) -> List[str]:
    """Render a rectangle (optionally rounded) with optional embedded text."""
    parts = []
    x, y = el["x"], el["y"]
    w, h = el["width"], el["height"]
    sw = el.get("strokeWidth", 2)
    sc = el.get("strokeColor", "#1e1e1e")
    bg = el.get("backgroundColor", "transparent")
    fs = el.get("fillStyle", "solid")
    op = opacity_val(el.get("opacity", 100))
    ss = el.get("strokeStyle", "solid")
    rnd = el.get("roundness")
    rx = min(w, h) * 0.1 if rnd and rnd.get("type") == 3 else 0

    fill = resolve_fill(bg, fs)
    da = stroke_dasharray(ss, sw)

    attrs = (
        f'x="{x}" y="{y}" width="{w}" height="{h}" '
        f'rx="{rx}" ry="{rx}" '
        f'fill="{fill}" stroke="{sc}" stroke-width="{sw}"'
    )
    if da:
        attrs += f' stroke-dasharray="{da}"'
    if op < 1:
        attrs += f' opacity="{op}"'

    parts.append(f"<rect {attrs}/>")

    # Embedded text
    text = el.get("text", "")
    if text:
        parts.extend(_render_bound_text(el, font_fallback))

    return parts


def render_ellipse(el: dict, font_fallback: str) -> List[str]:
    """Render an ellipse with optional embedded text."""
    parts = []
    x, y = el["x"], el["y"]
    w, h = el["width"], el["height"]
    cx, cy = x + w / 2, y + h / 2
    rx, ry = w / 2, h / 2
    sw = el.get("strokeWidth", 2)
    sc = el.get("strokeColor", "#1e1e1e")
    bg = el.get("backgroundColor", "transparent")
    fs = el.get("fillStyle", "solid")
    op = opacity_val(el.get("opacity", 100))
    ss = el.get("strokeStyle", "solid")

    fill = resolve_fill(bg, fs)
    da = stroke_dasharray(ss, sw)

    attrs = (
        f'cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" '
        f'fill="{fill}" stroke="{sc}" stroke-width="{sw}"'
    )
    if da:
        attrs += f' stroke-dasharray="{da}"'
    if op < 1:
        attrs += f' opacity="{op}"'

    parts.append(f"<ellipse {attrs}/>")

    text = el.get("text", "")
    if text:
        parts.extend(_render_bound_text(el, font_fallback))

    return parts


def render_diamond(el: dict, font_fallback: str) -> List[str]:
    """Render a diamond (rotated square) with optional embedded text."""
    parts = []
    x, y = el["x"], el["y"]
    w, h = el["width"], el["height"]
    sw = el.get("strokeWidth", 2)
    sc = el.get("strokeColor", "#1e1e1e")
    bg = el.get("backgroundColor", "transparent")
    fs = el.get("fillStyle", "solid")
    op = opacity_val(el.get("opacity", 100))
    ss = el.get("strokeStyle", "solid")

    fill = resolve_fill(bg, fs)
    da = stroke_dasharray(ss, sw)

    # Diamond points: top, right, bottom, left
    mx, my = x + w / 2, y + h / 2
    pts = f"{mx},{y} {x + w},{my} {mx},{y + h} {x},{my}"

    attrs = f'points="{pts}" fill="{fill}" stroke="{sc}" stroke-width="{sw}"'
    if da:
        attrs += f' stroke-dasharray="{da}"'
    if op < 1:
        attrs += f' opacity="{op}"'

    parts.append(f"<polygon {attrs}/>")

    text = el.get("text", "")
    if text:
        parts.extend(_render_bound_text(el, font_fallback))

    return parts


def render_arrow(el: dict, marker_id: str) -> List[str]:
    """Render an arrow with arrowhead marker."""
    parts = []
    x, y = el["x"], el["y"]
    pts = el.get("points", [[0, 0]])
    sw = el.get("strokeWidth", 2)
    sc = el.get("strokeColor", "#1e1e1e")
    op = opacity_val(el.get("opacity", 100))
    ss = el.get("strokeStyle", "solid")
    da = stroke_dasharray(ss, sw)

    # Build absolute coordinate list
    abs_pts = [(x + p[0], y + p[1]) for p in pts]

    if len(abs_pts) == 2:
        # Simple line
        x1, y1 = abs_pts[0]
        x2, y2 = abs_pts[1]
        attrs = (
            f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{sc}" stroke-width="{sw}" fill="none" '
            f'marker-end="url(#{marker_id})"'
        )
        if da:
            attrs += f' stroke-dasharray="{da}"'
        if op < 1:
            attrs += f' opacity="{op}"'
        parts.append(f"<line {attrs}/>")
    else:
        # Polyline for multi-point arrows
        points_str = " ".join(f"{px},{py}" for px, py in abs_pts)
        attrs = (
            f'points="{points_str}" '
            f'stroke="{sc}" stroke-width="{sw}" fill="none" '
            f'marker-end="url(#{marker_id})"'
        )
        if da:
            attrs += f' stroke-dasharray="{da}"'
        if op < 1:
            attrs += f' opacity="{op}"'
        parts.append(f"<polyline {attrs}/>")

    return parts


def render_line(el: dict) -> List[str]:
    """Render a line (no arrowhead)."""
    parts = []
    x, y = el["x"], el["y"]
    pts = el.get("points", [[0, 0]])
    sw = el.get("strokeWidth", 2)
    sc = el.get("strokeColor", "#1e1e1e")
    op = opacity_val(el.get("opacity", 100))
    ss = el.get("strokeStyle", "solid")
    da = stroke_dasharray(ss, sw)

    abs_pts = [(x + p[0], y + p[1]) for p in pts]

    if len(abs_pts) == 2:
        x1, y1 = abs_pts[0]
        x2, y2 = abs_pts[1]
        attrs = f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{sc}" stroke-width="{sw}" fill="none"'
        if da:
            attrs += f' stroke-dasharray="{da}"'
        if op < 1:
            attrs += f' opacity="{op}"'
        parts.append(f"<line {attrs}/>")
    else:
        points_str = " ".join(f"{px},{py}" for px, py in abs_pts)
        attrs = f'points="{points_str}" stroke="{sc}" stroke-width="{sw}" fill="none"'
        if da:
            attrs += f' stroke-dasharray="{da}"'
        if op < 1:
            attrs += f' opacity="{op}"'
        parts.append(f"<polyline {attrs}/>")

    return parts


def render_text(el: dict, font_fallback: str) -> List[str]:
    """Render a standalone text element."""
    text = el.get("text", "")
    if not text:
        return []
    return _render_free_text(el, font_fallback)


def _render_bound_text(el: dict, font_fallback: str) -> List[str]:
    """Render text that is bound inside a shape (rectangle, ellipse, diamond)."""
    parts = []
    text = el.get("text", "")
    if not text:
        return parts

    x, y = el["x"], el["y"]
    w, h = el["width"], el["height"]
    font_size = el.get("fontSize", 20)
    sc = el.get("strokeColor", "#1e1e1e")
    op = opacity_val(el.get("opacity", 100))
    t_align = el.get("textAlign", "center")
    v_align = el.get("verticalAlign", "middle")
    ff = get_font_family(el, font_fallback)

    lines = text.split("\n")
    line_height = font_size * 1.25
    total_text_h = len(lines) * line_height

    # Vertical position
    if v_align == "middle":
        start_y = y + (h - total_text_h) / 2 + font_size
    elif v_align == "bottom":
        start_y = y + h - total_text_h + font_size * 0.2
    else:  # top
        start_y = y + font_size * 1.1

    # Horizontal anchor
    if t_align == "center":
        tx = x + w / 2
        anchor = "middle"
    elif t_align == "right":
        tx = x + w - 8
        anchor = "end"
    else:
        tx = x + 8
        anchor = "start"

    for i, line in enumerate(lines):
        ly = start_y + i * line_height
        attrs = (
            f'x="{tx}" y="{ly}" '
            f'font-family="{ff}" font-size="{font_size}" '
            f'fill="{sc}" text-anchor="{anchor}"'
        )
        if op < 1:
            attrs += f' opacity="{op}"'
        parts.append(f'<text {attrs}>{escape_xml(line)}</text>')

    return parts


def _render_free_text(el: dict, font_fallback: str) -> List[str]:
    """Render a standalone (free-floating) text element."""
    parts = []
    text = el.get("text", "")
    if not text:
        return parts

    x, y = el["x"], el["y"]
    w = el.get("width", 0)
    font_size = el.get("fontSize", 20)
    sc = el.get("strokeColor", "#1e1e1e")
    op = opacity_val(el.get("opacity", 100))
    t_align = el.get("textAlign", "left")
    ff = get_font_family(el, font_fallback)

    lines = text.split("\n")
    line_height = font_size * 1.25

    # Horizontal anchor
    if t_align == "center":
        tx = x + w / 2
        anchor = "middle"
    elif t_align == "right":
        tx = x + w
        anchor = "end"
    else:
        tx = x
        anchor = "start"

    start_y = y + font_size  # baseline offset

    for i, line in enumerate(lines):
        ly = start_y + i * line_height
        attrs = (
            f'x="{tx}" y="{ly}" '
            f'font-family="{ff}" font-size="{font_size}" '
            f'fill="{sc}" text-anchor="{anchor}"'
        )
        if op < 1:
            attrs += f' opacity="{op}"'
        parts.append(f'<text {attrs}>{escape_xml(line)}</text>')

    return parts


# ── Main converter ────────────────────────────────────────────────────────────

def convert(
    input_path: str,
    output_path: Optional[str] = None,
    padding: int = 40,
    bg_override: Optional[str] = None,
    no_bg: bool = False,
    font_fallback: str = DEFAULT_FONT_FALLBACK,
    scale: float = 1.0,
    pretty: bool = False,
) -> str:
    """
    Convert an .excalidraw file to SVG.

    Returns the output file path.
    """
    inp = Path(input_path)
    if not inp.exists():
        raise FileNotFoundError(f"Input file not found: {inp}")

    with open(inp, "r", encoding="utf-8") as f:
        data = json.load(f)

    elements = [e for e in data.get("elements", []) if not e.get("isDeleted", False)]
    app_state = data.get("appState", {})

    # Background color
    if no_bg:
        bg = "none"
    elif bg_override:
        bg = bg_override
    else:
        bg = app_state.get("viewBackgroundColor", "#ffffff")

    # Bounding box
    min_x, min_y, max_x, max_y = compute_bbox(elements)
    vb_x = min_x - padding
    vb_y = min_y - padding
    vb_w = (max_x - min_x) + 2 * padding
    vb_h = (max_y - min_y) + 2 * padding

    out_w = vb_w * scale
    out_h = vb_h * scale

    # Build SVG
    svg_parts = []
    svg_parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{vb_x} {vb_y} {vb_w} {vb_h}" '
        f'width="{out_w}" height="{out_h}">'
    )

    # Defs: arrowhead marker
    marker_id = "arrowhead"
    svg_parts.append(f"""<defs>
  <marker id="{marker_id}" viewBox="0 0 10 10" refX="9" refY="5"
          markerWidth="8" markerHeight="8" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#1e1e1e"/>
  </marker>
</defs>""")

    # Background rect
    if bg != "none":
        svg_parts.append(
            f'<rect x="{vb_x}" y="{vb_y}" width="{vb_w}" height="{vb_h}" fill="{bg}"/>'
        )

    # Sort elements by index for correct stacking
    def sort_key(el):
        idx = el.get("index", "")
        return idx if idx else ""

    elements.sort(key=sort_key)

    # Render each element
    for el in elements:
        etype = el.get("type", "")
        try:
            if etype == "rectangle":
                svg_parts.extend(render_rectangle(el, font_fallback))
            elif etype == "ellipse":
                svg_parts.extend(render_ellipse(el, font_fallback))
            elif etype == "diamond":
                svg_parts.extend(render_diamond(el, font_fallback))
            elif etype == "arrow":
                svg_parts.extend(render_arrow(el, marker_id))
            elif etype == "line":
                svg_parts.extend(render_line(el))
            elif etype == "text":
                svg_parts.extend(render_text(el, font_fallback))
            else:
                # Unknown type — add a comment
                svg_parts.append(f"<!-- unsupported element type: {etype} -->")
        except Exception as exc:
            svg_parts.append(f"<!-- error rendering element {el.get('id', '?')}: {exc} -->")

    svg_parts.append("</svg>")

    # Assemble
    nl = "\n" if pretty else ""
    svg_content = nl.join(svg_parts)

    # Write output
    if output_path:
        out = Path(output_path)
    else:
        out = inp.with_suffix(".svg")

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg_content)

    return str(out)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert .excalidraw JSON files to SVG format."
    )
    parser.add_argument("input", help="Input .excalidraw file path")
    parser.add_argument("output", nargs="?", default=None, help="Output .svg file path (default: same name with .svg)")
    parser.add_argument("--padding", type=int, default=40, help="Padding around diagram (default: 40)")
    parser.add_argument("--bg", default=None, help="Override background color")
    parser.add_argument("--no-bg", action="store_true", help="Transparent background")
    parser.add_argument("--font-family", default=DEFAULT_FONT_FALLBACK, help="Fallback CSS font-family")
    parser.add_argument("--scale", type=float, default=1.0, help="Scale factor (default: 1.0)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print SVG XML")

    args = parser.parse_args()

    try:
        out = convert(
            input_path=args.input,
            output_path=args.output,
            padding=args.padding,
            bg_override=args.bg,
            no_bg=args.no_bg,
            font_fallback=args.font_family,
            scale=args.scale,
            pretty=args.pretty,
        )
        print(f"✅ SVG saved to: {out}")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
