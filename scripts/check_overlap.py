"""Проверка, что наш датасет не пересекается с экзаменационным.

Использование:
    python scripts/check_overlap.py data/sources_and_labels.jsonl data/exam/dataset.jsonl

Сравнивает photo_id. Код выхода 1, если пересечение есть.
"""
import json
import sys


def load(path):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                rows[row["photo_id"]] = row
    return rows


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    ours, exam = load(sys.argv[1]), load(sys.argv[2])
    common = sorted(set(ours) & set(exam))
    print(f"Наш датасет: {len(ours)} фото, экзаменационный: {len(exam)} фото")
    if not common:
        print("OK: пересечений нет")
        return
    print(f"ПЕРЕСЕЧЕНИЕ: {len(common)} фото есть в обоих наборах, их лучше заменить:")
    for pid in common:
        print(" ", pid, ours[pid].get("photo_url", ""))
    sys.exit(1)


if __name__ == "__main__":
    main()
