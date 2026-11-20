"""Метрики детекции бабочек — так же, как считает Контест.

Внутри каждого фото ищется максимальное число пар «эталонная рамка — предсказанная
рамка» с IoU не ниже порога; каждая рамка входит не больше чем в одну пару
(паросочетание максимальной мощности, а не жадный выбор по наибольшему IoU).
TP — число пар, FP — лишние предсказанные рамки, FN — пропущенные эталонные.
TP, FP и FN суммируются по всему набору, затем F1 = 2·TP / (2·TP + FP + FN).
Правильный пустой ответ TP не добавляет; любая рамка на фото без бабочек — FP.

Считает F1@0.5 (основная), precision, recall, F1@0.75, ложные срабатывания на фото
без бабочек, ошибки обработки и время на фото — на всём наборе и по группам.

Использование:
    # одна модель: итог и таблица по группам (группы берутся из data/sources_and_labels.jsonl)
    python eval/evaluate.py golden/golden.jsonl predictions/gdino_tiny__butterfly.jsonl

    # другой порог уверенности и удаление дублей (NMS) — без перезапуска модели
    python eval/evaluate.py golden/golden.jsonl predictions/owlv2_base__butterfly.jsonl --score-thr 0.3 --nms 0.8

    # перебор порога уверенности 0.05…0.95 — для выбора порога
    python eval/evaluate.py golden/golden.jsonl predictions/gdino_tiny__butterfly.jsonl --sweep

    # несколько файлов, у каждого своя постобработка после @, и лидерборд в markdown;
    # один и тот же файл можно указать дважды — так сравниваются варианты эксперимента
    python eval/evaluate.py golden/golden.jsonl predictions/gdino_tiny__butterfly.jsonl@score=0.35 predictions/owlv2_base__butterfly.jsonl@score=0.3,nms=0.8 predictions/qwen3vl_2b__default.jsonl --leaderboard eval/leaderboard.md

    # проверка самого скрипта: эталон против себя даёт F1 = 1
    python eval/evaluate.py golden/golden.jsonl golden/golden.jsonl

Форматы (подробнее — golden/README.md и predictions/README.md):
    эталон:       {"photo_id": 1, "boxes": [{"left": 0.1, "top": 0.2, "width": 0.3, "height": 0.4}]}
    предсказания: {"photo_id": 1, "boxes": [{..., "score": 0.8}], "error": null, "time_sec": 0.4}
Строка с "error" или с "boxes": null — ошибка обработки: рамок у фото нет, его эталонные
рамки идут в FN, а сама ошибка считается в столбце «Ошибок».
Рамки обрезаются по краям кадра — так же, как при сборке посылки в Контест.
Нужен только Python 3.9+, сторонние библиотеки не используются.
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

IOU_MAIN = 0.5
IOU_STRICT = 0.75
EPS = 1e-9  # чтобы IoU ровно 0.5 из-за округления не стал 0.4999999 и пара не потерялась
BOX_KEYS = ("left", "top", "width", "height")
SWEEP_THRESHOLDS = [round(0.05 * i, 2) for i in range(1, 20)]
DEFAULT_DATASET = os.path.join("data", "sources_and_labels.jsonl")

GROUP_NAMES = {
    "butterflies": "Дневные бабочки",
    "moths": "Мотыльки и моль",
    "beetles": "Жуки",
    "bees_wasps_ants": "Пчёлы, осы, муравьи",
    "flies": "Мухи и комары",
    "caterpillars": "Гусеницы",
    "plants": "Растения",
}


class FormatError(ValueError):
    """Файл не в нашем формате; текст ошибки объясняет, что исправить."""


# ---------- чтение файлов ----------

def load_jsonl(path):
    """jsonl → {photo_id: строка}. Повтор photo_id или битая строка — ошибка."""
    rows = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            where = f"{path}, строка {n}"
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise FormatError(f"{where}: невалидный JSON ({e})") from None
            if not isinstance(row, dict) or "photo_id" not in row:
                raise FormatError(f"{where}: нет поля photo_id")
            try:
                pid = int(row["photo_id"])
            except (TypeError, ValueError):
                raise FormatError(f"{where}: photo_id должен быть целым числом, сейчас {row['photo_id']!r}") from None
            if pid in rows:
                raise FormatError(f"{where}: photo_id {pid} повторяется")
            rows[pid] = row
    return rows


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def parse_box(b, where):
    """Рамка {left, top, width, height[, score]} в долях кадра → ((x0, y0, x1, y1, score), вышла_за_кадр).

    Рамка обрезается по краям кадра; если от неё ничего не осталось, вместо неё None.
    «Вышла за кадр» — правый или нижний край дальше 1.05: частая причина — записали
    x2, y2 вместо width, height.
    """
    if not isinstance(b, dict) or not all(k in b for k in BOX_KEYS):
        raise FormatError(f"{where}: нужна рамка с полями left, top, width, height, сейчас {b!r}")
    left, top, width, height = (b[k] for k in BOX_KEYS)
    if not all(is_number(v) for v in (left, top, width, height)):
        raise FormatError(f"{where}: координаты должны быть конечными числами: {b!r}")
    if any(v < -0.05 or v > 1.05 for v in (left, top, width, height)):
        raise FormatError(f"{where}: числа далеко за пределами [0, 1] — похоже на пиксели, а нужны доли кадра: {b!r}")
    if width < 0 or height < 0:
        raise FormatError(f"{where}: отрицательная ширина или высота: {b!r}")
    score = b.get("score")
    score = float(score) if is_number(score) else None
    outside = left + width > 1.05 or top + height > 1.05
    x0, y0 = max(0.0, left), max(0.0, top)
    x1, y1 = min(1.0, left + width), min(1.0, top + height)
    if x1 <= x0 or y1 <= y0:
        return None, outside
    return (x0, y0, x1, y1, score), outside


# ---------- геометрия ----------

def iou(a, b):
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def count_matches(gt, pred, thr):
    """Максимальное число пар «эталон — предсказание» с IoU ≥ thr (алгоритм Куна).

    Жадный выбор по наибольшему IoU может дать меньше пар, поэтому ищем именно
    максимальное паросочетание — как написано в условии Контеста.
    """
    adj = [[j for j, p in enumerate(pred) if iou(g, p) >= thr - EPS] for g in gt]
    owner = [-1] * len(pred)  # owner[j] — эталонная рамка, с которой сейчас в паре предсказание j

    def augment(i, seen):
        for j in adj[i]:
            if j in seen:
                continue
            seen.add(j)
            if owner[j] == -1 or augment(owner[j], seen):
                owner[j] = i
                return True
        return False

    return sum(augment(i, set()) for i in range(len(gt)))


def nms(boxes, iou_thr):
    """Удаление дублей: из рамок, перекрывающихся с IoU > iou_thr, остаётся рамка с большим score."""
    kept = []
    for b in sorted(boxes, key=lambda b: -(b[4] if b[4] is not None else 1.0)):
        if all(iou(b, k) <= iou_thr for k in kept):
            kept.append(b)
    return kept


def postprocess(boxes, score_thr=None, nms_iou=None):
    """Порог уверенности, затем NMS. Тем же кодом надо собирать посылку в Контест.

    Рамки без score порог проходят (у Qwen score нет).
    """
    if score_thr is not None:
        boxes = [b for b in boxes if b[4] is None or b[4] >= score_thr]
    if nms_iou is not None:
        boxes = nms(boxes, nms_iou)
    return boxes


# ---------- подсчёт ----------

def parse(golden, preds, name):
    """Готовит фото к подсчёту: эталонные рамки, сырые предсказания, ошибка, время.

    Считаются только фото из эталона. Возвращает (список фото, счётчики замечаний).
    """
    photos = []
    notes = defaultdict(int)
    notes["extra"] = len(set(preds) - set(golden))
    for pid, g in golden.items():
        gboxes = g.get("boxes")
        if not isinstance(gboxes, list):
            raise FormatError(f"эталон, photo_id {pid}: boxes должен быть списком — у каждого фото нужен итоговый ответ, сейчас {gboxes!r}")
        gt = []
        for k, b in enumerate(gboxes):
            box, outside = parse_box(b, f"эталон, photo_id {pid}, рамка {k}")
            notes["gt_outside"] += outside
            if box is not None:
                gt.append(box)

        row = preds.get(pid)
        error, pred, time_sec = None, [], None
        if row is None:
            error = "нет строки в файле предсказаний"
            notes["missing"] += 1
        else:
            time_sec = row.get("time_sec") if is_number(row.get("time_sec")) else None
            if row.get("error") is not None or row.get("boxes") is None:
                error = str(row.get("error") or "boxes = null без описания ошибки")
            elif not isinstance(row["boxes"], list):
                raise FormatError(f"{name}, photo_id {pid}: boxes должен быть списком или null, сейчас {row['boxes']!r}")
            else:
                for k, b in enumerate(row["boxes"]):
                    box, outside = parse_box(b, f"{name}, photo_id {pid}, рамка {k}")
                    notes["outside"] += outside
                    if box is None:
                        notes["degenerate"] += 1
                        continue
                    if box[4] is None:
                        notes["no_score"] += 1
                    pred.append(box)
        photos.append({"photo_id": pid, "gt": gt, "raw": pred, "error": error, "time_sec": time_sec})
    return photos, notes


def score_photos(photos, score_thr=None, nms_iou=None):
    """Постобработка и сопоставление рамок; одна запись на фото."""
    recs = []
    for p in photos:
        pred = postprocess(p["raw"], score_thr, nms_iou)
        recs.append({
            "photo_id": p["photo_id"],
            "n_gt": len(p["gt"]),
            "n_pred": len(pred),
            "tp": count_matches(p["gt"], pred, IOU_MAIN),
            "tp75": count_matches(p["gt"], pred, IOU_STRICT),
            "error": p["error"],
            "time_sec": p["time_sec"],
        })
    return recs


def ratio(a, b):
    return a / b if b else None


def summarize(recs):
    """Суммирует TP, FP, FN по фото и считает метрики. None — метрика не определена (деление на 0)."""
    gt = sum(r["n_gt"] for r in recs)
    pred = sum(r["n_pred"] for r in recs)
    tp = sum(r["tp"] for r in recs)
    tp75 = sum(r["tp75"] for r in recs)
    fp, fn = pred - tp, gt - tp
    fp75, fn75 = pred - tp75, gt - tp75
    empty = [r for r in recs if r["n_gt"] == 0]
    times = [r["time_sec"] for r in recs if r["time_sec"] is not None]
    return {
        "photos": len(recs),
        "photos_empty": len(empty),
        "gt": gt,
        "pred": pred,
        "tp": tp, "fp": fp, "fn": fn,
        "precision": ratio(tp, pred),
        "recall": ratio(tp, gt),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
        "tp75": tp75, "fp75": fp75, "fn75": fn75,
        "f1_75": ratio(2 * tp75, 2 * tp75 + fp75 + fn75),
        "fp_empty_boxes": sum(r["n_pred"] for r in empty),
        "fp_empty_photos": sum(1 for r in empty if r["n_pred"] > 0),
        "photos_with_fp": sum(1 for r in recs if r["n_pred"] > r["tp"]),
        "photos_with_fn": sum(1 for r in recs if r["n_gt"] > r["tp"]),
        "errors": sum(1 for r in recs if r["error"]),
        "time_mean": ratio(sum(times), len(times)),
        "time_count": len(times),
    }


# ---------- вывод ----------

def fmt(x, digits=3):
    return "—" if x is None else f"{x:.{digits}f}"


def md_table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def describe_post(score_thr, nms_iou):
    parts = []
    if score_thr is not None:
        parts.append(f"score ≥ {score_thr:g}")
    if nms_iou is not None:
        parts.append(f"NMS {nms_iou:g}")
    return ", ".join(parts)


def print_summary(title, s, notes, score_thr):
    print(f"=== {title}")
    if notes["missing"]:
        print(f"ВНИМАНИЕ: нет строки для {notes['missing']} фото из эталона — они посчитаны как ошибки обработки")
    if notes["extra"]:
        print(f"ВНИМАНИЕ: строк с photo_id, которых нет в эталоне: {notes['extra']} — не учитываются")
    if notes["outside"]:
        print(f"ВНИМАНИЕ: рамок, заметно выходивших за кадр: {notes['outside']} — обрезаны. Проверьте, не записаны ли x2, y2 вместо width, height")
    if notes["gt_outside"]:
        print(f"ВНИМАНИЕ: в эталоне рамок, заметно выходящих за кадр: {notes['gt_outside']} — проверьте конвертацию разметки")
    if notes["degenerate"]:
        print(f"ВНИМАНИЕ: рамок нулевой площади после обрезки по кадру: {notes['degenerate']} — отброшены")
    if score_thr is not None and notes["no_score"]:
        print(f"ВНИМАНИЕ: рамок без score: {notes['no_score']} — порог уверенности к ним не применялся")
    print(f"Фото: {s['photos']} (с бабочками {s['photos'] - s['photos_empty']}, без бабочек {s['photos_empty']}). "
          f"Рамок: в эталоне {s['gt']}, предсказано {s['pred']}.")
    print(f"F1@0.5  = {fmt(s['f1'])}   precision {fmt(s['precision'])}   recall {fmt(s['recall'])}   "
          f"(TP {s['tp']}, FP {s['fp']}, FN {s['fn']})")
    print(f"F1@0.75 = {fmt(s['f1_75'])}   (TP {s['tp75']}, FP {s['fp75']}, FN {s['fn75']})")
    print(f"Ложные срабатывания на фото без бабочек: рамок {s['fp_empty_boxes']}, на {s['fp_empty_photos']} фото из {s['photos_empty']}.")
    print(f"Фото с пропусками (есть FN): {s['photos_with_fn']}, с лишними рамками (есть FP): {s['photos_with_fp']}.")
    if s["time_mean"] is None:
        timing = "нет данных (поле time_sec)"
    else:
        timing = f"{fmt(s['time_mean'])} с/фото (известно для {s['time_count']} фото)"
    print(f"Ошибок обработки: {s['errors']}. Время: {timing}.")


def group_values(row, field):
    """Значения поля датасета для фото; у поля-списка (например tags) — каждое значение."""
    if row is None:
        return ["(нет в датасете)"]
    v = row.get(field)
    if v is None or v == []:
        return ["(не указано)"]
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def group_table(recs, dataset, field):
    groups = defaultdict(list)
    for r in recs:
        for g in group_values(dataset.get(r["photo_id"]), field):
            groups[g].append(r)
    order = [g for g in GROUP_NAMES if g in groups] if field == "source_group" else []
    order += sorted((g for g in groups if g not in order), key=lambda g: (-len(groups[g]), g))
    rows = []
    for g in order:
        s = summarize(groups[g])
        name = GROUP_NAMES.get(g, g) if field == "source_group" else g
        no_target = s["gt"] == 0  # в группе нет бабочек: F1 не имеет смысла, смотрим на FP
        empty = f"{s['fp_empty_photos']} из {s['photos_empty']}" if s["photos_empty"] else "—"
        rows.append([name, s["photos"], s["gt"], s["pred"], s["tp"], s["fp"], s["fn"],
                     fmt(s["precision"]), fmt(s["recall"]),
                     "—" if no_target else fmt(s["f1"]), "—" if no_target else fmt(s["f1_75"]), empty])
    header = ["Группа", "Фото", "Рамок в эталоне", "Предсказано", "TP", "FP", "FN",
              "Precision", "Recall", "F1@0.5", "F1@0.75", "Фото без бабочек с FP"]
    return md_table(header, rows)


def print_sweep(photos, nms_iou):
    scores = [b[4] for p in photos for b in p["raw"] if b[4] is not None]
    if not scores:
        print("Перебор порога: в файле нет score, перебирать нечего.")
        return
    title = "Перебор порога уверенности" + (f" (NMS {nms_iou:g})" if nms_iou is not None else "")
    print(f"{title}. Минимальный score в файле: {min(scores):.3f} — пороги ниже него ничего не меняют.")
    rows, best = [], None
    for thr in [None] + SWEEP_THRESHOLDS:
        s = summarize(score_photos(photos, thr, nms_iou))
        rows.append(["без порога" if thr is None else f"{thr:.2f}", s["pred"], s["tp"], s["fp"], s["fn"],
                     fmt(s["precision"]), fmt(s["recall"]), fmt(s["f1"]), fmt(s["f1_75"]),
                     f"{s['fp_empty_boxes']} ({s['fp_empty_photos']} фото)"])
        if s["f1"] is not None and (best is None or s["f1"] > best[1] + 1e-12):
            best = (thr, s["f1"])
    print(md_table(["Порог", "Рамок", "TP", "FP", "FN", "Precision", "Recall", "F1@0.5", "F1@0.75",
                    "FP на фото без бабочек"], rows))
    if best:
        where = "без порога" if best[0] is None else f"при пороге {best[0]:.2f}"
        print(f"Лучший F1@0.5 = {best[1]:.3f} {where} (при равенстве показан меньший порог).")


def leaderboard_md(results, golden_path, s0):
    rows = []
    key = lambda r: (-(r["s"]["f1"] or 0.0), r["s"]["time_mean"] if r["s"]["time_mean"] is not None else math.inf)
    for r in sorted(results, key=key):
        s = r["s"]
        rows.append([r["model"], r["config"] or "—", fmt(s["f1"]), fmt(s["precision"]), fmt(s["recall"]),
                     fmt(s["f1_75"]), f"{s['fp_empty_boxes']} ({s['fp_empty_photos']} фото)", s["errors"],
                     fmt(s["time_mean"], 2)])
    header = ["Модель", "Конфигурация", "F1@0.5", "Precision", "Recall", "F1@0.75",
              "FP на фото без бабочек", "Ошибок", "Время, с/фото"]
    intro = (f"Эталон: `{golden_path}`, {s0['photos']} фото ({s0['photos'] - s0['photos_empty']} с бабочками, "
             f"{s0['photos_empty']} без), рамок в эталоне: {s0['gt']}.\n"
             "F1 считается как в Контесте (максимальное паросочетание рамок с IoU ≥ порога, TP/FP/FN суммируются по набору). "
             "Строки отсортированы по F1@0.5, при равенстве — по времени.\n"
             "«FP на фото без бабочек» — лишние рамки на фото с пустым эталоном (в скобках — на скольких фото).\n")
    return "# Лидерборд\n\n" + intro + "\n" + md_table(header, rows) + "\n"


# ---------- запуск ----------

def parse_item(item, default_thr, default_nms):
    """«файл.jsonl@score=0.35,nms=0.8» → (путь, порог, nms). Без @ — значения из --score-thr и --nms."""
    path, sep, spec = item.rpartition("@") if "@" in item else (item, "", "")
    thr, nms_iou = default_thr, default_nms
    for part in filter(None, spec.split(",")) if sep else []:
        key, _, val = part.partition("=")
        val = None if val.strip().lower() in ("", "none", "off") else val
        try:
            if key.strip() == "score":
                thr = None if val is None else float(val)
            elif key.strip() == "nms":
                nms_iou = None if val is None else float(val)
            else:
                raise ValueError
        except ValueError:
            raise FormatError(f"{item}: после @ ожидается score=<число> и/или nms=<число>, например файл.jsonl@score=0.3,nms=0.8") from None
    if thr is not None and not 0 <= thr <= 1:
        raise FormatError(f"{item}: порог score должен быть от 0 до 1")
    if nms_iou is not None and not 0 < nms_iou <= 1:
        raise FormatError(f"{item}: порог NMS должен быть больше 0 и не больше 1")
    return path, thr, nms_iou


def slug(stem, thr, nms_iou):
    parts = [stem] + ([f"score{thr:g}"] if thr is not None else []) + ([f"nms{nms_iou:g}"] if nms_iou is not None else [])
    return "_".join(parts)


def run(args):
    golden = load_jsonl(args.golden)
    dataset_path = args.dataset or (DEFAULT_DATASET if os.path.exists(DEFAULT_DATASET) else None)
    dataset = load_jsonl(dataset_path) if dataset_path else {}
    if dataset:
        lacking = set(dataset) - set(golden)
        if lacking:
            print(f"ВНИМАНИЕ: в эталоне нет {len(lacking)} фото из {dataset_path}, например {sorted(lacking)[:5]}. "
                  "Golden set должен покрывать весь датасет.\n")

    results = []
    for item in args.predictions:
        path, thr, nms_iou = parse_item(item, args.score_thr, args.nms)
        name = os.path.basename(path)
        stem = name[:-len(".jsonl")] if name.endswith(".jsonl") else name
        photos, notes = parse(golden, load_jsonl(path), name)
        recs = score_photos(photos, thr, nms_iou)
        s = summarize(recs)
        post = describe_post(thr, nms_iou)
        print_summary(name + (f" ({post})" if post else ""), s, notes, thr)

        for field in args.by if dataset else []:
            print(f"\nПо группам ({field} из {dataset_path}):")
            if field != "source_group":
                print("Одно фото может входить в несколько групп, поэтому суммы по строкам не равны итогу.")
            print(group_table(recs, dataset, field))
        if args.sweep:
            print()
            print_sweep(photos, nms_iou)
        if args.per_photo_dir:
            os.makedirs(args.per_photo_dir, exist_ok=True)
            out = os.path.join(args.per_photo_dir, slug(stem, thr, nms_iou) + ".per_photo.jsonl")
            with open(out, "w", encoding="utf-8") as f:
                for r in recs:
                    row = dict(r, fp=r["n_pred"] - r["tp"], fn=r["n_gt"] - r["tp"],
                               group=(dataset.get(r["photo_id"]) or {}).get("source_group"))
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"По фото: {out}")
        model, _, config = stem.partition("__")
        results.append({"model": model, "config": ", ".join(x for x in (config, post) if x), "s": s})
        print()

    if args.leaderboard:
        s0 = summarize(score_photos(parse(golden, {}, "")[0]))  # размеры эталона: фото и рамки
        with open(args.leaderboard, "w", encoding="utf-8") as f:
            f.write(leaderboard_md(results, args.golden, s0))
        print(f"Лидерборд записан: {args.leaderboard}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("golden", help="эталон, golden/golden.jsonl")
    p.add_argument("predictions", nargs="+", help="файлы предсказаний; после @ можно задать постобработку: файл.jsonl@score=0.3,nms=0.8")
    p.add_argument("--dataset", help=f"датасет с группами для таблиц (по умолчанию {DEFAULT_DATASET}, если файл есть)")
    p.add_argument("--by", nargs="+", default=["source_group"], help="поля датасета для таблиц по группам, например: source_group tags")
    p.add_argument("--score-thr", type=float, help="порог уверенности для файлов без своего @score=")
    p.add_argument("--nms", type=float, metavar="IOU", help="удалять дубли с IoU больше этого значения, для файлов без своего @nms=")
    p.add_argument("--sweep", action="store_true", help="перебрать порог уверенности 0.05…0.95")
    p.add_argument("--leaderboard", metavar="FILE.md", help="записать лидерборд в markdown")
    p.add_argument("--per-photo-dir", metavar="DIR", help="сохранить TP/FP/FN по каждому фото — искать примеры ошибок")
    args = p.parse_args()
    try:
        run(args)
    except (FormatError, OSError) as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
