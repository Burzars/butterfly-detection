"""Private, external bbox exam grader. Never opens pools or exposes reference boxes."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

from portable_runtime import API, ACCOUNT_ID, WORK, save_json, load_token, load_runtime_ids

POLICY = WORK / 'configs/bbox_exam_scoring.json'
REPORT = WORK / 'state/bbox_exam_results.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def rectangle(r):
    if not isinstance(r, dict) or r.get('shape') != 'rectangle':
        raise ValueError('Invalid rectangle')
    v = [r.get(k) for k in ('left', 'top', 'width', 'height')]
    if not all(type(x) in (int, float) and math.isfinite(x) for x in v):
        raise ValueError('Invalid coordinates')
    x, y, w, h = v
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
        raise ValueError('Coordinates outside image')
    return x, y, x + w, y + h


def iou(a, b):
    ax, ay, ar, ab = rectangle(a)
    bx, by, br, bb = rectangle(b)
    intersection = max(0, min(ar, br) - max(ax, bx)) * max(0, min(ab, bb) - max(ay, by))
    return intersection / ((ar - ax) * (ab - ay) + (br - bx) * (bb - by) - intersection)


def grade_photo(answer, gold, threshold=0.75):
    if not isinstance(answer, dict):
        return {'correct': False, 'reason': 'missing_answer'}
    regions = answer.get('regions', [])
    if not isinstance(regions, list):
        return {'correct': False, 'reason': 'invalid_regions'}
    if gold['status'] == 'empty':
        ok = answer.get('status') == 'empty' and len(regions) == 0
        return {'correct': ok, 'reason': 'empty_match' if ok else 'not_empty_match'}
    expected = gold['regions']
    if answer.get('status') != 'objects' or len(regions) != len(expected):
        return {'correct': False, 'reason': 'status_or_object_count'}
    try:
        matrix = [[iou(pred, ref) for ref in expected] for pred in regions]
    except (ValueError, TypeError):
        return {'correct': False, 'reason': 'invalid_rectangle'}
    # Maximum bipartite matching, not a greedy choice: every box must match once.
    matched = {}

    def augment(pred, visited):
        for ref, overlap in enumerate(matrix[pred]):
            if overlap < threshold - 1e-12 or ref in visited:
                continue
            visited.add(ref)
            if ref not in matched or augment(matched[ref], visited):
                matched[ref] = pred
                return True
        return False

    count = sum(augment(pred, set()) for pred in range(len(regions)))
    pairs = [{'prediction': pred, 'reference': ref, 'iou': matrix[pred][ref]}
             for ref, pred in sorted(matched.items())]
    return {'correct': count == len(expected), 'matched': count,
            'expected': len(expected), 'pairs': pairs, 'reason': 'iou_matching'}


def load_policy():
    policy = json.loads(POLICY.read_text())
    objects = load_runtime_ids()
    expected = {'pool_id': objects['bbox_exam'], 'project_id': objects['bbox_project'],
                'skill_id': objects['bbox_skill'], 'opposite_skill_id': objects['review_skill']}
    if any(policy[k] != value for k, value in expected.items()):
        raise RuntimeError('Scorer is not bound to the configured bbox exam')
    source = WORK / policy['reference_file']
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != policy['reference_sha256']:
        raise RuntimeError('Reference file changed; review the scoring policy first')
    rows = json.loads(raw)['items']
    gold = {str(e['photo_id']): e for e in rows if e['section'] == '02_bbox_exam'}
    if len(gold) != 8 or set(gold) != set(policy['photo_ids']):
        raise RuntimeError('Expected exactly eight reference exam images')
    for row in gold.values():
        regions = row['gold']['regions']
        if (row['gold']['status'] == 'empty') != (len(regions) == 0):
            raise RuntimeError('Invalid reference status')
        for r in regions:
            rectangle(r)
    return policy, gold


def grade_assignment(assignment, suite, gold, policy):
    if str(assignment.get('pool_id')) != policy['pool_id']:
        raise ValueError('Assignment belongs to another pool')
    if assignment.get('task_suite_id') not in (None, suite['id']):
        raise ValueError('Assignment belongs to another exam suite')
    tasks, solutions = assignment.get('tasks', []), assignment.get('solutions', [])
    expected = {str(t['id']): t['input_values'] for t in suite['tasks']}
    if len(tasks) != 8 or len(solutions) != 8 or {str(t['id']) for t in tasks} != set(expected):
        raise ValueError('Incomplete or unexpected exam task set')
    photos = []
    for task, solution in zip(tasks, solutions):
        values = task['input_values']
        if values != expected[str(task['id'])]:
            raise ValueError('Exam input mismatch')
        pid = str(values['photo_id'])
        photos.append({'photo_id': pid, **grade_photo(solution.get('output_values'),
                      gold[pid]['gold'], policy['iou_threshold'])})
    score = sum(p['correct'] for p in photos)
    return {'assignment_id': str(assignment['id']), 'user_id': str(assignment['user_id']),
            'correct': score, 'total': 8, 'passed': score >= policy['pass_count'], 'photos': photos}


def completion_reason(pool, suite, assignments, results, apply):
    """Do not exit before launch, during an attempt, or before its admission is settled."""
    if not apply or any(a['status'] in ('ACTIVE', 'SUBMITTED') for a in assignments):
        return None
    graded = {r['assignment_id'] for r in results}
    if any(str(a['id']) not in graded for a in assignments if a['status'] == 'ACCEPTED'):
        return None
    if any(not r.get('payment_accepted') or
           (r['passed'] and not r.get('admission_verified') and not r.get('blocked_reason'))
           for r in results):
        return None
    completed = sum(a['status'] in ('ACCEPTED', 'REJECTED') for a in assignments)
    quota = suite.get('overlap', 0)
    if not suite.get('infinite_overlap') and quota > 0 and completed >= quota:
        return 'exam_attempt_limit_completed'
    if pool['status'] == 'ARCHIVED':
        return 'exam_archived'
    if pool['status'] == 'CLOSED' and (pool.get('last_started') or completed):
        return 'started_exam_closed_and_all_attempts_settled'
    return None


def run_once(api, apply=False):
    policy, gold = load_policy()
    account = api.get('requester')
    if str(account['id']) != ACCOUNT_ID:
        raise RuntimeError('Wrong requester account')
    pool = api.get('pools/' + policy['pool_id'])
    if (str(pool['id']) != policy['pool_id'] or str(pool['project_id']) != policy['project_id'] or
            pool['reward_per_assignment'] != 6):
        raise RuntimeError('Exam pool or payment policy changed')
    suite = api.get('task-suites/' + policy['suite_id'])
    if (suite['id'] != policy['suite_id'] or str(suite['pool_id']) != policy['pool_id'] or
            len(suite['tasks']) != 8):
        raise RuntimeError('Unexpected exam suite')
    if {str(t['input_values']['photo_id']) for t in suite['tasks']} != set(gold):
        raise RuntimeError('Exam images differ from private references')
    for task in suite['tasks']:
        if task['input_values']['image'] != gold[str(task['input_values']['photo_id'])]['image']:
            raise RuntimeError('Exam image URL changed')
    all_assignments = api.all('assignments', pool_id=policy['pool_id'])
    if any(str(a.get('pool_id')) != policy['pool_id'] for a in all_assignments):
        raise RuntimeError('Assignments query returned another pool')
    assignments = [a for a in all_assignments
                   if a['status'] in ('SUBMITTED', 'ACCEPTED')]
    if len({str(a['user_id']) for a in assignments}) != len(assignments):
        raise RuntimeError('Multiple completed attempts by one user; manual review required')
    own = {str(s['user_id']): s for s in api.all('user-skills', skill_id=policy['skill_id'])}
    other = {str(s['user_id']) for s in api.all('user-skills', skill_id=policy['opposite_skill_id'])}
    results = [grade_assignment(a, suite, gold, policy) for a in assignments]
    report = {'checked_at': now(), 'mode': 'apply' if apply else 'dry_run',
              'pool_id': policy['pool_id'], 'iou_threshold': policy['iou_threshold'],
              'pass_count': policy['pass_count'], 'results': results}
    # Preserve the complete grading decision before any write to the service.
    save_json(REPORT, report)
    for a, result in zip(assignments, results):
        user = result['user_id']
        if result['passed'] and user in other:
            result['blocked_reason'] = 'opposite_role_skill_present'
        if not result['passed'] and user in own and float(own[user]['value']) == 100:
            raise RuntimeError('Admission exists for a failed exam; manual review required')
        if not apply:
            continue
        # All completed exam attempts are paid, independently of admission.
        current = api.get('assignments/' + str(a['id']))
        if (str(current.get('pool_id')) != policy['pool_id'] or
                str(current.get('user_id')) != user or current.get('id') != a['id']):
            raise RuntimeError('Assignment identity changed; no writes permitted')
        if current['status'] == 'SUBMITTED':
            api.request('PATCH', '/api/v1/assignments/' + str(a['id']), body={'status': 'ACCEPTED'})
            current = api.get('assignments/' + str(a['id']))
        if current['status'] != 'ACCEPTED':
            raise RuntimeError('Assignment not accepted; admission withheld')
        result['payment_accepted'] = True
        if result['passed'] and user not in other:
            # Recheck the other role just before granting admission.
            if api.all('user-skills', skill_id=policy['opposite_skill_id'], user_id=user):
                result['blocked_reason'] = 'opposite_role_skill_present'
                continue
            if user not in own or float(own[user]['value']) != 100:
                api.request('PUT', '/api/v1/user-skills', body={
                    'skill_id': policy['skill_id'], 'user_id': user, 'value': 100})
            granted = api.all('user-skills', skill_id=policy['skill_id'], user_id=user)
            if not any(float(s['value']) == 100 for s in granted):
                raise RuntimeError('Admission write could not be verified')
            result['admission_verified'] = True
        save_json(REPORT, report)
    # Reread after writes: a candidate may still be answering a reserved page.
    final_assignments = api.all('assignments', pool_id=policy['pool_id'])
    if any(str(a.get('pool_id')) != policy['pool_id'] for a in final_assignments):
        raise RuntimeError('Final assignments query returned another pool')
    final_pool = api.get('pools/' + policy['pool_id'])
    final_suite = api.get('task-suites/' + policy['suite_id'])
    report['stop_reason'] = completion_reason(final_pool, final_suite, final_assignments, results, apply)
    report['finished_at'] = now()
    save_json(REPORT, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Accept completed attempts and grant passing admission')
    parser.add_argument('--watch', action='store_true', help='Check once per minute until stopped')
    args = parser.parse_args()
    # Exclude concurrent graders, including a manual invocation during --watch.
    (WORK / 'state').mkdir(exist_ok=True)
    with (WORK / 'state/bbox_exam_control.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.write(str(os.getpid())); lock.flush()
        while True:
            try:
                token = load_token()
                training_result=None;training_error=None
                if (WORK/'configs/bbox_training_scoring.json').exists():
                    try:
                        from bbox_training_control import run_training_once
                        training_result=run_training_once(API(token),args.apply)
                    except Exception as error:
                        training_error=type(error).__name__
                result = run_once(API(token), args.apply)
                if result['stop_reason'] and training_result and args.apply:
                    learning_pool=API(token).get('pools/'+training_result['pool_id'])
                    if learning_pool['status']=='OPEN':
                        API(token).post('pools/'+training_result['pool_id']+'/close')
                if training_error or (training_result and training_result['active_pages']):
                    result['stop_reason']=None
                save_json(WORK / 'state/bbox_exam_control_health.json', {
                    'checked_at': now(), 'ok': True, 'pid': os.getpid(),
                    'apply': args.apply, 'attempts': len(result['results']),
                    'running': not bool(result['stop_reason']),
                    'status': 'stopped' if result['stop_reason'] else 'watching',
                    'stop_reason': result['stop_reason'],
                    'training_ok':training_error is None,'training_error':training_error,
                    'training_pool_id':training_result['pool_id'] if training_result else None})
                print(now(), 'checked', len(result['results']), 'attempts', flush=True)
                if result['stop_reason']:
                    print(now(), 'STOP', result['stop_reason'], flush=True)
                    break
            except Exception as error:
                # Do not log request bodies, credentials, or reference geometry.
                save_json(WORK / 'state/bbox_exam_control_health.json', {
                    'checked_at': now(), 'ok': False, 'pid': os.getpid(),
                    'error_type': type(error).__name__})
                print(now(), 'ERROR', type(error).__name__, flush=True)
                if not args.watch:
                    raise
            if not args.watch:
                break
            time.sleep(60)


if __name__ == '__main__':
    main()
