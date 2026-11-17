"""Рисует рамки поверх фото, чтобы глазами проверить разметку и предсказания.

Эталон рисуется зелёным, предсказания красным (с уверенностью, если она есть).

Использование:
    python scripts/draw_boxes.py data/sources_and_labels.jsonl --golden golden/golden.jsonl --sample 20
    python scripts/draw_boxes.py data/sources_and_labels.jsonl --pred predictions/gdino.jsonl --ids 123 456
    python scripts/draw_boxes.py data/sources_and_labels.jsonl --golden golden/golden.jsonl --pred predictions/gdino.jsonl --sample 30

Картинки берутся из папки --images (файл <photo_id>.<расширение>), а если там нет — скачиваются по photo_url.
Результат — в папке --out (по умолчанию viz/).
"""
import argparse
import io
import json
import os
import random

from PIL import Image, ImageDraw, ImageOps

GOLDEN_COLOR = (0, 200, 0)
PRED_COLOR = (230, 0, 0)


def load_jsonl(path):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                rows[row["photo_id"]] = row
    return rows


def open_image(row, images_dir):
    pid = row["photo_id"]
    if images_dir:
        for name in os.listdir(images_dir):
            if os.path.splitext(name)[0] == str(pid):
                return Image.open(os.path.join(images_dir, name))
    import requests

    resp = requests.get(row["photo_url"], timeout=60)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content))


def draw(img, boxes, color, width):
    d = ImageDraw.Draw(img)
    w, h = img.size
    for b in boxes or []:
        x0, y0 = b["left"] * w, b["top"] * h
        x1, y1 = x0 + b["width"] * w, y0 + b["height"] * h
        d.rectangle([x0, y0, x1, y1], outline=color, width=width)
        if "score" in b:
            d.text((x0 + width + 2, y0 + width + 2), f"{b['score']:.2f}", fill=color)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("photos", help="jsonl с photo_id и photo_url")
    p.add_argument("--golden", help="jsonl с эталонными рамками")
    p.add_argument("--pred", help="jsonl с предсказаниями")
    p.add_argument("--ids", nargs="*", type=int, help="конкретные photo_id")
    p.add_argument("--sample", type=int, default=20, help="сколько случайных фото нарисовать")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--images", help="папка со скачанными фото")
    p.add_argument("--out", default="viz")
    args = p.parse_args()

    photos = load_jsonl(args.photos)
    golden = load_jsonl(args.golden) if args.golden else {}
    pred = load_jsonl(args.pred) if args.pred else {}

    ids = args.ids or random.Random(args.seed).sample(sorted(photos), min(args.sample, len(photos)))
    os.makedirs(args.out, exist_ok=True)

    for pid in ids:
        try:
            img = ImageOps.exif_transpose(open_image(photos[pid], args.images)).convert("RGB")
        except Exception as e:
            print(f"{pid}: не удалось открыть ({e})")
            continue
        line_w = max(2, round(max(img.size) / 300))
        if pid in golden:
            draw(img, golden[pid]["boxes"], GOLDEN_COLOR, line_w)
        if pid in pred:
            draw(img, pred[pid].get("boxes"), PRED_COLOR, line_w)
            if pred[pid].get("error"):
                print(f"{pid}: ошибка модели: {pred[pid]['error']}")
        img.thumbnail((1600, 1600))
        img.save(os.path.join(args.out, f"{pid}.jpg"), quality=90)
        n_g = len(golden.get(pid, {}).get("boxes") or [])
        n_p = len(pred.get(pid, {}).get("boxes") or [])
        print(f"{pid}: эталон {n_g}, предсказано {n_p}")

    print(f"Готово: {args.out}/")


if __name__ == "__main__":
    main()
