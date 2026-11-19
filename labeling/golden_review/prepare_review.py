"""Готовит review_data.js для страницы разбора (index.html). Только чтение файлов, без сети.

    python3 labeling/golden_review/prepare_review.py [--images папка_с_фото]

Без --images страница берёт фото по ссылкам iNaturalist (нужен интернет).

Страница показывает все 400 фото; в очередь «к разбору» попадают фото с флагами
из golden_common.FLAGS. Подсказки Claude — только наблюдения при просмотре,
решение и рамки — за человеком.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import golden_common as gc

HERE = Path(__file__).resolve().parent

# Что Claude увидел при просмотре 19.11 — подсказки на странице, решения принимала команда.
NOTES = {
    "761589120": "Крупный мотылёк на стене, разметчик ответил «нет» — нужна рамка.",
    "762957220": "Крупный мотылёк на стене, ответ «нет» — нужна рамка. Оба ревьюера пропустили.",
    "763809006": "Мотылёк на сетке, ответ «нет» — нужна рамка.",
    "763887536": "Парусник на листе, ответ «нет» — нужна рамка.",
    "761664025": "Мотылёк на листе, ответ «нет» — нужна рамка.",
    "760640706": "Толстоголовка на соцветии вербены (вверху справа), ответ «нет» — нужна рамка.",
    "760796949": "Мелкая бархатница среди травы (в центре), ответ «нет» — нужна рамка.",
    "762672418": "Мелкая бабочка на ромашке (центр, слева), ответ «нет» — нужна рамка.",
    "762679766": "По предварительной метке — мелкая бабочка у края кадра; найти и выделить. Оба ревьюера пропустили.",
    "761828886": "Пальцекрылка — это мотылёк, нужна рамка (длинные тонкие крылья и ноги). Оба ревьюера пропустили.",
    "763234115": "Выделена одна бабочка из двух: вторая вверху у края кадра. Оба ревьюера пропустили.",
    "762012832": "Это оса, а не бабочка — рамку убрать («Бабочек нет»). Оба ревьюера пропустили.",
    "763676455": "Рамка с большим запасом фона справа и снизу — сузить.",
    "762373286": "Рамка с большим запасом фона справа и сверху — сузить.",
    "761792971": "Рамка, возможно, слишком свободная — проверить.",
    "763918376": "Рамка, возможно, слишком свободная слева и сверху — проверить.",
}
SPLIT_DEFAULT = "При беглом просмотре разметка выглядит нормальной; один ревьюер, вероятно, отклонил зря — проверьте."
BLOCKED_DEFAULT = "Проверили только заблокированные ревьюеры — нужен свежий взгляд. Claude это фото не смотрел."


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k, v in gc.DEFAULTS.items():
        p.add_argument(f"--{k}", default=str(v))
    p.add_argument("--images", help="папка со скачанными фото <photo_id>.jpg (необязательно)")
    p.add_argument("--out", default=str(HERE / "review_data.js"))
    a = p.parse_args()

    records, ds = gc.collect(a.bbox, a.review, a.restrictions, a.dataset)
    out_dir = Path(a.out).resolve().parent
    image_base = os.path.relpath(Path(a.images).resolve(), out_dir).replace(os.sep, "/") if a.images else None
    order = {f: i for i, f in enumerate(gc.FLAGS)}
    items = []
    for pid in ds:                                   # порядок как в датасете
        r, row = records[pid], ds[pid]
        if a.images and not (Path(a.images) / f"{pid}.jpg").is_file():
            raise SystemExit(f"нет картинки {Path(a.images) / (pid + '.jpg')}")
        note = NOTES.get(pid)
        if note is None and "split" in r["flags"]:
            note = SPLIT_DEFAULT
        if note is None and "blocked_only" in r["flags"]:
            note = BLOCKED_DEFAULT
        items.append({
            "photo_id": pid,
            "file": f"{pid}.jpg",
            "url": row.get("photo_url"),          # запасной источник, если локальной копии нет
            "labeler": {"status": r["labeler"]["status"], "regions": r["labeler"]["regions"]},
            "votes": [{"reviewer": v["reviewer"][:8], "verdict": v["verdict"], "blocked": v["blocked"]}
                      for v in r["votes"]],
            "flags": r["flags"],
            "must_review": r["must_review"],
            "priority": min((order[f] for f in r["flags"]), default=99),
            "prelim": r["prelim"],
            "tags": row.get("tags") or [],
            "group": row.get("source_group"),
            "observation_url": row.get("observation_url"),
            "note": note,
        })
    data = {
        "schema": "golden_review_data_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "image_base": image_base,
        "flag_names": gc.FLAGS,
        "items": items,
    }
    Path(a.out).write_text("window.REVIEW_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    queue = [i for i in items if i["must_review"]]
    by_flag = {f: sum(f in i["flags"] for i in items) for f in gc.FLAGS}
    print(f"Записано {a.out}: фото {len(items)}, к разбору {len(queue)}; по причинам: {by_flag}")


if __name__ == "__main__":
    main()
