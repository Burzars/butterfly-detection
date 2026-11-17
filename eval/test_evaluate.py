"""Проверки evaluate.py на маленьких примерах с заранее известным ответом.

Запуск (нужен только Python): python eval/test_evaluate.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evaluate as ev  # noqa: E402


def box(left, top, width, height, score=None):
    b = {"left": left, "top": top, "width": width, "height": height}
    if score is not None:
        b["score"] = score
    return b


def run(golden_rows, pred_rows, score_thr=None, nms_iou=None):
    golden = {r["photo_id"]: r for r in golden_rows}
    preds = {r["photo_id"]: r for r in pred_rows}
    photos, notes = ev.parse(golden, preds, "test")
    return ev.summarize(ev.score_photos(photos, score_thr, nms_iou)), notes


class Matching(unittest.TestCase):
    def test_golden_against_itself_gives_one(self):
        g = [{"photo_id": 1, "boxes": [box(.1, .1, .3, .3), box(.5, .5, .2, .2)]},
             {"photo_id": 2, "boxes": []}]
        s, _ = run(g, g)
        self.assertEqual((s["f1"], s["f1_75"], s["fp"], s["fn"]), (1.0, 1.0, 0, 0))

    def test_maximum_matching_not_greedy(self):
        # P1 перекрывает A и B (IoU 0.78 с обеими), P2 — только A (IoU 0.6).
        # Жадный выбор может отдать P1 рамке A и получить одну пару; максимум — две: P1–B, P2–A.
        A, B = box(0.10, 0.1, 0.4, 0.5), box(0.20, 0.1, 0.4, 0.5)
        P1, P2 = box(0.15, 0.1, 0.4, 0.5), box(0.00, 0.1, 0.4, 0.5)
        s, _ = run([{"photo_id": 1, "boxes": [A, B]}], [{"photo_id": 1, "boxes": [P1, P2]}])
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (2, 0, 0))

    def test_iou_06_counts_at_05_but_not_at_075(self):
        s, _ = run([{"photo_id": 1, "boxes": [box(0.1, 0.1, 0.4, 0.5)]}],
                   [{"photo_id": 1, "boxes": [box(0.2, 0.1, 0.4, 0.5)]}])  # IoU = 0.3 / 0.5 = 0.6
        self.assertEqual((s["tp"], s["tp75"]), (1, 0))
        self.assertEqual((s["f1"], s["f1_75"]), (1.0, 0.0))

    def test_iou_exactly_half_is_a_match(self):
        s, _ = run([{"photo_id": 1, "boxes": [box(0, 0, 0.4, 0.2)]}],
                   [{"photo_id": 1, "boxes": [box(0, 0, 0.2, 0.2)]}])  # половина площади → IoU 0.5
        self.assertEqual(s["tp"], 1)

    def test_duplicate_box_is_fp(self):
        b = box(.1, .1, .3, .3)
        s, _ = run([{"photo_id": 1, "boxes": [b]}], [{"photo_id": 1, "boxes": [b, b]}])
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (1, 1, 0))
        self.assertAlmostEqual(s["f1"], 2 / 3)

    def test_box_on_empty_photo_is_fp(self):
        s, _ = run([{"photo_id": 1, "boxes": []}, {"photo_id": 2, "boxes": []}],
                   [{"photo_id": 1, "boxes": [box(.1, .1, .2, .2)]}, {"photo_id": 2, "boxes": []}])
        self.assertEqual((s["fp"], s["fp_empty_boxes"], s["fp_empty_photos"], s["photos_empty"]), (1, 1, 1, 2))
        self.assertEqual(s["f1"], 0.0)

    def test_correct_empty_answer_adds_no_tp(self):
        s, _ = run([{"photo_id": 1, "boxes": []}], [{"photo_id": 1, "boxes": []}])
        self.assertEqual((s["tp"], s["fp"], s["fn"], s["f1"]), (0, 0, 0, None))

    def test_counts_summed_over_photos_then_f1(self):
        g = [{"photo_id": 1, "boxes": [box(.1, .1, .2, .2), box(.6, .6, .2, .2)]},  # 1 TP, 1 FN
             {"photo_id": 2, "boxes": [box(.1, .1, .2, .2)]},                       # 1 TP, 1 FP
             {"photo_id": 3, "boxes": []}]                                         # 1 FP
        p = [{"photo_id": 1, "boxes": [box(.1, .1, .2, .2)]},
             {"photo_id": 2, "boxes": [box(.1, .1, .2, .2), box(.6, .6, .2, .2)]},
             {"photo_id": 3, "boxes": [box(.3, .3, .1, .1)]}]
        s, _ = run(g, p)
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (2, 2, 1))
        self.assertAlmostEqual(s["f1"], 2 * 2 / (2 * 2 + 2 + 1))
        self.assertAlmostEqual(s["precision"], 2 / 4)
        self.assertAlmostEqual(s["recall"], 2 / 3)


class ErrorsAndFormat(unittest.TestCase):
    def test_error_row_is_counted_and_its_boxes_are_fn(self):
        s, _ = run([{"photo_id": 1, "boxes": [box(.1, .1, .3, .3)]}],
                   [{"photo_id": 1, "boxes": None, "error": "timeout"}])
        self.assertEqual((s["errors"], s["tp"], s["fn"]), (1, 0, 1))

    def test_missing_row_is_an_error(self):
        s, notes = run([{"photo_id": 1, "boxes": [box(.1, .1, .3, .3)]}], [])
        self.assertEqual((notes["missing"], s["errors"], s["fn"]), (1, 1, 1))

    def test_pixels_instead_of_fractions_rejected(self):
        with self.assertRaises(ev.FormatError):
            run([{"photo_id": 1, "boxes": []}], [{"photo_id": 1, "boxes": [box(120, 40, 300, 200)]}])

    def test_x2y2_instead_of_width_height_flagged(self):
        # левый верхний угол (0.5, 0.5), а вместо ширины и высоты записан правый нижний угол (0.9, 0.9)
        _, notes = run([{"photo_id": 1, "boxes": []}], [{"photo_id": 1, "boxes": [box(.5, .5, .9, .9)]}])
        self.assertEqual(notes["outside"], 1)

    def test_golden_without_final_answer_rejected(self):
        with self.assertRaises(ev.FormatError):
            run([{"photo_id": 1, "boxes": None}], [])

    def test_duplicate_photo_id_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "dup.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"photo_id": 1, "boxes": []}\n{"photo_id": 1, "boxes": []}\n')
            with self.assertRaises(ev.FormatError):
                ev.load_jsonl(path)

    def test_box_slightly_outside_frame_is_clipped(self):
        s, _ = run([{"photo_id": 1, "boxes": [box(0, .1, .28, .3)]}],
                   [{"photo_id": 1, "boxes": [box(-0.02, .1, .3, .3)]}])
        self.assertEqual(s["tp75"], 1)


class Postprocessing(unittest.TestCase):
    GOLDEN = [{"photo_id": 1, "boxes": [box(.1, .1, .3, .3)]}]

    def test_score_threshold_drops_low_confidence(self):
        p = [{"photo_id": 1, "boxes": [box(.1, .1, .3, .3, score=.9), box(.6, .6, .2, .2, score=.2)]}]
        s, _ = run(self.GOLDEN, p)
        self.assertEqual((s["tp"], s["fp"]), (1, 1))
        s, _ = run(self.GOLDEN, p, score_thr=0.5)
        self.assertEqual((s["tp"], s["fp"]), (1, 0))

    def test_nms_removes_duplicate_keeps_separate(self):
        p = [{"photo_id": 1, "boxes": [box(.1, .1, .3, .3, score=.9), box(.11, .1, .3, .3, score=.8),
                                       box(.6, .6, .2, .2, score=.7)]}]
        s, _ = run(self.GOLDEN, p, nms_iou=0.8)
        self.assertEqual((s["pred"], s["tp"], s["fp"]), (2, 1, 1))

    def test_boxes_without_score_pass_threshold(self):
        s, notes = run(self.GOLDEN, [{"photo_id": 1, "boxes": [box(.1, .1, .3, .3)]}], score_thr=0.9)
        self.assertEqual((s["tp"], notes["no_score"]), (1, 1))

    def test_parse_item(self):
        self.assertEqual(ev.parse_item("p/a__b.jsonl", None, None), ("p/a__b.jsonl", None, None))
        self.assertEqual(ev.parse_item("p/a.jsonl@score=0.3,nms=0.8", None, None), ("p/a.jsonl", 0.3, 0.8))
        self.assertEqual(ev.parse_item("p/a.jsonl@nms=off", 0.2, 0.5), ("p/a.jsonl", 0.2, None))
        with self.assertRaises(ev.FormatError):
            ev.parse_item("p/a.jsonl@thr=0.3", None, None)


class CommandLine(unittest.TestCase):
    def test_golden_against_itself_via_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            golden = os.path.join(d, "golden.jsonl")
            dataset = os.path.join(d, "dataset.jsonl")
            board = os.path.join(d, "leaderboard.md")
            with open(golden, "w", encoding="utf-8") as f:
                f.write(json.dumps({"photo_id": 1, "boxes": [box(.1, .1, .3, .3, score=.9)]}) + "\n")
                f.write(json.dumps({"photo_id": 2, "boxes": []}) + "\n")
            with open(dataset, "w", encoding="utf-8") as f:
                f.write(json.dumps({"photo_id": 1, "source_group": "moths", "tags": ["мелкий объект"]}) + "\n")
                f.write(json.dumps({"photo_id": 2, "source_group": "flies", "tags": []}) + "\n")
            res = subprocess.run(
                [sys.executable, os.path.join(HERE, "evaluate.py"), golden, golden, golden + "@score=0.95",
                 "--dataset", dataset, "--by", "source_group", "tags", "--sweep", "--leaderboard", board],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("F1@0.5  = 1.000", res.stdout)
            with open(board, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("| 1.000 | 1.000 | 1.000 | 1.000 |", text)
            self.assertIn("score ≥ 0.95", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
