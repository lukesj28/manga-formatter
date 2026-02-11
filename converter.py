"""
CBZ → XTC conversion engine.
Extracts images from CBZ files, processes them for main (full vertical)
and zoom (overlapping thirds, rotated), then writes XTC files.
"""

import os
import re
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from xtc import build_xtc, build_single_page_xtc


# ---------- defaults ----------
DEFAULT_SETTINGS = {
    "dithering": True,
    "contrast": 4,
    "target_width": 480,
    "target_height": 800,
}


def _parse_settings(raw):
    """Merge user-supplied settings dict with defaults."""
    s = dict(DEFAULT_SETTINGS)
    if raw:
        if "dithering" in raw:
            s["dithering"] = bool(raw["dithering"])
        if "contrast" in raw:
            s["contrast"] = max(0, min(8, int(raw["contrast"])))
        if "target_width" in raw:
            s["target_width"] = int(raw["target_width"])
        if "target_height" in raw:
            s["target_height"] = int(raw["target_height"])
    return s


# ------------------------------------------------------------------
# Chapter number extraction
# ------------------------------------------------------------------

# Patterns that suggest "chapter"
_CH_PATTERNS = [
    re.compile(r'chapter\s*[._-]?\s*(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'chp\s*[._-]?\s*(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'ch\s*[._-]?\s*(\d+(?:\.\d+)?)', re.IGNORECASE),
]

# Patterns that suggest "volume / book" (noise to strip)
_VOL_NOISE = re.compile(
    r'(?:vol(?:ume)?|book|bk)\s*[._-]?\s*\d+(?:\.\d+)?',
    re.IGNORECASE,
)


def extract_chapter_number(filename):
    """
    Extract chapter number from a filename.

    Priority:
      1. Numbers following chapter/chp/ch keywords
      2. Remaining standalone numbers after stripping vol/volume/book/bk noise

    For decimal chapters (e.g. 12.5), returns the float so the caller
    can decide whether to use the integer part.
    Returns (int_chapter, is_decimal) or (None, False).
    """
    base = os.path.splitext(os.path.basename(filename))[0]

    # 1) Look for explicit chapter keywords
    for pat in _CH_PATTERNS:
        m = pat.search(base)
        if m:
            num_str = m.group(1)
            if '.' in num_str:
                return int(num_str.split('.')[0]), True
            return int(num_str), False

    # 2) Strip volume/book noise and look for remaining numbers
    cleaned = _VOL_NOISE.sub('', base)
    # Find all standalone numbers (not part of words like resolution)
    numbers = re.findall(r'(?<!\d)(\d+(?:\.\d+)?)(?!\d)', cleaned)
    if numbers:
        # Take the last number found (often the chapter in patterns like
        # "Manga Name 005" or "Title - 012")
        num_str = numbers[-1]
        if '.' in num_str:
            return int(num_str.split('.')[0]), True
        return int(num_str), False

    return None, False


def classify_cbz_files(cbz_paths):
    """
    Classify a list of CBZ file paths into recognized and unrecognized.

    Returns:
        recognized: dict {chapter_num (int): cbz_path}
        unrecognized: list of cbz_paths that couldn't be auto-assigned
    """
    recognized = {}
    unrecognized = []

    for path in cbz_paths:
        ch_num, is_decimal = extract_chapter_number(path)
        if ch_num is None:
            unrecognized.append(path)
        elif is_decimal and ch_num in recognized:
            # Decimal chapter and integer chapter already claimed this slot
            # Skip — don't overwrite
            continue
        elif ch_num in recognized:
            # Duplicate chapter number — can't auto-resolve
            unrecognized.append(path)
        else:
            recognized[ch_num] = path

    return recognized, unrecognized


# ------------------------------------------------------------------
# Image helpers
# ------------------------------------------------------------------

def _apply_contrast(img, level):
    """Apply contrast boost identical to cbz2xtc.py defaults."""
    if level == 0:
        return img
    black_cutoff = 3 * level
    white_cutoff = 3 + 9 * level
    return ImageOps.autocontrast(img, cutoff=(black_cutoff, white_cutoff), preserve_tone=True)


def _to_grayscale(img):
    if img.mode == "P":
        img = img.convert("RGB")
    if img.mode != "L":
        img = img.convert("L")
    return img


def _resize_and_pad(img, tw, th, dithering=True):
    """Resize to fit target, apply optional dithering, center on padded canvas."""
    iw, ih = img.size
    scale = min(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)

    resized = img.resize((nw, nh), Image.LANCZOS)

    if dithering:
        resized = resized.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")

    canvas = Image.new("L", (tw, th), 255)
    canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


# ------------------------------------------------------------------
# Main page processing (full vertical, no rotation)
# ------------------------------------------------------------------

def _process_main_page(img, settings):
    """Return a single PIL Image ready for XTC – full page, upright."""
    img = _to_grayscale(img)
    img = _apply_contrast(img, settings["contrast"])
    return _resize_and_pad(img, settings["target_width"], settings["target_height"],
                           dithering=settings["dithering"])


# ------------------------------------------------------------------
# Zoom page processing (overlapping thirds, rotated 90° CW)
# ------------------------------------------------------------------

def _process_zoom_page(img, settings):
    """
    Split a vertical page into 3 overlapping horizontal thirds,
    rotate each 90° CW, and resize to target.
    Returns list of 3 PIL Images.
    """
    img = _to_grayscale(img)
    img = _apply_contrast(img, settings["contrast"])

    tw = settings["target_width"]
    th = settings["target_height"]
    width, height = img.size

    # ---- overlapping-thirds math from cbz2xtc.py ----
    desired_segments = 3
    established_scale = th * 1.0 / width
    overlapping_height = tw / established_scale

    num_segments = desired_segments
    min_overlap_pct = 5

    if num_segments > 1:
        shift = overlapping_height - (overlapping_height * num_segments - height) / (num_segments - 1)
    else:
        shift = 0

    while num_segments < 26 and shift > 0 and (shift / overlapping_height) > (1.0 - 0.01 * min_overlap_pct):
        num_segments += 1
        if num_segments > 1:
            shift = overlapping_height - (overlapping_height * num_segments - height) / (num_segments - 1)
        else:
            shift = 0

    segments = []
    for v in range(num_segments):
        top = int(shift * v)
        bottom = int(height - shift * (num_segments - v - 1))
        segment = img.crop((0, top, width, bottom))
        rotated = segment.rotate(-90, expand=True)
        processed = _resize_and_pad(rotated, tw, th, dithering=settings["dithering"])
        segments.append(processed)

    return segments


# ------------------------------------------------------------------
# CBZ extraction
# ------------------------------------------------------------------

def _extract_images(cbz_path):
    """Extract and return sorted list of PIL Images from a CBZ."""
    images = []
    with zipfile.ZipFile(cbz_path, "r") as zf:
        names = zf.namelist()
        exts = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
        img_files = sorted(
            f for f in names
            if f.lower().endswith(exts) and not f.lower().startswith("__macos")
        )
        for f in img_files:
            data = zf.read(f)
            img = Image.open(BytesIO(data))
            if img.mode == "P":
                img = img.convert("RGB")
            images.append(img)
    return images


def get_cbz_preview(cbz_path, max_size=(300, 500)):
    """Extract the first page of a CBZ and return as JPEG bytes."""
    images = _extract_images(cbz_path)
    if not images:
        return None
    img = images[0]
    img.thumbnail(max_size, Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def convert_chapter(cbz_path, ch_num, root, settings, progress_cb=None, progress_info=None):
    """
    Convert a single CBZ file into the structured XTC output for one chapter.

    root: Path to the manga output directory (e.g. output/MangaTitle)
    ch_num: integer chapter number
    """
    s = _parse_settings(settings)
    force_size = (s["target_width"], s["target_height"])
    ch_label = f"{ch_num:04d}"
    ch_dir = root / ch_label
    ch_dir.mkdir(parents=True, exist_ok=True)
    zoom_dir = ch_dir / f"zoom_{ch_label}"
    zoom_dir.mkdir(parents=True, exist_ok=True)

    if progress_cb and progress_info:
        progress_cb(*progress_info, f"Extracting chapter {ch_num}")

    images = _extract_images(cbz_path)

    # ---- main: one multi-page XTC ----
    if progress_cb and progress_info:
        progress_cb(*progress_info, f"Building main XTC for chapter {ch_num}")

    main_pages = [_process_main_page(img, s) for img in images]
    build_xtc(main_pages, str(ch_dir / f"main_{ch_label}.xtc"), force_size)

    # ---- zoom: per-page, 3 splits each ----
    if progress_cb and progress_info:
        progress_cb(*progress_info, f"Building zoom XTCs for chapter {ch_num}")

    for page_idx, img in enumerate(images, start=1):
        splits = _process_zoom_page(img, s)
        fname = f"{ch_label}_{page_idx}.xtc"
        build_xtc(splits, str(zoom_dir / fname), force_size)


def convert_chapters(chapter_map, output_dir, manga_title, settings=None, progress_cb=None):
    """
    Convert a mapping of {chapter_num: cbz_path} into structured XTC output.

    Output layout under output_dir/manga_title/:
        0001/
            main_0001.xtc
            zoom_0001/ ...
        0005/ ...

    Returns the root output path.
    """
    root = Path(output_dir) / manga_title
    root.mkdir(parents=True, exist_ok=True)

    total = len(chapter_map)
    for idx, (ch_num, cbz_path) in enumerate(sorted(chapter_map.items()), start=1):
        info = (idx, total) if progress_cb else None
        convert_chapter(cbz_path, ch_num, root, settings, progress_cb, info)

        if progress_cb:
            progress_cb(idx, total, f"Chapter {ch_num} done ({idx}/{total})")

    return str(root)


# Legacy wrapper for backwards compatibility
def convert_folder(cbz_files, output_dir, manga_title, settings=None, progress_cb=None):
    """
    Convert a list of CBZ file paths into the structured XTC output.
    Uses chapter number extraction; falls back to sequential numbering.
    """
    recognized, unrecognized = classify_cbz_files(cbz_files)

    # Assign sequential numbers to unrecognized files
    next_ch = max(recognized.keys(), default=0) + 1000
    for path in unrecognized:
        recognized[next_ch] = path
        next_ch += 1

    return convert_chapters(recognized, output_dir, manga_title, settings, progress_cb)
