"""Рисует итог разбора для проверки глазами: красное — рамки разметчика, зелёное — итог golden.

    python labeling/golden_review/render_golden.py [--images папка_с_фото] [--sample 24]

Рисует все изменённые фото и случайную выборку неизменённых, листы по 12 фото кладёт в viz/golden/.
Фото берутся из --images (<photo_id>.jpg), иначе скачиваются по photo_url из датасета в viz/images/.
Нужен Pillow.
"""
import argparse
import json
import random
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "viz/images"


def open_photo(pid, images, urls):
    if images and (Path(images) / f"{pid}.jpg").is_file():
        return Image.open(Path(images) / f"{pid}.jpg")
    cached = CACHE / f"{pid}.jpg"
    if not cached.is_file():
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(urls[pid], timeout=60) as r:
            cached.write_bytes(r.read())
    return Image.open(cached)


def draw(row, images, urls, size=560):
    im = ImageOps.exif_transpose(open_photo(str(row["photo_id"]), images, urls)).convert("RGB")
    im.thumbnail((size, size))
    d, (w, h) = ImageDraw.Draw(im), im.size
    for boxes, color, width in ((row["labeler"]["boxes"], (255, 40, 40), 5), (row["final_boxes"], (0, 230, 0), 2)):
        for b in boxes:
            d.rectangle([b["left"] * w, b["top"] * h, (b["left"] + b["width"]) * w, (b["top"] + b["height"]) * h],
                        outline=color, width=width)
    d.rectangle([0, 0, w, 18], fill=(0, 0, 0))
    d.text((4, 3), f"{row['photo_id']} {row['source']}: {len(row['labeler']['boxes'])} -> {len(row['final_boxes'])}",
           fill=(255, 255, 0))
    return im


def sheet(rows, path, images, urls, size=560, cols=4):
    tiles = [draw(r, images, urls, size) for r in rows]
    g = Image.new("RGB", (cols * size, ((len(tiles) + cols - 1) // cols) * size), (30, 30, 30))
    for i, t in enumerate(tiles):
        g.paste(t, ((i % cols) * size, (i // cols) * size))
    g.save(path, quality=88)
    print(path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--audit", default=str(ROOT / "golden/golden_audit.jsonl"))
    p.add_argument("--dataset", default=str(ROOT / "data/sources_and_labels.jsonl"))
    p.add_argument("--images", help="папка со скачанными фото <photo_id>.jpg")
    p.add_argument("--out", default=str(ROOT / "viz/golden"))
    p.add_argument("--sample", type=int, default=24, help="сколько неизменённых фото добавить для выборочной проверки")
    p.add_argument("--seed", type=int, default=20261119)
    a = p.parse_args()
    rows = [json.loads(x) for x in Path(a.audit).read_text(encoding="utf-8").splitlines() if x.strip()]
    urls = {str(json.loads(x)["photo_id"]): json.loads(x)["photo_url"]
            for x in Path(a.dataset).read_text(encoding="utf-8").splitlines() if x.strip()}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    changed = [r for r in rows if r["changed"]]
    rest = [r for r in rows if not r["changed"] and r["final_boxes"]]
    sample = random.Random(a.seed).sample(rest, min(a.sample, len(rest)))
    for name, group in (("изменённые", changed), ("выборка", sample)):
        for i in range(0, len(group), 12):
            sheet(group[i:i + 12], out / f"{name}_{i // 12 + 1}.jpg", a.images, urls)


if __name__ == "__main__":
    main()
