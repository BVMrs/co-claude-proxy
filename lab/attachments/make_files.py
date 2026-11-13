"""Write test attachments into <dir>: a small PNG, a large noisy PNG, a 2-page text PDF,
and an .ipynb whose cell output is a PNG. Usage: python3 make_files.py <dir>"""
import base64
import json
import pathlib
import random
import struct
import sys
import zlib

d = pathlib.Path(sys.argv[1])
d.mkdir(parents=True, exist_ok=True)


def png(w, h, noise=False):
    r = random.Random(1)
    rows = []
    for _ in range(h):
        px = bytes(r.randrange(256) for _ in range(w * 3)) if noise else bytes((x * 7) % 256 for x in range(w * 3))
        rows.append(b"\x00" + px)

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def pdf(pages):
    T = "/" + "Type"  # spelled out at runtime only
    objs = [f"<< {T} /Catalog /Pages 2 0 R >>", None, f"<< {T} /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for txt in pages:
        s = f"BT /F1 24 Tf 72 700 Td ({txt}) Tj ET"
        objs.append(f"<< /Length {len(s)} >>\nstream\n{s}\nendstream")
        objs.append(f"<< {T} /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R "
                    "/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< {T} /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return out


(d / "small.png").write_bytes(png(64, 48))
(d / "screen.png").write_bytes(png(1600, 1000, noise=True))
(d / "spec.pdf").write_bytes(pdf(["MAGIC-PDF-WORD-ZEBRA page one", "page two"]))
nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
    {"cell_type": "code", "id": "c1", "metadata": {}, "execution_count": 1, "source": ["plot()"],
     "outputs": [{"output_type": "display_data", "metadata": {}, "data": {
         "image/png": base64.b64encode(png(32, 32)).decode(), "text/plain": ["<Figure>"]}}]}]}
(d / "plot.ipynb").write_text(json.dumps(nb))
for p in sorted(d.iterdir()):
    print(p.name, p.stat().st_size)
