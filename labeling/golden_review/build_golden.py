"""Собирает golden set из ответов разметчиков, вердиктов ревью и решений команды.

    python3 labeling/golden_review/build_golden.py \
        --decisions labeling/golden_review/decisions/misha.json labeling/golden_review/decisions/nastya.json \
        --override labeling/golden_review/decisions/arsenii.json

--decisions — файлы решений команды; разные решения по одному фото в них — ошибка.
--override  — решения ответственного за итоговую разметку: они главнее и снимают противоречия;
              заменённые решения сохраняются в golden_audit.jsonl (поле superseded) и в corrections.md.

Правило:
  - фото с решением команды: «оставить» → рамки разметчика; «исправлено» → рамки из решения;
    «бабочек нет» → пустой список; «не уверен» → не разобрано;
  - фото без решения: если оно в обязательном разборе (golden_common.FLAGS) — ошибка,
    иначе (оба ревьюера «верно», расхождений с датасетом нет) — рамки разметчика.

Пишет в --out-dir (по умолчанию golden/):
  golden.jsonl        — эталон для eval/evaluate.py: {"photo_id", "boxes": [{left, top, width, height}]};
  golden_audit.jsonl  — по каждому фото: исходная разметка, вердикты, источник решения, итог;
  corrections.jsonl   — исправления: {"photo_id", "before", "after", "reason", "by", ...};
  corrections.md      — те же исправления таблицей (для отчёта).
--labeler-predictions ПУТЬ — дополнительно записать ответы разметчиков в формате предсказаний
  (сверка через evaluate.py: чем исходная разметка отличается от golden).

Если что-то не разобрано или решение некорректно — ничего не пишет и завершается с кодом 1
(с --draft пишет golden_draft.jsonl в viz/golden_draft/, чтобы посмотреть промежуточный результат).
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import golden_common as gc

KINDS = {"keep": "оставить как есть", "fix": "исправлено", "empty": "бабочек нет", "unsure": "не уверен"}


def read_decision_files(paths):
    """→ (решения, противоречия). Противоречие — разные решения по одному фото в разных файлах."""
    merged, seen, conflicts = {}, {}, {}
    for path in paths or []:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("schema") != "golden_decisions_v1" or not isinstance(d.get("decisions"), dict):
            raise gc.DataError(f"{path}: это не файл решений со страницы разбора (schema golden_decisions_v1)")
        for k, v in d["decisions"].items():
            k = str(k)
            seen.setdefault(k, []).append({"file": Path(path).name, **v})
            if k in merged and merged[k] != v:
                conflicts[k] = seen[k]
            merged[k] = v
    return merged, seen, conflicts


def load_decisions(paths, overrides=None):
    """Решения команды + решения ответственного (overrides), которые главнее.

    → (решения, заменённые). Противоречие между обычными файлами допустимо, только если
    это фото решено в overrides; противоречие внутри overrides — ошибка.
    """
    base, seen, conflicts = read_decision_files(paths)
    over, _, over_conflicts = read_decision_files(overrides)
    for k in over_conflicts:
        raise gc.DataError(f"фото {k}: разные решения в файлах --override ({', '.join(x['file'] for x in over_conflicts[k])})")
    for k in conflicts:
        if k not in over:
            names = ", ".join(x["file"] for x in conflicts[k])
            raise gc.DataError(f"фото {k}: разные решения в {names} — договоритесь или решите через --override")
    def differs(x, k):
        return {kk: vv for kk, vv in x.items() if kk != "file"} != over[k]
    superseded = {k: [x for x in seen[k] if differs(x, k)] for k in over if k in seen}
    superseded = {k: v for k, v in superseded.items() if v}
    base.update(over)
    return base, superseded


def build(records, ds, decisions, superseded=None):
    """→ (golden, audit, errors). golden/audit — в порядке датасета.

    superseded: photo_id → решения из обычных файлов, заменённые решением ответственного (--override).
    """
    superseded = superseded or {}
    errors, golden, audit = [], [], []
    for pid in sorted(set(decisions) - set(ds)):
        errors.append(f"{pid}: решение для фото, которого нет в датасете")
    for pid in ds:
        rec = records[pid]
        lab = rec["labeler"]["regions"]
        d = decisions.get(pid)
        why = ", ".join(gc.FLAGS[f] for f in rec["flags"])
        final, source, comment, by = lab, None, "", ""
        if d is None:
            if rec["must_review"]:
                errors.append(f"{pid}: не разобрано ({why})")
                source = "unresolved"
            else:
                source = "auto_accept"
        else:
            kind, comment, by = d.get("decision"), (d.get("comment") or "").strip(), (d.get("decided_by") or "").strip()
            if not by:
                errors.append(f"{pid}: не указано, кто принял решение")
            if kind == "keep":
                source = "team_keep"
            elif kind == "fix":
                source = "team_fix"
                raw = d.get("boxes")
                if not isinstance(raw, list) or not raw:
                    errors.append(f"{pid}: «исправлено», но рамок нет (для пустого фото нужно «бабочек нет»)")
                    source = "unresolved"
                else:
                    try:
                        final = [gc.clean_box(b, f"решение {pid}, рамка {i + 1}") for i, b in enumerate(raw)]
                    except gc.DataError as e:
                        errors.append(str(e))
                        source = "unresolved"
                    else:
                        if final == lab:
                            errors.append(f"{pid}: «исправлено», но рамки совпадают с разметчиком — это «оставить»")
                            source = "unresolved"
                if not comment:
                    errors.append(f"{pid}: исправление без пояснения")
            elif kind == "empty":
                source, final = "team_empty", []
                if lab and not comment:
                    errors.append(f"{pid}: рамки разметчика убраны без пояснения")
            elif kind == "unsure":
                source = "unresolved"
                errors.append(f"{pid}: решение «не уверен» — нужно договориться")
            else:
                source = "unresolved"
                errors.append(f"{pid}: неизвестное решение {kind!r}")
        golden.append({"photo_id": int(pid), "boxes": final})
        audit.append({
            "photo_id": int(pid),
            "labeler": {"status": rec["labeler"]["status"], "boxes": lab,
                        "worker_id": rec["labeler"]["worker"], "assignment_id": rec["labeler"]["assignment"]},
            "review_votes": [{"reviewer_id": v["reviewer"], "verdict": v["verdict"], "blocked_by_quality_control": v["blocked"]}
                             for v in rec["votes"]],
            "preliminary": rec["prelim"],
            "review_flags": rec["flags"],
            "source": source,
            "final_boxes": final,
            "changed": final != lab,
            "comment": comment,
            "decided_by": by,
            "decided_at": (d or {}).get("decided_at", ""),
            "superseded": [{k: x.get(k) for k in ("file", "decision", "decided_by", "comment", "boxes") if k in x}
                           for x in superseded.get(pid, [])],
        })
    self_check(golden, audit, ds, errors)
    return golden, audit, errors


def self_check(golden, audit, ds, errors):
    """Независимые проверки результата, а не того, как он собран."""
    ids = [str(g["photo_id"]) for g in golden]
    if ids != list(ds):
        errors.append("состав golden не совпадает с датасетом")
    for g in golden:
        for i, b in enumerate(g["boxes"]):
            try:
                gc.check_box(b, f"golden {g['photo_id']}, рамка {i + 1}")
            except gc.DataError as e:
                errors.append(str(e))
    for a in audit:
        must_change = a["source"] == "team_fix" or (a["source"] == "team_empty" and bool(a["labeler"]["boxes"]))
        if a["changed"] != must_change and a["source"] != "unresolved":
            errors.append(f"{a['photo_id']}: итог отличается от разметчика без решения «исправлено/нет» (или наоборот)")


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def corrections_rows(audit):
    """Строки corrections.jsonl: изменённые фото и подтверждения с пояснением."""
    rows = []
    for a in audit:
        if a["source"] not in ("team_fix", "team_empty", "team_keep") or (not a["changed"] and not a["comment"]):
            continue
        rows.append({
            "photo_id": a["photo_id"],
            "before": a["labeler"]["boxes"],
            "after": a["final_boxes"],
            "decision": a["source"].replace("team_", ""),
            "reason": a["comment"],
            "by": "человек",                     # рамки рисовала и решения принимала команда, не модель
            "decided_by": a["decided_by"],
            "review_votes": [v["verdict"] for v in a["review_votes"]],
            "review_flags": a["review_flags"],
            "superseded": a.get("superseded", []),
        })
    return rows


def corrections_md(audit):
    def lab(a):
        n = len(a["labeler"]["boxes"])
        return f"рамок {n}" if n else "«нет»"
    def votes(a):
        return " / ".join(("верно" if v["verdict"] == "accept" else "неверно") + ("*" if v["blocked_by_quality_control"] else "")
                          for v in a["review_votes"]) or "—"
    src = Counter(a["source"] for a in audit)
    removed = sum(1 for a in audit if a["source"] == "team_empty" and a["labeler"]["boxes"])
    lines = [
        "# Исправления golden set",
        "",
        f"Всего фото: {len(audit)}. Принято автоматически (оба ревьюера «верно», расхождений нет): {src['auto_accept']}. "
        f"Разобрано командой: исправлено рамок {src['team_fix']}, убраны рамки {removed} "
        f"(«бабочек нет»), подтверждено без изменений {src['team_keep'] + src['team_empty'] - removed} "
        f"(из них ответ «нет» — {src['team_empty'] - removed}). Изменено фото: {sum(a['changed'] for a in audit)}.",
        "",
        "`*` — ревьюер позже заблокирован правилом контроля качества.",
        "",
        "| Фото | Разметчик | Ревью | Почему разбирали | Решение | Итог | Пояснение | Кто |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in audit:
        if a["source"] not in ("team_fix", "team_empty", "team_keep"):
            continue
        if not a["changed"] and not a["comment"]:
            continue                                   # подтверждения без комментария — только в golden_audit.jsonl
        flags = "; ".join(gc.FLAGS[f] for f in a["review_flags"]) or "—"
        res = f"рамок {len(a['final_boxes'])}" if a["final_boxes"] else "«нет»"
        name = {"team_fix": "исправлено", "team_empty": "бабочек нет", "team_keep": "оставлено"}[a["source"]]
        comment = a["comment"].replace("|", "/").replace("\n", " ")
        if a.get("superseded"):
            was = "; ".join(f"{x.get('decided_by') or x['file']}: {KINDS.get(x.get('decision'), x.get('decision'))}" for x in a["superseded"])
            comment += f" (заменено решение — {was})"
        lines.append(f"| {a['photo_id']} | {lab(a)} | {votes(a)} | {flags} | {name} | {res} | {comment} | {a['decided_by']} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k, v in gc.DEFAULTS.items():
        p.add_argument(f"--{k}", default=str(v))
    p.add_argument("--decisions", nargs="+", help="файл(ы) решений со страницы разбора (golden_decisions.json)")
    p.add_argument("--override", nargs="+", help="файл(ы) решений ответственного: главнее --decisions, снимают противоречия")
    p.add_argument("--out-dir", help="куда писать (по умолчанию golden/, с --draft — viz/golden_draft/)")
    p.add_argument("--labeler-predictions", help="записать ответы разметчиков в формате предсказаний в этот файл")
    p.add_argument("--draft", action="store_true", help="записать golden_draft.jsonl даже при ошибках")
    a = p.parse_args(argv)
    try:
        records, ds = gc.collect(a.bbox, a.review, a.restrictions, a.dataset)
        decisions, superseded = load_decisions(a.decisions, a.override)
    except (gc.DataError, OSError, json.JSONDecodeError) as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 2
    golden, audit, errors = build(records, ds, decisions, superseded)
    if superseded:
        print(f"Решением ответственного заменены решения по {len(superseded)} фото: {', '.join(sorted(superseded))}")

    src = Counter(x["source"] for x in audit)
    print(f"Фото: {len(golden)}. Источник итога: {dict(src)}")
    print(f"Изменено относительно разметчиков: {sum(x['changed'] for x in audit)} фото; "
          f"рамок в golden: {sum(len(g['boxes']) for g in golden)}; пустых фото: {sum(not g['boxes'] for g in golden)}")
    if errors:
        print(f"\nНе готово — {len(errors)} замечаний:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
    out = Path(a.out_dir or gc.ROOT / ("viz/golden_draft" if a.draft else "golden"))
    if errors and not a.draft:
        print("\nНичего не записано. Разберите замечания или запустите с --draft.", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    name = "golden_draft.jsonl" if errors else "golden.jsonl"
    write_jsonl(out / name, golden)
    write_jsonl(out / "golden_audit.jsonl", audit)
    write_jsonl(out / "corrections.jsonl", corrections_rows(audit))
    (out / "corrections.md").write_text(corrections_md(audit), encoding="utf-8")
    written = [name, "golden_audit.jsonl", "corrections.jsonl", "corrections.md"]
    if a.labeler_predictions:
        write_jsonl(Path(a.labeler_predictions),
                    [{"photo_id": x["photo_id"], "boxes": x["labeler"]["boxes"], "error": None} for x in audit])
        written.append(a.labeler_predictions)
    print(f"\nЗаписано в {out}: {', '.join(written)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
