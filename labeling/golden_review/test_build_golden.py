"""Тесты сборки golden set. Запуск: python3 labeling/golden_review/test_build_golden.py"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_golden as bg          # noqa: E402
import golden_common as gc         # noqa: E402

EVALUATE = gc.ROOT / "eval/evaluate.py"


def box(l, t, w, h):
    return {"shape": "rectangle", "left": l, "top": t, "width": w, "height": h}


B1 = box(0.1234567891, 0.2, 0.3, 0.4)


class Fixture:
    """4 фото: A — всё хорошо; B — «нет» при бабочке; C — рамка на не-бабочке, 1:1; D — только заблокированные."""

    def __init__(self, tmp, review_regions=None, self_review=False, drop_photo=False):
        self.dir = Path(tmp)
        rows = [
            {"photo_id": 1, "preliminary_label": "бабочка", "review_count": "количество: 1"},
            {"photo_id": 2, "preliminary_label": "бабочка", "review_count": "количество: 1"},
            {"photo_id": 3, "preliminary_label": "не бабочка", "review_count": "количество: 0 при просмотре"},
            {"photo_id": 4, "preliminary_label": "не бабочка"},
        ]
        if drop_photo:
            rows.append({"photo_id": 5, "preliminary_label": "не бабочка"})
        (self.dir / "ds.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        labels = {"1": [B1], "2": [], "3": [box(0.5, 0.5, 0.2, 0.2)], "4": []}
        tasks, sols = [], []
        for pid, regs in labels.items():
            tasks.append({"input_values": {"photo_id": pid}})
            sols.append({"output_values": {"status": "objects" if regs else "empty", "regions": regs}})
        tasks.append({"input_values": {"photo_id": "999"}, "known_solutions": [{"output_values": {"status": "empty"}}]})
        sols.append({"output_values": {"status": "empty", "regions": []}})
        bbox = [{"id": "as1", "user_id": "lab1", "status": "ACCEPTED", "tasks": tasks, "solutions": sols},
                {"id": "as2", "user_id": "lab2", "status": "EXPIRED", "tasks": [], "solutions": []}]
        (self.dir / "bbox.json").write_text(json.dumps(bbox), encoding="utf-8")

        def rt(pid, regs=None):
            return {"input_values": {"photo_id": pid, "regions": labels[pid] if regs is None else regs}}
        votes = {"1": [("r1", "accept"), ("r2", "accept")], "2": [("r1", "accept"), ("r2", "accept")],
                 "3": [("r1", "reject"), ("r2", "accept")], "4": [("bad1", "accept"), ("bad2", "accept")]}
        review = []
        for pid, vs in votes.items():
            for who, v in vs:
                if self_review and pid == "1" and who == "r1":
                    who = "lab1"
                regs = review_regions if (review_regions is not None and pid == "1") else None
                review.append({"id": f"rv-{pid}-{who}", "user_id": who, "status": "ACCEPTED",
                               "tasks": [rt(pid, regs)], "solutions": [{"output_values": {"status": v}}]})
        (self.dir / "review.json").write_text(json.dumps(review), encoding="utf-8")
        (self.dir / "restr.json").write_text(json.dumps({"items": [{"user_id": "bad1"}, {"user_id": "bad2"}],
                                                         "has_more": False}), encoding="utf-8")

    def collect(self):
        return gc.collect(self.dir / "bbox.json", self.dir / "review.json", self.dir / "restr.json", self.dir / "ds.jsonl")

    def args(self, decisions=None):
        a = ["--bbox", str(self.dir / "bbox.json"), "--review", str(self.dir / "review.json"),
             "--restrictions", str(self.dir / "restr.json"), "--dataset", str(self.dir / "ds.jsonl"),
             "--out-dir", str(self.dir / "out")]
        if decisions is not None:
            (self.dir / "dec.json").write_text(json.dumps({"schema": "golden_decisions_v1", "decisions": decisions}),
                                              encoding="utf-8")
            a += ["--decisions", str(self.dir / "dec.json")]
        return a


def dec(kind, boxes=None, comment="пояснение", by="Арсений"):
    d = {"decision": kind, "comment": comment, "decided_by": by, "decided_at": "2026-11-19T12:00:00Z"}
    if boxes is not None:
        d["boxes"] = boxes
    return d


GOOD = {"2": dec("fix", [{"left": 0.4, "top": 0.4, "width": 0.2, "height": 0.1}]), "3": dec("empty"), "4": dec("keep", comment="")}


def run_main(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = bg.main(args)
    return code, out.getvalue() + err.getvalue()


class CollectTests(unittest.TestCase):
    def test_flags(self):
        with tempfile.TemporaryDirectory() as t:
            rec, _ = Fixture(t).collect()
        self.assertEqual(rec["1"]["flags"], [])
        self.assertEqual(rec["2"]["flags"], ["mismatch"])
        self.assertEqual(rec["3"]["flags"], ["mismatch", "split"])
        self.assertEqual(rec["4"]["flags"], ["blocked_only"])
        self.assertFalse(rec["1"]["must_review"])
        self.assertEqual(rec["1"]["labeler"]["regions"][0], {k: B1[k] for k in gc.BOX_KEYS})   # без shape, числа те же

    def test_review_saw_other_boxes(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(gc.DataError, "не те рамки"):
                Fixture(t, review_regions=[box(0.0, 0.0, 0.5, 0.5)]).collect()

    def test_self_review(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(gc.DataError, "сам себя"):
                Fixture(t, self_review=True).collect()

    def test_photo_without_answer(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(gc.DataError, "не совпадает с датасетом"):
                Fixture(t, drop_photo=True).collect()


class BuildTests(unittest.TestCase):
    def build(self, decisions):
        with tempfile.TemporaryDirectory() as t:
            rec, ds = Fixture(t).collect()
        return bg.build(rec, ds, decisions)

    def test_unresolved_blocks(self):
        _, _, errors = self.build({})
        self.assertEqual(len(errors), 3)
        self.assertTrue(all("не разобрано" in e for e in errors))

    def test_good_decisions(self):
        golden, audit, errors = self.build(GOOD)
        self.assertEqual(errors, [])
        g = {x["photo_id"]: x["boxes"] for x in golden}
        self.assertEqual(g[1], [{k: B1[k] for k in gc.BOX_KEYS}])          # координаты не округляются
        self.assertEqual(g[2], [{"left": 0.4, "top": 0.4, "width": 0.2, "height": 0.1}])
        self.assertEqual(g[3], [])
        self.assertEqual(g[4], [])
        self.assertEqual([a["source"] for a in audit], ["auto_accept", "team_fix", "team_empty", "team_keep"])
        self.assertEqual([a["changed"] for a in audit], [False, True, True, False])

    def test_bad_decisions(self):
        cases = [
            ({"2": dec("fix", [{"left": 0.4, "top": 0.4, "width": 0.2, "height": 0.1}], comment="")}, "без пояснения"),
            ({"2": dec("fix", [{"left": 10, "top": 20, "width": 300, "height": 100}])}, "за кадр"),
            ({"2": dec("fix", [])}, "рамок нет"),
            ({"1": dec("fix", [{k: B1[k] for k in gc.BOX_KEYS}])}, "совпадают с разметчиком"),
            ({"3": dec("empty", comment="")}, "без пояснения"),
            ({"2": dec("unsure")}, "не уверен"),
            ({"2": dec("keep", by="")}, "кто принял"),
            ({"77": dec("keep")}, "нет в датасете"),
            ({"2": dec("maybe")}, "неизвестное решение"),
        ]
        for extra, needle in cases:
            with self.subTest(needle=needle):
                _, _, errors = self.build(dict(GOOD, **extra))
                self.assertTrue(any(needle in e for e in errors), errors)


class CliTests(unittest.TestCase):
    def test_refuses_without_decisions(self):
        with tempfile.TemporaryDirectory() as t:
            f = Fixture(t)
            code, text = run_main(f.args())
            self.assertEqual(code, 1)
            self.assertIn("Ничего не записано", text)
            self.assertFalse((f.dir / "out").exists())

    def test_two_decision_files(self):
        with tempfile.TemporaryDirectory() as t:
            f = Fixture(t)
            p1, p2 = f.dir / "a.json", f.dir / "b.json"
            p1.write_text(json.dumps({"schema": "golden_decisions_v1", "decisions": {"2": GOOD["2"], "3": GOOD["3"]}}), encoding="utf-8")
            p2.write_text(json.dumps({"schema": "golden_decisions_v1", "decisions": {"4": GOOD["4"], "3": GOOD["3"]}}), encoding="utf-8")
            code, text = run_main(f.args() + ["--decisions", str(p1), str(p2)])
            self.assertEqual(code, 0, text)                                   # одинаковое решение в двух файлах — можно
            p2.write_text(json.dumps({"schema": "golden_decisions_v1", "decisions": {"4": GOOD["4"], "3": dec("keep")}}), encoding="utf-8")
            code, text = run_main(f.args() + ["--decisions", str(p1), str(p2)])
            self.assertEqual(code, 2)
            self.assertIn("разные решения", text)

    def test_override_resolves_conflict(self):
        with tempfile.TemporaryDirectory() as t:
            f = Fixture(t)
            fix_a = dec("fix", [{"left": 0.4, "top": 0.4, "width": 0.2, "height": 0.1}], by="Миша")
            fix_b = dec("fix", [{"left": 0.41, "top": 0.4, "width": 0.2, "height": 0.1}], by="Настя")
            for name, d in (("a.json", {"2": fix_a, "3": GOOD["3"], "4": GOOD["4"]}), ("b.json", {"2": fix_b}),
                            ("o.json", {"2": fix_a}), ("o2.json", {"2": dec("keep", by="Арсений")})):
                (f.dir / name).write_text(json.dumps({"schema": "golden_decisions_v1", "decisions": d}), encoding="utf-8")
            base = ["--decisions", str(f.dir / "a.json"), str(f.dir / "b.json")]
            code, text = run_main(f.args() + base)
            self.assertEqual(code, 2)                                          # без ответственного — противоречие
            self.assertIn("--override", text)
            code, text = run_main(f.args() + base + ["--override", str(f.dir / "o.json")])
            self.assertEqual(code, 0, text)                                    # выбран вариант Миши
            audit = [json.loads(x) for x in (f.dir / "out/golden_audit.jsonl").read_text(encoding="utf-8").splitlines()]
            a2 = [x for x in audit if x["photo_id"] == 2][0]
            self.assertEqual(a2["decided_by"], "Миша")
            self.assertEqual([x["decided_by"] for x in a2["superseded"]], ["Настя"])
            self.assertIn("заменено решение — Настя", (f.dir / "out/corrections.md").read_text(encoding="utf-8"))
            code, text = run_main(f.args() + base + ["--override", str(f.dir / "o.json"), str(f.dir / "o2.json")])
            self.assertEqual(code, 2)                                          # противоречие внутри override
            self.assertIn("--override", text)

    def test_end_to_end_with_evaluate(self):
        with tempfile.TemporaryDirectory() as t:
            f = Fixture(t)
            code, text = run_main(f.args(GOOD) + ["--labeler-predictions", str(f.dir / "out/labeler_predictions.jsonl")])
            self.assertEqual(code, 0, text)
            out = f.dir / "out"
            corr = [json.loads(x) for x in (out / "corrections.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(c["photo_id"], c["decision"], c["by"]) for c in corr], [(2, "fix", "человек"), (3, "empty", "человек")])
            self.assertEqual(corr[1]["before"], [{k: 0.5 if k in ("left", "top") else 0.2 for k in gc.BOX_KEYS}])
            lines = [json.loads(x) for x in (out / "golden.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([x["photo_id"] for x in lines], [1, 2, 3, 4])
            self.assertIn("| 2 | «нет» |", (out / "corrections.md").read_text(encoding="utf-8"))
            if not EVALUATE.exists():
                self.skipTest("нет eval/evaluate.py")
            # эталон против самого себя → F1 = 1
            r = subprocess.run([sys.executable, str(EVALUATE), str(out / "golden.jsonl"), str(out / "golden.jsonl")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            # эталон против разметчиков: расходятся ровно фото 2 (пропуск) и 3 (лишняя рамка)
            pp = f.dir / "pp"
            r = subprocess.run([sys.executable, str(EVALUATE), str(out / "golden.jsonl"), str(out / "labeler_predictions.jsonl"),
                                "--per-photo-dir", str(pp)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = [json.loads(x) for p in pp.glob("*.jsonl") for x in p.read_text(encoding="utf-8").splitlines()]
            bad = sorted(x["photo_id"] for x in rows if x.get("fp") or x.get("fn"))
            self.assertEqual(bad, [2, 3])


if __name__ == "__main__":
    unittest.main(verbosity=1)
