"""Two review verdicts; never opens pools or requests moderation."""
import copy
import json
from build_projects import Drafts, WORK, save_json, clean_review_instructions
from remove_bbox_image_error import upload_once

FOLDER = WORK/'state/review_two_answers'
FIELDS = ['public_name', 'public_description', 'public_instructions', 'private_comment',
          'task_spec', 'assignments_issuing_type', 'assignments_automerge_enabled']
DEFAULTS = {'interface_variant': 'qualification', 'review_work_ui': 'two_answers_v1'}


def clean_view(original):
    view = copy.deepcopy(original)
    c = json.loads(view['config'])
    c['view']['items'] = [x for x in c['view']['items'] if not (
        x.get('type') == 'helper.if' and x.get('condition', {}).get('to') == 'image_error')]
    conditions = c['view']['validation']['conditions']
    conditions[:] = [x for x in conditions if 'image_error' not in json.dumps(x)]
    radio = [x for x in c['view']['items'] if x.get('type') == 'field.radio-group']
    assert len(radio) == 1
    radio[0]['options'] = [x for x in radio[0]['options'] if x['value'] != 'image_error']
    assert [x['value'] for x in radio[0]['options']] == ['accept', 'reject']
    conditions.append({'type': 'condition.any', 'conditions': [
        {'type': 'condition.equals', 'data': {'type': 'data.output', 'path': 'status'}, 'to': status}
        for status in ['accept', 'reject']], 'hint': 'Выберите: разметка верна или требует исправления.'})
    assert 'image_error' not in json.dumps(c)
    # The original frames remain read-only; rejection reasons/comment remain optional.
    original_c = json.loads(original['config'])
    old_frame = next(x for x in original_c['view']['items'] if x.get('type') == 'field.image-annotation')
    new_frame = next(x for x in c['view']['items'] if x.get('type') == 'field.image-annotation')
    assert old_frame == new_frame and new_frame['disabled'] is True
    old_reject = [x for x in original_c['view']['items'] if x.get('condition', {}).get('to') == 'reject']
    new_reject = [x for x in c['view']['items'] if x.get('condition', {}).get('to') == 'reject']
    assert old_reject == new_reject and len(new_reject) == 2
    view['config'] = json.dumps(c, ensure_ascii=False)
    return view


