"""Upload exactly 400 accepted bbox results; never open or moderate review."""
import copy
import json
from datetime import datetime, timezone
from build_projects import Drafts, WORK, save_json
from bbox_exam_control import rectangle
from remove_bbox_image_error import upload_once


def main():
    d = Drafts()
    pid = d.state['objects']['review_main']
    folder = WORK/'state/review_400_upload'
    pool = d.api.get('pools/'+pid)
    assert pool['status'] == 'CLOSED' and not pool.get('last_started')
    assert not d.api.all('assignments', pool_id=pid)
    assert pool['reward_per_assignment'] == .5
    assert pool['mixer_config']['real_tasks_count'] == 4
    assert pool['mixer_config']['golden_tasks_count'] == 1
    assert pool['mixer_config']['min_golden_tasks_count'] == 1
    before = d.api.all('tasks', pool_id=pid)
    controls = [t for t in before if t.get('known_solutions')]
    assert len(controls) == 30
    save_json(folder/'pool_before.json', pool)
    save_json(folder/'controls_before.json', controls)
    raw = [json.loads(s) for s in (WORK/'Результаты_bbox/bbox_400_raw.jsonl').read_text().splitlines()]
    assert len(raw) == len({r['photo_id'] for r in raw}) == 400
    original = {t['input_values']['photo_id']: t for t in json.loads((WORK/'state/bbox_tasks.json').read_text())}
    assert set(original) == {r['photo_id'] for r in raw}
    assert not {r['photo_id'] for r in raw} & {t['input_values']['photo_id'] for t in controls}
    defaults = json.loads((WORK/'state/review_main_ui_defaults.json').read_text())
    tasks = []
    for r in raw:
        assert r['task_id'] == original[r['photo_id']]['id']
        assert r['image'] == original[r['photo_id']]['input_values']['image']
        assert r['status'] in ['objects', 'empty'] and isinstance(r['regions'], list)
        assert (r['status'] == 'objects') == bool(r['regions'])
        for box in r['regions']:
            rectangle(box)
        values = dict(defaults, photo_id=r['photo_id'], image=r['image'], regions=copy.deepcopy(r['regions']),
            source_assignment_id=r['source_assignment_id'], source_worker_id=r['source_worker_id'],
            annotation_id='bbox-'+r['task_id'])
        tasks.append({'pool_id': pid, 'input_values': values, 'overlap': 2,
                      'unavailable_for': [r['source_worker_id']]})
    expected = {t['input_values']['annotation_id']: t for t in tasks}
    assert len(expected) == 400
    existing = [t for t in before if not t.get('known_solutions')]
    if existing:
        assert len(existing) == 400, 'Partial or unknown prior upload requires inspection'
    else:
        key = 'review_bbox_400_'+pid
        d.state['objects'][key] = pid
        upload_once(d, key, 'tasks', tasks)
    actual = d.api.all('tasks', pool_id=pid)
    real = [t for t in actual if not t.get('known_solutions')]
    gold = [t for t in actual if t.get('known_solutions')]
    assert len(actual) == 430 and len(real) == 400 and len(gold) == 30
    assert len({t['input_values']['photo_id'] for t in real}) == 400
    for t in real:
        e = expected[t['input_values']['annotation_id']]
        assert t['input_values'] == e['input_values']
        assert t['overlap'] == 2 and not t.get('infinite_overlap')
        assert t.get('unavailable_for') == e['unavailable_for']
    by_id = {t['id']: t for t in gold}
    for t in controls:
        assert by_id[t['id']] == t
    final = d.api.get('pools/'+pid)
    assert final['status'] == 'CLOSED' and not final.get('last_started')
    assert not d.api.all('assignments', pool_id=pid)
    for field in ['filter', 'quality_control', 'mixer_config', 'defaults', 'reward_per_assignment']:
        assert final.get(field) == pool.get(field)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'pool_id': pid,
        'status': final['status'], 'work_photos': 400, 'controls': 30, 'overlap': 2,
        'expected_work_verdicts': 800, 'author_excluded_from_own_task': True,
        'regions_preserved': True, 'moderation_submitted': False, 'opened': False,
        'source_statuses': {s: sum(r['status'] == s for r in raw) for s in ['objects', 'empty']}}
    save_json(folder/'verified.json', report)
    save_json(folder/'work_tasks.json', real)
    save_json(WORK/'state/review_main.json', final)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
