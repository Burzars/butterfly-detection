"""Общая часть подготовки и сборки golden set. Только stdlib, сеть не нужна.

Источники — выгрузки API Яндекс Заданий (как есть, по одному заданию на строку) и датасет:
  ответы bbox      — labeling/bbox/raw/pool_6870107_main.jsonl (основной пул разметки);
  вердикты review  — labeling/review/raw/pool_6870574_main.jsonl (основной пул проверки);
  блокировки       — labeling/review/raw/user_restrictions.jsonl (ревьюеры, отстранённые правилом контроля);
  датасет          — data/sources_and_labels.jsonl (предварительная метка и подсчёт).

Правило, какие фото обязательно разобрать вручную (must_review), задано здесь
одно для страницы разбора и для сборки, чтобы они не разошлись.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # корень репозитория
DEFAULTS = {
    "bbox": ROOT / "labeling/bbox/raw/pool_6870107_main.jsonl",
    "review": ROOT / "labeling/review/raw/pool_6870574_main.jsonl",
    "restrictions": ROOT / "labeling/review/raw/user_restrictions.jsonl",
    "dataset": ROOT / "data/sources_and_labels.jsonl",
}
BOX_KEYS = ("left", "top", "width", "height")
EPS = 1e-6

# Почему фото попало в обязательный разбор. Порядок = порядок в очереди.
FLAGS = {
    "mismatch": "ответ разметчика расходится с предварительной меткой датасета",
    "both_reject": "оба ревьюера: «неверно»",
    "split": "ревьюеры разошлись (1:1)",
    "blocked_only": "проверили только ревьюеры, заблокированные правилом контроля",
}


class DataError(ValueError):
    pass


def read_items(path):
    """JSONL (по объекту на строку), JSON-массив или API-страница {items, has_more:false}."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict) and "items" not in data:
        data = [data]                                     # JSONL из одной строки
    if isinstance(data, dict):
        if data.get("has_more"):
            raise DataError(f"{path}: выгрузка неполная (has_more=true)")
        data = data.get("items")
    if not isinstance(data, list):
        raise DataError(f"{path}: ожидался список")
    return data


def clean_box(b, where):
    """{"shape":..., left, top, width, height} → {left, top, width, height} без изменения чисел."""
    if not isinstance(b, dict) or any(k not in b for k in BOX_KEYS):
        raise DataError(f"{where}: рамка без left/top/width/height: {b!r}")
    box = {k: b[k] for k in BOX_KEYS}
    check_box(box, where)
    return box


def check_box(box, where):
    vals = [box.get(k) for k in BOX_KEYS]
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v != v for v in vals):
        raise DataError(f"{where}: координаты должны быть числами: {box!r}")
    left, top, width, height = vals
    if width <= 0 or height <= 0:
        raise DataError(f"{where}: ширина и высота должны быть больше нуля: {box!r}")
    if left < -EPS or top < -EPS or left + width > 1 + EPS or top + height > 1 + EPS:
        raise DataError(f"{where}: рамка выходит за кадр (нужны доли 0..1, не пиксели): {box!r}")


def load_dataset(path):
    rows = {}
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        r = json.loads(line)
        pid = str(r["photo_id"])
        if pid in rows:
            raise DataError(f"{path}:{n}: photo_id {pid} повторяется")
        rows[pid] = r
    return rows


def prelim_count(row):
    """Предварительное число бабочек: 0 для «не бабочка», число из review_count, иначе None."""
    if row.get("preliminary_label") == "не бабочка":
        return 0
    m = re.search(r"\d+", row.get("review_count") or "")
    return int(m.group()) if m else None


def collect(bbox=None, review=None, restrictions=None, dataset=None):
    """→ (records, dataset_rows). records: photo_id → сведения о разметке, вердиктах и флагах."""
    bbox = bbox or DEFAULTS["bbox"]
    review = review or DEFAULTS["review"]
    restrictions = restrictions or DEFAULTS["restrictions"]
    dataset = dataset or DEFAULTS["dataset"]
    ds = load_dataset(dataset)

    records = {}
    for a in read_items(bbox):
        if a.get("status") != "ACCEPTED":
            continue
        for t, s in zip(a["tasks"], a["solutions"]):
            if t.get("known_solutions"):
                continue                                  # контрольные задания пула
            pid = str(t["input_values"]["photo_id"])
            if pid in records:
                raise DataError(f"фото {pid}: два принятых ответа разметчиков")
            out = s["output_values"]
            regions = [clean_box(b, f"разметка {pid}") for b in (out.get("regions") or [])]
            status = out.get("status")
            if (status == "objects") != bool(regions):
                raise DataError(f"фото {pid}: статус {status!r} не согласован с числом рамок {len(regions)}")
            records[pid] = {
                "photo_id": pid,
                "labeler": {"status": status, "regions": regions,
                            "worker": a["user_id"], "assignment": a["id"]},
                "votes": [],
            }
    missing = sorted(set(ds) - set(records))
    extra = sorted(set(records) - set(ds))
    if missing or extra:
        raise DataError(f"разметка не совпадает с датасетом: нет ответа для {missing[:5]}, лишние {extra[:5]}")

    blocked = {r["user_id"] for r in read_items(restrictions)}
    for a in read_items(review):
        if a.get("status") != "ACCEPTED":
            continue
        for t, s in zip(a["tasks"], a["solutions"]):
            if t.get("known_solutions"):
                continue
            pid = str(t["input_values"]["photo_id"])
            rec = records.get(pid)
            if rec is None:
                raise DataError(f"вердикт для фото {pid}, которого нет в разметке")
            shown = [clean_box(b, f"review {pid}") for b in (t["input_values"].get("regions") or [])]
            if shown != rec["labeler"]["regions"]:
                raise DataError(f"фото {pid}: ревьюеру показаны не те рамки, что дал разметчик")
            if a["user_id"] == rec["labeler"]["worker"]:
                raise DataError(f"фото {pid}: автор разметки проверял сам себя")
            rec["votes"].append({"reviewer": a["user_id"], "verdict": s["output_values"].get("status"),
                                 "blocked": a["user_id"] in blocked})

    for pid, rec in records.items():
        row = ds[pid]
        verdicts = [v["verdict"] for v in rec["votes"]]
        rejects = verdicts.count("reject")
        exp = prelim_count(row)
        flags = []
        if exp is not None and exp != len(rec["labeler"]["regions"]):
            flags.append("mismatch")
        if rejects >= 2:
            flags.append("both_reject")
        elif rejects == 1:
            flags.append("split")
        if rec["votes"] and all(v["blocked"] for v in rec["votes"]):
            flags.append("blocked_only")
        if not rec["votes"]:
            flags.append("blocked_only")                  # без вердиктов — тоже к разбору
        rec["flags"] = [f for f in FLAGS if f in flags]
        rec["must_review"] = bool(flags)
        rec["prelim"] = {"label": row.get("preliminary_label"), "count": exp}
    return records, ds