def main():
    d = Drafts()
    before = json.loads((FOLDER/'before.json').read_text())
    original = json.loads((FOLDER/'project_before.json').read_text())
    path = FOLDER/'migration.json'
    ledger = json.loads(path.read_text()) if path.exists() else {}
    for key in ['review_training', 'review_exam', 'review_main']:
        collection = 'trainings' if key == 'review_training' else 'pools'
        old_id = before[key]['object']['id']
        p = d.api.get(collection+'/'+old_id)
        assert p['status'] in (['CLOSED', 'ARCHIVED'] if key == 'review_main' else ['CLOSED'])
        assert not d.api.all('assignments', pool_id=old_id)
    qualification = {k: copy.deepcopy(original[k]) for k in FIELDS if k in original}
    qualification['task_spec']['view_spec'] = clean_view(original['task_spec']['view_spec'])
    qualification['public_instructions'] = clean_review_instructions(qualification['public_instructions'])
    assert 'не могу проверить' not in qualification['public_instructions']
    assert qualification['task_spec']['input_spec'] == original['task_spec']['input_spec']
    assert qualification['task_spec']['output_spec'] == original['task_spec']['output_spec']
    if not ledger.get('qualification_updated'):
        snapshots = d.api.request('GET', '/api/new/requester/projects/11894/snapshots')
        assert snapshots[0]['snapshotMajorVersion'] == 3
        d.api.request('PUT', '/api/v1/projects/11894', body=qualification)
        snapshots = d.api.request('GET', '/api/new/requester/projects/11894/snapshots')
        assert snapshots[0]['snapshotMajorVersion'] == 3, 'Qualification must update the existing major version'
        ledger['qualification_updated'] = True
        ledger['qualification_version'] = [snapshots[0]['snapshotMajorVersion'], snapshots[0]['snapshotMinorVersion']]
        save_json(FOLDER/'qualification_project.json', qualification)
        save_json(path, ledger)
        print('Existing training/exam updated in major version 3', flush=True)
    try:
        if not ledger.get('main_pool_id'):
            work = copy.deepcopy(qualification)
            work['task_spec']['input_spec']['review_work_ui'] = {
                'type': 'string', 'required': True, 'hidden': True, 'allowed_values': ['two_answers_v1']}
            work['task_spec']['view_spec']['settings']['showSkip'] = True
            d.api.request('PUT', '/api/v1/projects/11894', body=work)
            save_json(FOLDER/'main_project.json', work)
            old = before['review_main']['object']
            payload = {k: copy.deepcopy(v) for k, v in old.items() if k not in [
                'id', 'owner', 'created', 'status', 'last_started', 'last_stopped', 'last_close_reason']}
            payload['private_comment'] += ' [review-two-answers-20261118]'
            matches = [p for p in d.api.all('pools', project_id='11894')
                       if p.get('private_comment') == payload['private_comment']]
            assert len(matches) <= 1
            new = matches[0] if matches else d.api.post('pools', payload)
            assert new['status'] == 'CLOSED'
            ledger['main_pool_id'] = new['id']
            save_json(path, ledger)
        pool_id = ledger['main_pool_id']
        assert d.api.get('pools/'+pool_id)['status'] == 'CLOSED'
        assert not d.api.all('assignments', pool_id=pool_id)
        source = [x for x in before['review_main']['tasks'] if x.get('infinite_overlap') or x.get('overlap', 0) > 0]
        assert len(source) == 30 and all(x.get('known_solutions') for x in source)
        tasks = []
        for t in source:
            row = {k: copy.deepcopy(t[k]) for k in ['input_values', 'known_solutions', 'message_on_unknown_solution'] if k in t}
            row.update(pool_id=pool_id, infinite_overlap=True)
            row['input_values'].update(DEFAULTS)
            assert set(row['known_solutions'][0]['output_values']) == {'status'}
            assert row['known_solutions'][0]['output_values']['status'] in ['accept', 'reject']
            tasks.append(row)
        key = 'review_main_two_answers_20261118'
        d.state['objects'][key] = pool_id
        upload_once(d, key, 'tasks', tasks)
        actual = d.api.all('tasks', pool_id=pool_id)
        assert len(actual) == 30
        expected = {t['input_values']['photo_id']: t for t in tasks}
        for t in actual:
            e = expected[t['input_values']['photo_id']]
            for k in ['input_values', 'known_solutions', 'message_on_unknown_solution', 'infinite_overlap']:
                assert t.get(k) == e.get(k), k
        p = d.api.get('pools/'+pool_id)
        for k in ['filter', 'quality_control', 'mixer_config', 'defaults', 'reward_per_assignment',
                  'assignment_max_duration_seconds', 'auto_accept_solutions']:
            assert p.get(k) == before['review_main']['object'].get(k), k
        save_json(WORK/'state/quality_tasks_review_main.json', actual)
        save_json(WORK/'state/review_main.json', p)
        save_json(WORK/'configs/review_main.json', {k: v for k, v in p.items() if k not in ['id', 'owner', 'created', 'status']})
        save_json(WORK/'state/review_main_ui_defaults.json', DEFAULTS)
        print('New main draft', pool_id, 'CLOSED; 30 exact controls; no work photos', flush=True)
    finally:
        current = d.api.get('projects/11894')
        if current['task_spec'] != qualification['task_spec'] or current['public_instructions'] != qualification['public_instructions']:
            d.api.request('PUT', '/api/v1/projects/11894', body=qualification)
    current = d.api.get('projects/11894')
    assert current['task_spec'] == qualification['task_spec']
    save_json(WORK/'state/review_project.json', current)
    save_json(WORK/'configs/review_project.json', qualification)
    state = json.loads(d.state_path.read_text())
    state['objects']['review_main'] = ledger['main_pool_id']
    save_json(d.state_path, state)
    for name in ['review.html', 'review_published.html']:
        f = WORK/'instructions'/name
        f.write_text(clean_review_instructions(f.read_text()))
    ledger['pointers_updated'] = True
    save_json(path, ledger)


if __name__ == '__main__':
    main()
