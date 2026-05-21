"""Generate a PostgreSQL-themed application icon (.ico) for PyInstaller builds.

Creates a multi-resolution .ico file with a database/elephant-inspired design
in PostgreSQL blue tones.
"""

import struct
import zlib
from pathlib import Path


def create_png_rgba(size: int) -> bytes:
    """Create a PNG image in memory with a PostgreSQL-themed database icon."""
    # Simple database cylinder icon in PostgreSQL blue (#336791)
    pixels = []
    center_x = size // 2
    center_y = size // 2
    radius = int(size * 0.38)
    bg_r, bg_g, bg_b = 0x33, 0x67, 0x91  # PostgreSQL blue
    highlight_r, highlight_g, highlight_b = 0x52, 0x9B, 0xC9
    arrow_r, arrow_g, arrow_b = 0x4C, 0xAF, 0x50  # Green for backup arrow

    for y in range(size):
        row = []
        for x in range(size):
            dx = x - center_x
            dy = y - center_y

            # Circular background
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= radius:
                # Database cylinder shape
                cylinder_top = center_y - int(radius * 0.5)
                cylinder_bottom = center_y + int(radius * 0.5)
                ellipse_height = int(radius * 0.25)

                # Top ellipse
                in_top_ellipse = False
                ey = y - cylinder_top
                if abs(ey) <= ellipse_height:
                    ex_radius = radius * 0.7 * (1 - (ey / ellipse_height) ** 2) ** 0.5 if abs(ey) < ellipse_height else 0
                    if abs(dx) <= ex_radius:
                        in_top_ellipse = True

                # Bottom ellipse
                in_bottom_ellipse = False
                ey_b = y - cylinder_bottom
                if abs(ey_b) <= ellipse_height:
                    ex_radius_b = radius * 0.7 * (1 - (ey_b / ellipse_height) ** 2) ** 0.5 if abs(ey_b) < ellipse_height else 0
                    if abs(dx) <= ex_radius_b:
                        in_bottom_ellipse = True

                # Cylinder body
                in_body = (cylinder_top <= y <= cylinder_bottom) and abs(dx) <= radius * 0.7

                # Backup arrow (down arrow in bottom-right)
                arrow_cx = center_x + int(radius * 0.45)
                arrow_cy = center_y + int(radius * 0.35)
                arrow_size = int(radius * 0.3)
                in_arrow = False
                adx = x - arrow_cx
                ady = y - arrow_cy
                # Arrow shaft
                if abs(adx) <= arrow_size * 0.2 and -arrow_size <= ady <= arrow_size * 0.4:
                    in_arrow = True
                # Arrow head
                if ady >= arrow_size * 0.1 and ady <= arrow_size * 0.7:
                    head_width = arrow_size * 0.6 * (ady - arrow_size * 0.1) / (arrow_size * 0.6)
                    if abs(adx) <= arrow_size * 0.6 - head_width:
                        in_arrow = True

                if in_arrow:
                    row.extend([arrow_r, arrow_g, arrow_b, 255])
                elif in_top_ellipse:
                    row.extend([highlight_r, highlight_g, highlight_b, 255])
                elif in_body or in_bottom_ellipse:
                    row.extend([bg_r, bg_g, bg_b, 255])
                else:
                    # Inside circle but outside cylinder - darker shade
                    row.extend([bg_r // 2, bg_g // 2, bg_b // 2, 200])
            else:
                # Transparent
                row.extend([0, 0, 0, 0])
        pixels.append(bytes(row))

    # Encode as PNG
    return _encode_png(size, size, pixels)


def _encode_png(width: int, height: int, rows: list[bytes]) -> bytes:
    """Minimal PNG encoder for RGBA data."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"

    # IHDR
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr = _chunk(b"IHDR", ihdr_data)

    # IDAT
    raw_data = b""
    for row in rows:
        raw_data += b"\x00" + row  # filter byte 0 (None) per row

    compressed = zlib.compress(raw_data, 9)
    idat = _chunk(b"IDAT", compressed)

    # IEND
    iend = _chunk(b"IEND", b"")

    return sig + ihdr + idat + iend


def create_ico(output_path: Path) -> None:
    """Create a multi-resolution .ico file."""
    sizes = [16, 32, 48, 64, 128, 256]
    images = []

    for size in sizes:
        png_data = create_png_rgba(size)
        images.append((size, png_data))

    # ICO file format
    # Header: reserved(2) + type(2) + count(2)
    num_images = len(images)
    header = struct.pack("<HHH", 0, 1, num_images)

    # Calculate offsets
    dir_entry_size = 16
    data_offset = 6 + num_images * dir_entry_size

    directory = b""
    image_data = b""

    for size, png_data in images:
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entry = struct.pack(
            "<BBBBHHII",
            w,  # width (0 = 256)
            h,  # height (0 = 256)
            0,  # color palette
            0,  # reserved
            1,  # color planes
            32,  # bits per pixel
            len(png_data),  # size of image data
            data_offset + len(image_data),  # offset to image data
        )
        directory += entry
        image_data += png_data

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + directory + image_data)
    print(f"Icon created: {output_path} ({len(images)} sizes)")


if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    icon_path = project_root / "build_scripts" / "app_icon.ico"
    create_ico(icon_path)
