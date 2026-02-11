"""
XTC/XTG binary format builder.
Ported from epub2xtc/png2xtc.py for use as a library.
"""

import hashlib
import struct
from PIL import Image


def _png_to_xtg_bytes(img: Image.Image, force_size=(480, 800), threshold=200):
    """Convert a PIL image to XTG bytes (1-bit monochrome)."""
    if img.size != force_size:
        img = img.resize(force_size, Image.LANCZOS)

    w, h = img.size
    gray = img.convert("L")
    row_bytes = (w + 7) // 8
    data = bytearray(row_bytes * h)

    pixels = gray.load()
    for y in range(h):
        for x in range(w):
            bit = 1 if pixels[x, y] >= threshold else 0
            byte_index = y * row_bytes + (x // 8)
            bit_index = 7 - (x % 8)  # MSB first
            if bit:
                data[byte_index] |= 1 << bit_index

    md5digest = hashlib.md5(data).digest()[:8]
    data_size = len(data)

    header = struct.pack(
        "<4sHHBBI8s",
        b"XTG\x00",
        w,
        h,
        0,  # colorMode
        0,  # compression
        data_size,
        md5digest,
    )
    return header + data


def build_xtc(pil_images, out_path, force_size=(480, 800)):
    """
    Build a multi-page XTC file from a list of PIL Images.
    Used for main chapter files.
    """
    xtg_blobs = [_png_to_xtg_bytes(img, force_size) for img in pil_images]

    page_count = len(xtg_blobs)
    header_size = 48
    index_entry_size = 16
    index_offset = header_size
    data_offset = index_offset + page_count * index_entry_size

    # Index table
    index_table = bytearray()
    rel_offset = data_offset
    for blob in xtg_blobs:
        w, h = struct.unpack_from("<HH", blob, 4)
        entry = struct.pack("<Q I H H", rel_offset, len(blob), w, h)
        index_table += entry
        rel_offset += len(blob)

    # XTC header
    xtc_header = struct.pack(
        "<4sHHBBBBIQQQQ",
        b"XTC\x00",
        1,           # version
        page_count,  # pageCount
        0,           # readDirection
        0,           # hasMetadata
        0,           # hasThumbnails
        0,           # hasChapters
        0,           # currentPage
        0,           # metadataOffset
        index_offset,
        data_offset,
        0,           # thumbOffset
    )

    with open(out_path, "wb") as f:
        f.write(xtc_header)
        f.write(index_table)
        for blob in xtg_blobs:
            f.write(blob)


def build_single_page_xtc(pil_image, out_path, force_size=(480, 800)):
    """
    Build a single-page XTC file from one PIL Image.
    Used for zoom split files.
    """
    build_xtc([pil_image], out_path, force_size)
