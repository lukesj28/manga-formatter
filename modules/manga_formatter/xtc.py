import hashlib
import struct
from PIL import Image


def _png_to_xtg_bytes(img: Image.Image, force_size=(480, 800), threshold=200):
    if img.size != force_size:
        img = img.resize(force_size, Image.LANCZOS)

    w, h = img.size
    gray = img.convert("L")
    
    table = [0 if i < threshold else 255 for i in range(256)]
    bw = gray.point(table, mode='1')

    data = bw.tobytes()

    md5digest = hashlib.md5(data).digest()[:8]
    data_size = len(data)

    header = struct.pack(
        "<4sHHBBI8s",
        b"XTG\x00",
        w,
        h,
        0,
        0,
        data_size,
        md5digest,
    )
    return header + data


def build_xtc(pil_images, out_path, force_size=(480, 800)):
    xtg_blobs = [_png_to_xtg_bytes(img, force_size) for img in pil_images]

    page_count = len(xtg_blobs)
    header_size = 48
    index_entry_size = 16
    index_offset = header_size
    data_offset = index_offset + page_count * index_entry_size

    index_table = bytearray()
    rel_offset = data_offset
    for blob in xtg_blobs:
        w, h = struct.unpack_from("<HH", blob, 4)
        entry = struct.pack("<Q I H H", rel_offset, len(blob), w, h)
        index_table += entry
        rel_offset += len(blob)

    xtc_header = struct.pack(
        "<4sHHBBBBIQQQQ",
        b"XTC\x00",
        1,
        page_count,
        0,
        0,
        0,
        0,
        0,
        0,
        index_offset,
        data_offset,
        0,
    )

    with open(out_path, "wb") as f:
        f.write(xtc_header)
        f.write(index_table)
        for blob in xtg_blobs:
            f.write(blob)


def build_single_page_xtc(pil_image, out_path, force_size=(480, 800)):
    build_xtc([pil_image], out_path, force_size)
