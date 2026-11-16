"""Проверка файла для Яндекс Контеста по правилам из условия.

Использование:
    python scripts/check_submission.py submission/submission.jsonl data/exam/dataset.jsonl

Код выхода 0 — файл можно отправлять, 1 — есть ошибки.
"""
import json
import math
import os
import sys

MAX_BYTES = 5 * 1024 * 1024
ALLOWED_KEYS = {"photo_id", "boxes", "error", "photo_url"}
BOX_KEYS = {"left", "top", "width", "height"}


def no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    dups = {k for k in keys if keys.count(k) > 1}
    if dups:
        raise ValueError(f"повторяющиеся ключи: {sorted(dups)}")
    return dict(pairs)


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_ids(path):
    ids = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ids.append(json.loads(line)["photo_id"])
    return ids


def check(sub_path, dataset_path):
    errors, warnings = [], []

    size = os.path.getsize(sub_path)
    if size > MAX_BYTES:
        errors.append(f"файл {size / 1024 / 1024:.2f} МиБ, допустимо не больше 5 МиБ")

    try:
        with open(sub_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except UnicodeDecodeError:
        return [f"файл не в кодировке UTF-8"], warnings, {}

    expected = set(load_ids(dataset_path))
    seen = {}
    n_boxes = n_empty = 0

    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        where = f"строка {i}"
        try:
            row = json.loads(line, object_pairs_hook=no_duplicate_keys)
        except ValueError as e:
            errors.append(f"{where}: невалидный JSON ({e})")
            continue
        if not isinstance(row, dict):
            errors.append(f"{where}: ожидается JSON-объект")
            continue

        extra = set(row) - ALLOWED_KEYS
        if extra:
            errors.append(f"{where}: лишние поля {sorted(extra)}")

        pid = row.get("photo_id")
        if not isinstance(pid, int) or isinstance(pid, bool):
            errors.append(f"{where}: photo_id должен быть целым числом, сейчас {pid!r}")
            continue
        where = f"строка {i} (photo_id {pid})"
        if pid in seen:
            errors.append(f"{where}: photo_id повторяется (уже был в строке {seen[pid]})")
        seen[pid] = i
        if pid not in expected:
            errors.append(f"{where}: такого photo_id нет в датасете")

        if row.get("error") is not None:
            errors.append(f"{where}: error = {row['error']!r}, перед сдачей ошибки нужно устранить")

        boxes = row.get("boxes", "нет поля")
        if not isinstance(boxes, list):
            errors.append(f"{where}: boxes должен быть списком, сейчас {boxes!r}")
            continue
        if not boxes:
            n_empty += 1
        for j, b in enumerate(boxes):
            bw = f"{where}, рамка {j}"
            if not isinstance(b, dict):
                errors.append(f"{bw}: ожидается объект")
                continue
            if set(b) != BOX_KEYS:
                errors.append(f"{bw}: нужны ровно поля left, top, width, height, сейчас {sorted(b)}")
                continue
            if not all(is_number(b[k]) for k in BOX_KEYS):
                errors.append(f"{bw}: координаты должны быть конечными числами: {b}")
                continue
            if not all(0 <= b[k] <= 1 for k in BOX_KEYS):
                errors.append(f"{bw}: координаты вне [0, 1]: {b}")
                continue
            if b["width"] <= 0 or b["height"] <= 0:
                errors.append(f"{bw}: ширина и высота должны быть положительными: {b}")
                continue
            if b["left"] + b["width"] > 1 + 1e-6 or b["top"] + b["height"] > 1 + 1e-6:
                warnings.append(f"{bw}: рамка выходит за край кадра: {b}")
            n_boxes += 1

    missing = expected - set(seen)
    if missing:
        errors.append(f"нет ответа для {len(missing)} фото, например: {sorted(missing)[:10]}")

    stats = {"строк с ответом": len(seen), "рамок": n_boxes, "фото без рамок": n_empty}
    return errors, warnings, stats


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    errors, warnings, stats = check(sys.argv[1], sys.argv[2])
    for w in warnings:
        print("ПРЕДУПРЕЖДЕНИЕ:", w)
    for e in errors[:50]:
        print("ОШИБКА:", e)
    if len(errors) > 50:
        print(f"... и ещё {len(errors) - 50} ошибок")
    if stats:
        print("Итого:", ", ".join(f"{k}: {v}" for k, v in stats.items()))
    if errors:
        print(f"\nНЕ ОТПРАВЛЯТЬ: {len(errors)} ошибок")
        sys.exit(1)
    print("\nOK: файл соответствует формату Контеста")


if __name__ == "__main__":
    main()
