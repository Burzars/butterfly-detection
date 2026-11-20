"""Small regression check of admission decisions, without paid API calls."""
import copy
import json
import unittest
from unittest.mock import patch

import bbox_exam_control as c


def box(left=0, width=1):
    return {'shape': 'rectangle', 'left': left, 'top': 0, 'width': width, 'height': 1}


class ControlTests(unittest.TestCase):
    def test_empty_and_technical_error(self):
        gold = {'status': 'empty', 'regions': []}
        self.assertTrue(c.grade_photo({'status': 'empty'}, gold)['correct'])
        self.assertFalse(c.grade_photo({'status': 'image_error'}, gold)['correct'])
        self.assertFalse(c.grade_photo({'status': 'empty', 'regions': [box()]}, gold)['correct'])

    def test_threshold_and_invalid_geometry(self):
        gold = {'status': 'objects', 'regions': [box()]}
        for width, expected in [(0.75, True), (0.749, False)]:
            self.assertEqual(c.grade_photo({'status': 'objects', 'regions': [box(width=width)]}, gold)['correct'], expected)
        self.assertFalse(c.grade_photo({'status': 'objects', 'regions': [box(width=float('nan'))]}, gold)['correct'])

    def test_count_and_unique_matching(self):
        gold = {'status': 'objects', 'regions': [box(width=.3), box(left=.7, width=.3)]}
        for regions, expected in [([box(width=.3)], False), ([box(width=.3)] * 2, False),
                                  (list(reversed(gold['regions'])), True), (gold['regions'] + [box()], False)]:
            self.assertEqual(c.grade_photo({'status': 'objects', 'regions': regions}, gold)['correct'], expected)

    def fixture(self, correct):
        policy, gold = c.load_policy()
        suite = json.loads((c.WORK/'fixtures/bbox_exam_suite.json').read_text())
        solutions = [{'output_values': copy.deepcopy(gold[str(t['input_values']['photo_id'])]['gold'])}
                     for t in suite['tasks']]
        for s in solutions[correct:]:
            s['output_values'] = {'status': 'image_error', 'regions': [], 'comment': 'test'}
        a = {'id': 'test-assignment', 'pool_id': policy['pool_id'], 'user_id': 'test-user',
             'status': 'SUBMITTED', 'tasks': suite['tasks'], 'solutions': solutions}
        return policy, gold, suite, a

    def test_pass_7_fail_6_and_no_answers(self):
        for score in (6, 7, 8):
            policy, gold, suite, a = self.fixture(score)
            result = c.grade_assignment(a, suite, gold, policy)
            self.assertEqual(result['correct'], score)
            self.assertEqual(result['passed'], score >= 7)
        a['solutions'].pop()
        with self.assertRaises(ValueError):
            c.grade_assignment(a, suite, gold, policy)

    def test_payment_and_admission_idempotent(self):
        for correct in (6, 7):
            policy, gold, suite, assignment = self.fixture(correct)
            class FakeAPI:
                skills = []
                writes = []
                def get(self, path):
                    if path == 'requester': return {'id': c.ACCOUNT_ID}
                    if path.startswith('pools/'): return {'id': policy['pool_id'], 'project_id': '11893',
                        'reward_per_assignment': 6, 'status': 'CLOSED', 'last_started': None}
                    if path.startswith('task-suites/'): return suite
                    return assignment
                def all(self, path, **kwargs):
                    if path == 'assignments': return [assignment]
                    return [s for s in self.skills if s['skill_id'] == kwargs['skill_id']]
                def request(self, method, path, body):
                    self.writes.append((method, path))
                    if method == 'PATCH': assignment['status'] = body['status']
                    else: self.skills.append(body)
            api = FakeAPI()
            with patch.object(c, 'save_json'):
                c.run_once(api, apply=True)
                count = len(api.writes)
                c.run_once(api, apply=True)
            self.assertEqual(assignment['status'], 'ACCEPTED')
            self.assertEqual(bool(api.skills), correct >= 7)
            self.assertEqual(len(api.writes), count)

    def test_foreign_pool_or_suite_never_graded(self):
        policy, gold, suite, a = self.fixture(8)
        a['pool_id'] = '6869461'  # Main bbox, not the exam.
        with self.assertRaises(ValueError):
            c.grade_assignment(a, suite, gold, policy)
        a['pool_id'] = policy['pool_id']; a['task_suite_id'] = 'another-page'
        with self.assertRaises(ValueError):
            c.grade_assignment(a, suite, gold, policy)

    def test_stop_only_after_exam_is_settled(self):
        pool = {'status': 'CLOSED', 'last_started': None}
        suite = {'overlap': 2, 'infinite_overlap': False}
        self.assertIsNone(c.completion_reason(pool, suite, [], [], True))
        assignments = [{'id': 'a', 'status': 'ACCEPTED'}, {'id': 'b', 'status': 'ACCEPTED'}]
        results = [{'assignment_id': a['id'], 'passed': True, 'payment_accepted': True,
                    'admission_verified': True} for a in assignments]
        self.assertEqual(c.completion_reason(pool, suite, assignments, results, True),
                         'exam_attempt_limit_completed')
        assignments[1]['status'] = 'ACTIVE'
        self.assertIsNone(c.completion_reason(pool, suite, assignments, results, True))
        assignments[1]['status'] = 'SUBMITTED'
        self.assertIsNone(c.completion_reason(pool, suite, assignments, results, True))
        assignments[1]['status'] = 'ACCEPTED'; results[1]['admission_verified'] = False
        self.assertIsNone(c.completion_reason(pool, suite, assignments, results, True))
        self.assertIsNone(c.completion_reason(pool, suite, assignments, results[:1], True))
        pool['last_started'] = '2026-11-18T13:00:00Z'
        self.assertEqual(c.completion_reason(pool, suite, assignments[:1], results[:1], True),
                         'started_exam_closed_and_all_attempts_settled')


if __name__ == '__main__':
    unittest.main()
