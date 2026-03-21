from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def _add_library_search_path(path: Path) -> None:
    env_var = "DYLD_FALLBACK_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    existing = os.environ.get(env_var, "")
    parts = [entry for entry in existing.split(":") if entry]
    if str(path) not in parts:
        os.environ[env_var] = ":".join([str(path), *parts]) if parts else str(path)


def _configure_cairo_runtime() -> None:
    candidates: list[Path] = []

    if sys.platform == "darwin":
        brew = shutil.which("brew")
        if brew:
            result = subprocess.run([brew, "--prefix", "cairo"], capture_output=True, text=True, check=False)
            if result.returncode == 0:
                prefix = result.stdout.strip()
                if prefix:
                    candidates.append(Path(prefix) / "lib")
        candidates.extend([
            Path("/opt/homebrew/opt/cairo/lib"),
            Path("/usr/local/opt/cairo/lib"),
        ])

    for candidate in candidates:
        if (candidate / "libcairo.2.dylib").exists() or (candidate / "libcairo.dylib").exists():
            _add_library_search_path(candidate)
            break


def _render_svg_to_png(svg_text: str, render_width: int) -> bytes:
    _configure_cairo_runtime()
    import cairosvg

    return cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), output_width=render_width)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crop dead space from SVGs by tightening their root viewBox.",
    )
    parser.add_argument("input_path", type=Path, help="Input SVG file or directory.")
    parser.add_argument(
        "output_path",
        type=Path,
        nargs="?",
        help="Output SVG file or directory. Defaults to a sibling *_cropped path.",
    )
    parser.add_argument(
        "--target-fill",
        type=float,
        default=0.9,
        help="Target fill ratio for the dominant dimension. Default: 0.9",
    )
    parser.add_argument(
        "--alpha-threshold",
        type=int,
        default=8,
        help="Alpha threshold used to detect non-empty pixels. Default: 8",
    )
    parser.add_argument(
        "--render-width",
        type=int,
        default=1024,
        help="Raster width used to measure bounds. Default: 1024",
    )
    parser.add_argument(
        "--include-static",
        action="store_true",
        help="Also process svg-static directories when the input is a directory.",
    )
    return parser.parse_args()


def default_output_path(input_path: Path) -> Path:
    suffix = input_path.suffix if input_path.is_file() else ""
    stem = input_path.stem if input_path.is_file() else input_path.name
    return input_path.with_name(f"{stem}_cropped{suffix}")


def iter_svg_files(input_path: Path, include_static: bool) -> Iterable[Path]:
    if input_path.is_file():
        yield input_path
        return

    for path in sorted(input_path.rglob("*.svg")):
        if not include_static and "svg-static" in path.parts:
            continue
        yield path


def parse_viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    viewbox = root.attrib.get("viewBox")
    if viewbox:
        values = [float(part) for part in viewbox.replace(",", " ").split()]
        if len(values) == 4:
            return values[0], values[1], values[2], values[3]

    width = float(str(root.attrib.get("width", "512")).replace("px", ""))
    height = float(str(root.attrib.get("height", "512")).replace("px", ""))
    return 0.0, 0.0, width, height


def raster_bbox(svg_text: str, render_width: int, alpha_threshold: int) -> tuple[int, int, int, int] | None:
    png_bytes = _render_svg_to_png(svg_text, render_width)
    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    alpha = image.getchannel("A")
    mask = alpha.point(lambda value: 255 if value >= alpha_threshold else 0)
    return mask.getbbox()


def compute_cropped_viewbox(
    original_viewbox: tuple[float, float, float, float],
    pixel_bbox: tuple[int, int, int, int],
    raster_size: tuple[int, int],
    target_fill: float,
) -> tuple[float, float, float, float]:
    viewbox_x, viewbox_y, viewbox_w, viewbox_h = original_viewbox
    raster_w, raster_h = raster_size
    left, top, right, bottom = pixel_bbox
    bbox_w = right - left
    bbox_h = bottom - top

    bbox_cx = viewbox_x + ((left + bbox_w / 2) / raster_w) * viewbox_w
    bbox_cy = viewbox_y + ((top + bbox_h / 2) / raster_h) * viewbox_h
    bbox_viewbox_w = (bbox_w / raster_w) * viewbox_w
    bbox_viewbox_h = (bbox_h / raster_h) * viewbox_h

    scale = max(
        bbox_viewbox_w / (target_fill * viewbox_w),
        bbox_viewbox_h / (target_fill * viewbox_h),
    )
    crop_w = viewbox_w * scale
    crop_h = viewbox_h * scale
    crop_x = bbox_cx - crop_w / 2
    crop_y = bbox_cy - crop_h / 2
    return crop_x, crop_y, crop_w, crop_h


def replace_viewbox(svg_text: str, new_viewbox: tuple[float, float, float, float]) -> str:
    x, y, width, height = (round(value, 3) for value in new_viewbox)
    viewbox_text = f'{x:g} {y:g} {width:g} {height:g}'
    pattern = re.compile(r'(<svg\b[^>]*\bviewBox=)(["\'])([^"\']*)(\2)', re.IGNORECASE | re.DOTALL)
    if pattern.search(svg_text):
        return pattern.sub(rf'\1"{viewbox_text}"', svg_text, count=1)

    insert_at = svg_text.find(">")
    if insert_at == -1:
        raise ValueError("Unable to locate <svg> tag in document")
    return f'{svg_text[:insert_at]} viewBox="{viewbox_text}"{svg_text[insert_at:]}'


def crop_svg_file(input_file: Path, output_file: Path, target_fill: float, alpha_threshold: int, render_width: int) -> bool:
    svg_text = input_file.read_text(encoding="utf-8")
    root = ET.fromstring(svg_text)
    original_viewbox = parse_viewbox(root)
    pixel_bbox = raster_bbox(svg_text, render_width, alpha_threshold)
    if pixel_bbox is None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(svg_text, encoding="utf-8")
        return False

    raster_w = render_width
    raster_h = max(1, round(render_width * original_viewbox[3] / original_viewbox[2]))
    new_viewbox = compute_cropped_viewbox(
        original_viewbox,
        pixel_bbox,
        (raster_w, raster_h),
        target_fill,
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(replace_viewbox(svg_text, new_viewbox), encoding="utf-8")
    return True


def main() -> None:
    args = parse_args()
    input_path = args.input_path.resolve()
    output_path = (args.output_path or default_output_path(input_path)).resolve()

    if not 0 < args.target_fill < 1:
        raise SystemExit("--target-fill must be between 0 and 1")

    svg_files = list(iter_svg_files(input_path, include_static=args.include_static))
    processed = 0
    unchanged = 0

    for input_file in svg_files:
        if input_path.is_file():
            target_file = output_path
        else:
            target_file = output_path / input_file.relative_to(input_path)

        changed = crop_svg_file(
            input_file=input_file,
            output_file=target_file,
            target_fill=args.target_fill,
            alpha_threshold=args.alpha_threshold,
            render_width=args.render_width,
        )
        processed += 1
        if not changed:
            unchanged += 1

    print(f"Processed {processed} SVGs -> {output_path}")
    if unchanged:
        print(f"Copied {unchanged} SVGs unchanged because no visible pixels were detected.")


if __name__ == "__main__":
    main()