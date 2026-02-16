import os
import re
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from modules.manga_formatter.xtc import build_xtc, build_single_page_xtc


DEFAULT_SETTINGS = {
    "dithering": True,
    "contrast": 4,
    "target_width": 480,
    "target_height": 800,
    "long_strip": False,
    "overlap": 33,
}


def _parse_settings(raw):
    s = dict(DEFAULT_SETTINGS)
    if raw:
        if "dithering" in raw:
            s["dithering"] = bool(raw["dithering"])
        if "long_strip" in raw:
            s["long_strip"] = bool(raw["long_strip"])
        if "overlap" in raw:
            s["overlap"] = max(0, min(90, int(raw["overlap"])))
        if "contrast" in raw:
            s["contrast"] = max(0, min(8, int(raw["contrast"])))
        if "target_width" in raw:
            s["target_width"] = int(raw["target_width"])
        if "target_height" in raw:
            s["target_height"] = int(raw["target_height"])
    return s

_CH_PATTERNS = [
    re.compile(r'chapter\s*[._-]?\s*(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'chp\s*[._-]?\s*(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'ch\s*[._-]?\s*(\d+(?:\.\d+)?)', re.IGNORECASE),
]

_VOL_NOISE = re.compile(
    r'(?:vol(?:ume)?|book|bk)\s*[._-]?\s*\d+(?:\.\d+)?',
    re.IGNORECASE,
)


def extract_chapter_number(filename):
    base = os.path.splitext(os.path.basename(filename))[0]

    for pat in _CH_PATTERNS:
        m = pat.search(base)
        if m:
            num_str = m.group(1)
            if '.' in num_str:
                return int(num_str.split('.')[0]), True
            return int(num_str), False

    cleaned = _VOL_NOISE.sub('', base)
    numbers = re.findall(r'(?<!\d)(\d+(?:\.\d+)?)(?!\d)', cleaned)
    if numbers:
        num_str = numbers[-1]
        if '.' in num_str:
            return int(num_str.split('.')[0]), True
        return int(num_str), False

    return None, False


def classify_cbz_files(cbz_paths):
    recognized = {}
    unrecognized = []

    for path in cbz_paths:
        ch_num, is_decimal = extract_chapter_number(path)
        if ch_num is None:
            unrecognized.append(path)
        elif is_decimal and ch_num in recognized:
            continue
        elif ch_num in recognized:
            unrecognized.append(path)
        else:
            recognized[ch_num] = path

    return recognized, unrecognized

def _apply_contrast(img, level):
    if level == 0:
        return img
    black_cutoff = 3 * level
    white_cutoff = 3 + 9 * level
    return ImageOps.autocontrast(img, cutoff=(black_cutoff, white_cutoff), preserve_tone=True)


def _to_grayscale(img):
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        background = Image.new("RGBA", img.size, (255, 255, 255))
        composite = Image.alpha_composite(background, img)
        return composite.convert("L")
        
    if img.mode == "P":
        img = img.convert("RGB")
    if img.mode != "L":
        img = img.convert("L")
    return img


def _resize_and_pad(img, tw, th, dithering=True):
    iw, ih = img.size
    scale = min(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)

    resized = img.resize((nw, nh), Image.LANCZOS)

    if dithering:
        resized = resized.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")

    canvas = Image.new("L", (tw, th), 255)
    canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return canvas

def _process_main_page(img, settings):
    img = _to_grayscale(img)
    img = _apply_contrast(img, settings["contrast"])
    return _resize_and_pad(img, settings["target_width"], settings["target_height"],
                           dithering=settings["dithering"])

def _process_zoom_page(img, settings):
    img = _to_grayscale(img)
    img = _apply_contrast(img, settings["contrast"])

    tw = settings["target_width"]
    th = settings["target_height"]
    width, height = img.size

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

def _process_long_strip(images, settings):
    processed_imgs = []
    target_w = settings["target_width"]
    target_h = settings["target_height"]
    overlap_pct = settings.get("overlap", 33) / 100.0
    
    overlap_px = int(target_h * overlap_pct)
    step = target_h - overlap_px
    
    if step <= 0:
        step = 10 
        overlap_px = target_h - 10
    
    total_virtual_height = 0
    img_metadata = []
    
    for img in images:
        img = _to_grayscale(img)
        img = _apply_contrast(img, settings["contrast"])
        
        w, h = img.size
        if w == 0: continue
            
        scale = target_w / w
        new_h = int(h * scale)
        
        img = img.resize((target_w, new_h), Image.LANCZOS)
        
        start_y = total_virtual_height
        end_y = start_y + new_h
        img_metadata.append({
            "img": img,
            "start_y": start_y,
            "end_y": end_y,
            "h": new_h
        })
        total_virtual_height += new_h
        
    if not img_metadata:
        return []

    segments = []
    current_y = 0
    
    while current_y < total_virtual_height:
        win_start = current_y
        win_end = current_y + target_h
        
        canvas = Image.new("L", (target_w, target_h), 255) # White bg
        
        for meta in img_metadata:
            if meta["end_y"] > win_start and meta["start_y"] < win_end:
                paste_y = meta["start_y"] - win_start
                
                src_crop_top = 0
                if paste_y < 0:
                    src_crop_top = -paste_y
                    paste_y = 0
                                                
                src_img = meta["img"]
                
                if src_crop_top > 0:
                   src_img = src_img.crop((0, src_crop_top, target_w, meta["h"]))
                
                canvas.paste(src_img, (0, int(paste_y)))

        segments.append(canvas)
        
        current_y += step
        
        pass
    return segments

def _extract_images(cbz_path):
    images = []
    with zipfile.ZipFile(cbz_path, "r") as zf:
        names = zf.namelist()
        exts = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
        img_files = sorted(
            f for f in names
            if f.lower().endswith(exts) 
            and "__macosx" not in f.lower() 
            and not os.path.basename(f).startswith(".")
        )
        for f in img_files:
            try:
                data = zf.read(f)
                img = Image.open(BytesIO(data))
                img.load()
                
                if img.mode == "P":
                    img = img.convert("RGB")
                    
                images.append(img)
            except Exception as e:
                print(f"Warning: Failed to load image {f} from {cbz_path}: {e}")
                continue
    return images


def get_cbz_preview(cbz_path, max_size=(300, 500)):
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

def convert_chapter(cbz_path, ch_num, root, settings):
    s = _parse_settings(settings)
    force_size = (s["target_width"], s["target_height"])
    ch_label = f"{ch_num:04d}"
    ch_dir = root / ch_label
    ch_dir.mkdir(parents=True, exist_ok=True)
    zoom_dir = ch_dir / f"zoom_{ch_label}"
    zoom_dir.mkdir(parents=True, exist_ok=True)

    images = _extract_images(cbz_path)



    if s["long_strip"]:
        segments = _process_long_strip(images, s)
        
        main_segments_ready = []
        for seg in segments:
            if s["dithering"]:
                seg = seg.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
            main_segments_ready.append(seg)
            
        build_xtc(main_segments_ready, str(ch_dir / f"main_{ch_label}.xtc"), force_size)
        
        zoom_settings = dict(s)
        zoom_settings["contrast"] = 0
        
        for page_idx, seg_img in enumerate(segments, start=1):
            splits = _process_zoom_page(seg_img, zoom_settings)
            fname = f"{ch_label}_{page_idx}.xtc"
            build_xtc(splits, str(zoom_dir / fname), force_size)

    else:
        main_pages = [_process_main_page(img, s) for img in images]
        build_xtc(main_pages, str(ch_dir / f"main_{ch_label}.xtc"), force_size)

        for page_idx, img in enumerate(images, start=1):
            splits = _process_zoom_page(img, s)
            fname = f"{ch_label}_{page_idx}.xtc"
            build_xtc(splits, str(zoom_dir / fname), force_size)


from concurrent.futures import ProcessPoolExecutor, as_completed

def convert_chapters(chapter_map, output_dir, manga_title, settings=None):
    root = Path(output_dir) / manga_title
    root.mkdir(parents=True, exist_ok=True)

    total = len(chapter_map)
    futures = {}

    with ProcessPoolExecutor() as executor:
        for ch_num, cbz_path in chapter_map.items():
            future = executor.submit(convert_chapter, cbz_path, ch_num, root, settings)
            futures[future] = (ch_num, os.path.basename(cbz_path))

        completed_count = 0
        for future in as_completed(futures):
            completed_count += 1
            ch_num, fname = futures[future]
            try:
                future.result()
                yield {
                    "current": completed_count,
                    "total": total,
                    "message": f"Finished chapter {ch_num}",
                    "filename": fname
                }
            except Exception as e:
                print(f"Error converting chapter {ch_num}: {e}")
                yield {
                    "current": completed_count,
                    "total": total,
                    "message": f"Error in chapter {ch_num}: {str(e)}",
                    "filename": fname
                }

    yield {
        "current": total,
        "total": total,
        "message": "All chapters processed. Creating zip archive...",
        "filename": ""
    }

    return str(root)

