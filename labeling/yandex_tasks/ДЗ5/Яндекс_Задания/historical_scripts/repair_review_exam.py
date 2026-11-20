"""Replace the unstarted review exam page after correcting two antenna cutoffs."""
import json
from build_projects import Drafts, WORK, save_json
from upload_quality_examples import perform_batch, inputs, known


def main():
    d = Drafts()
    pool_id = d.state['objects']['review_exam']
    pool = d.api.get('pools/' + pool_id)
    assert pool['status'] == 'CLOSED' and not pool.get('last_started')
    assert not d.api.all('assignments', pool_id=pool_id)
    folder = WORK / 'state/review_exam_antennae_repair'
    old_path = folder / 'old_suite.json'
    if not old_path.exists():
        save_json(old_path, json.loads((WORK / 'state/quality_suite_review_exam.json').read_text()))
    old = json.loads(old_path.read_text())
    source = json.loads((WORK.parent / 'Проверка_эталонов/annotations_reviewed.json').read_text())
    rows = {str(e['photo_id']): e for e in source['items'] if e['section'] == '04_review_exam'}
    tasks = [{'input_values': inputs(rows[str(t['input_values']['photo_id'])], 'review'),
              'known_solutions': known(rows[str(t['input_values']['photo_id'])], 'review')}
             for t in old['tasks']]
    assert len(tasks) == 12
    payload = {'pool_id': pool_id, 'tasks': tasks, 'overlap': 10, 'infinite_overlap': False}
    # Persisted operation UUID makes retrying creation safe.
    key = 'review_exam_antennae_v1'
    d.state['objects'][key] = pool_id
    perform_batch(d, key, 'task-suites', [payload])
    suites = d.api.all('task-suites', pool_id=pool_id)
    candidates = [s for s in suites if s['id'] != old['id'] and
                  [t['input_values'] for t in s['tasks']] == [t['input_values'] for t in tasks]]
    assert len(candidates) == 1
    new = candidates[0]
    assert new['overlap'] == 10 and len(new['tasks']) == 12
    assert [t['known_solutions'] for t in new['tasks']] == [t['known_solutions'] for t in tasks]
    d.api.request('PATCH', '/api/v1/task-suites/' + old['id'] + '/set-overlap-or-min',
                  body={'overlap': 0})
    retired = d.api.get('task-suites/' + old['id'])
    assert retired['overlap'] == 0 and retired['remaining_overlap'] == 0
    save_json(folder / 'retired_suite.json', retired)
    save_json(WORK / 'state/quality_suite_review_exam.json', new)
    save_json(folder / 'verification.json', {'pool_id': pool_id, 'old_suite_id': old['id'],
              'new_suite_id': new['id'], 'retired_overlap': 0, 'active_overlap': 10,
              'active_tasks': 12, 'corrected_photos': ['763734745', '763478388'],
              'accept_count': sum(t['known_solutions'][0]['output_values']['status'] == 'accept' for t in tasks),
              'status': d.api.get('pools/' + pool_id)['status']})
    print('Review exam: corrected page verified, 6 accept / 6 reject, old page retired, pool CLOSED.')


if __name__ == '__main__':
    main()
