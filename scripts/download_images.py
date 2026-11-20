"""Скачивает фотографии по photo_url из jsonl (наш датасет или экзаменационный набор Контеста).

    python scripts/download_images.py                                   # наш датасет → data/images/
    python scripts/download_images.py data/exam/dataset.jsonl --out data/exam/images

Только стандартная библиотека Python 3.9+. Файл называется <photo_id>.<расширение из ссылки>.
Уже скачанные файлы пропускаются. Если в строке есть sha256 (наш датасет), файл сверяется
с ним побайтно — это гарантирует, что фото то же, что видели исполнители. Картинки не
коммитить: папки с ними должны быть в .gitignore.
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = "butterfly-detection-dataset-downloader/1.0"


def target(row, out):
    ext = row["photo_url"].rsplit(".", 1)[-1].lower()
    return Path(out) / f"{row['photo_id']}.{ext}"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch(row, out, retries):
    path = target(row, out)
    want = row.get("sha256")
    if path.is_file() and path.stat().st_size > 0 and (not want or sha256(path) == want):
        return "skip", row["photo_id"], ""
    last = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(row["photo_url"], headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if not data:
                raise ValueError("пустой ответ")
            if want and hashlib.sha256(data).hexdigest() != want:
                raise ValueError("sha256 не совпадает с датасетом")
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            return "ok", row["photo_id"], ""
        except Exception as e:                      # сеть, HTTP, хеш — пробуем ещё раз
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
    return "fail", row["photo_id"], last


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("jsonl", nargs="?", default="data/sources_and_labels.jsonl")
    p.add_argument("--out", default="data/images")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--limit", type=int, help="скачать только первые N (для проверки)")
    a = p.parse_args()
    rows = [json.loads(x) for x in Path(a.jsonl).read_text(encoding="utf-8").splitlines() if x.strip()]
    if a.limit:
        rows = rows[:a.limit]
    Path(a.out).mkdir(parents=True, exist_ok=True)
    stats, failed = {"ok": 0, "skip": 0, "fail": 0}, []
    with ThreadPoolExecutor(a.workers) as ex:
        for n, (status, pid, err) in enumerate(ex.map(lambda r: fetch(r, a.out, a.retries), rows), 1):
            stats[status] += 1
            if status == "fail":
                failed.append((pid, err))
            if n % 50 == 0 or n == len(rows):
                print(f"{n}/{len(rows)}: скачано {stats['ok']}, уже было {stats['skip']}, ошибок {stats['fail']}", flush=True)
    for pid, err in failed:
        print(f"  не скачано {pid}: {err}", file=sys.stderr)
    checked = "со сверкой sha256" if any(r.get("sha256") for r in rows) else "без сверки (в файле нет sha256)"
    print(f"Готово: {a.out}, {checked}. Повторный запуск докачает пропущенное.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
