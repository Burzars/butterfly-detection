"""Upload reviewed examples without opening pools or issuing any paid work."""
import copy
import hashlib
import json
import random
import time
import uuid
from pathlib import Path
from build_projects import Drafts, WORK, APIError, save_json

SOURCE = WORK.parent / 'Проверка_эталонов/annotations_reviewed.json'
EXPECTED = {'bbox_training': 8, 'review_training': 8, 'bbox_exam': 8, 'review_exam': 12,
            'bbox_main': 30, 'review_main': 30}


def photo_id(task):
    return str(task['input_values']['photo_id'])


def verify_task(actual, desired):
    assert actual['input_values'] == desired['input_values'], photo_id(desired)
    assert actual.get('known_solutions', []) == desired.get('known_solutions', []), photo_id(desired)
    assert actual.get('message_on_unknown_solution') == desired.get('message_on_unknown_solution')


def perform_batch(drafts, key, collection, payload):
    path = WORK / 'state' / ('quality_upload_' + key + '.json')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if path.exists():
        ledger = json.loads(path.read_text())
        assert ledger['payload_sha256'] == digest, 'Payload changed; audit existing operation before retrying.'
    else:
        ledger = {'operation_id': str(uuid.uuid4()), 'status': 'PENDING', 'payload_sha256': digest,
                  'pool_id': drafts.state['objects'][key], 'collection': collection, 'count': len(payload)}
        save_json(path, ledger)
        save_json(WORK / 'state' / ('quality_payload_' + key + '.json'), payload)
    try:
        response = drafts.api.post(collection, payload, async_mode='true', skip_invalid_items='false',
                                   open_pool='false', operation_id=ledger['operation_id'])
    except APIError as error:
        if error.status != 409:
            raise
        response = drafts.api.get('operations/' + ledger['operation_id'])
    assert response['id'] == ledger['operation_id']
    for _ in range(30):
        operation = drafts.api.get('operations/' + ledger['operation_id'])
        save_json(WORK / 'state' / ('quality_operation_' + key + '.json'), operation)
        if operation['status'] == 'SUCCESS':
            ledger['status'] = 'SUCCESS'
            save_json(path, ledger)
            return
        if operation['status'] == 'FAIL':
            raise RuntimeError('Upload failed; inspect saved operation: ' + key)
        time.sleep(1)
    raise RuntimeError('Operation pending; rerun to resume safely: ' + key)


def configure_pools(drafts):
    objects = drafts.state['objects']
    for kind in ['bbox', 'review']:
        other = 'review' if kind == 'bbox' else 'bbox'
        for stage in ['exam', 'main']:
            key = kind + '_' + stage
            current = drafts.api.get('pools/' + objects[key])
            assert current['status'] == 'CLOSED' and not current.get('last_started')
            assert not drafts.api.all('assignments', pool_id=current['id'])
            path = WORK / 'configs' / (key + '.json')
            desired = json.loads(path.read_text())
            filters = desired['filter']['and']
            filters[:] = [f for f in filters if str(f.get('key')) != objects[other + '_skill']]
            # A qualified reviewer cannot annotate, and vice versa. The 30
            # control photos can therefore be reused without cross-role exposure.
            filters.append({'category': 'skill', 'key': objects[other + '_skill'], 'operator': 'EQ', 'value': None})
            quality = desired.setdefault('quality_control', {})
            if stage == 'exam':
                quality['training_requirement'] = {'training_pool_id': objects[kind + '_training'],
                                                   'training_passing_skill_value': 0}
                if kind == 'review':
                    desired['public_description'] += ('' if 'Оценивается только вердикт' in desired['public_description'] else
                                                      ' Оценивается только вердикт; причины ошибок необязательны и на допуск не влияют.')
            else:
                desired['mixer_config'].update(min_golden_tasks_count=1, mix_tasks_in_creation_order=False,
                                                shuffle_tasks_in_task_suite=True)
            save_json(path, desired)
            drafts.api.request('PUT', '/api/v1/pools/' + objects[key], body=desired)
            actual = drafts.api.get('pools/' + objects[key])
            assert actual['status'] == 'CLOSED' and actual['filter'] == desired['filter']
            if stage == 'exam':
                assert actual['quality_control']['training_requirement'] == quality['training_requirement']
            save_json(WORK / 'state' / (key + '.json'), actual)
            print(key, 'filters/config verified', flush=True)


def inputs(entry, kind, training=False, exam=False):
    values = {'photo_id': str(entry['photo_id']), 'image': entry['image']}
    if training or exam:
        values['interface_variant'] = 'qualification'
    if kind == 'review':
        values.update(regions=entry['review']['regions'], annotation_id='prepared-' + str(entry['photo_id']))
    if training:
        values['training_hint'] = entry['training_hint']
        if kind == 'bbox':
            values.update(show_reference=True, reference_regions=entry['gold']['regions'])
    return values


def known(entry, kind):
    answer = entry['gold']['status'] if kind == 'bbox' else entry['review']['expected_status']
    return [{'output_values': {'status': answer}, 'correctness_weight': 1}]


def upload_tasks(drafts, key, payload):
    pool_id = drafts.state['objects'][key]
    existing = [t for t in drafts.api.all('tasks', pool_id=pool_id)
                if t.get('infinite_overlap') or t.get('overlap', 0) > 0]
    if key.endswith('main'):
        existing = [t for t in existing if t.get('known_solutions') and
                    (t.get('infinite_overlap') or t.get('overlap', 0) > 0)]
    index = {photo_id(t): t for t in existing}
    assert len(index) == len(existing), 'Duplicate prepared photo IDs in ' + key
    desired = {photo_id(t): t for t in payload}
    assert len(desired) == EXPECTED[key]
    assert set(index) <= set(desired), 'Unexpected prepared tasks in ' + key
    for identifier, task in index.items():
        verify_task(task, desired[identifier])
    missing = [t for t in payload if photo_id(t) not in index]
    if missing:
        perform_batch(drafts, key, 'tasks', missing)
    actual = [t for t in drafts.api.all('tasks', pool_id=pool_id)
              if t.get('infinite_overlap') or t.get('overlap', 0) > 0]
    if key.endswith('main'):
        actual = [t for t in actual if t.get('known_solutions') and
                  (t.get('infinite_overlap') or t.get('overlap', 0) > 0)]
    assert len(actual) == EXPECTED[key]
    for task in actual:
        verify_task(task, desired[photo_id(task)])
        assert task['infinite_overlap'] is True
        assert set(task['known_solutions'][0]['output_values']) == {'status'}
    save_json(WORK / 'state' / ('quality_tasks_' + key + '.json'), actual)
    print(key, len(actual), 'tasks verified', flush=True)


def upload_exam(drafts, key, tasks):
    pool_id = drafts.state['objects'][key]
    payload = {'pool_id': pool_id, 'tasks': tasks, 'overlap': 10, 'infinite_overlap': False}
    suites = [s for s in drafts.api.all('task-suites', pool_id=pool_id)
              if s.get('infinite_overlap') or s.get('overlap', 0) > 0]
    assert len(suites) <= 1, 'More than one exam suite: ' + key
    if not suites:
        perform_batch(drafts, key, 'task-suites', [payload])
        suites = [s for s in drafts.api.all('task-suites', pool_id=pool_id)
                  if s.get('infinite_overlap') or s.get('overlap', 0) > 0]
    assert len(suites) == 1
    suite = suites[0]
    assert len(suite['tasks']) == EXPECTED[key] and suite['overlap'] == 10 and not suite.get('infinite_overlap')
    expected = {photo_id(t): t for t in tasks}
    assert set(expected) == {photo_id(t) for t in suite['tasks']}
    for task in suite['tasks']:
        verify_task(task, expected[photo_id(task)])
    save_json(WORK / 'state' / ('quality_suite_' + key + '.json'), suite)
    print(key, len(tasks), 'exam tasks in one suite, overlap 10, verified', flush=True)


def main():
    if (WORK/'configs/bbox_training_scoring.json').exists():
        raise RuntimeError('BBox now uses first-attempt learning. Use stage-specific setup; this uploader is for historical TB stages.')
    drafts = Drafts()
    source = json.loads(SOURCE.read_text())
    assert source['completed'] == source['total'] == len(source['items']) == 66
    for kind in ['bbox', 'review']:
        project = drafts.api.get('projects/' + drafts.state['objects'][kind + '_project'])
        mandatory = {k for k, v in project['task_spec']['output_spec'].items() if v.get('required')}
        assert mandatory == {'status'}, 'Native control must compare status only: ' + str(mandatory)
    configure_pools(drafts)
    for kind, training_section, exam_section in [('bbox', '01_bbox_training', '02_bbox_exam'),
                                                  ('review', '03_review_training', '04_review_exam')]:
        key = kind + '_training'
        rows = [e for e in source['items'] if e['section'] == training_section]
        payload = [{'pool_id': drafts.state['objects'][key], 'input_values': inputs(e, kind, training=True),
                    'known_solutions': known(e, kind), 'message_on_unknown_solution': e['training_hint'],
                    'infinite_overlap': True} for e in rows]
        assert all(t['message_on_unknown_solution'].strip() for t in payload)
        upload_tasks(drafts, key, payload)
        key = kind + '_exam'
        rows = [e for e in source['items'] if e['section'] == exam_section]
        tasks = [{'input_values': inputs(e, kind, exam=True), **({'known_solutions': known(e, kind)} if kind == 'review' else {})} for e in rows]
        random.Random(20261118).shuffle(tasks)
        upload_exam(drafts, key, tasks)
        key = kind + '_main'
        rows = [e for e in source['items'] if e['section'] == '05_review_control']
        payload = [{'pool_id': drafts.state['objects'][key], 'input_values': inputs(e, kind),
                    'known_solutions': known(e, kind), 'infinite_overlap': True} for e in rows]
        assert all('training_hint' not in t['input_values'] and 'reference_regions' not in t['input_values'] for t in payload)
        upload_tasks(drafts, key, payload)
    for key in EXPECTED:
        collection = 'trainings' if key.endswith('training') else 'pools'
        obj = drafts.api.get(collection + '/' + drafts.state['objects'][key])
        assert obj['status'] == 'CLOSED' and not obj.get('last_started')
        assert not drafts.api.all('assignments', pool_id=obj['id'])
    real = [t for t in drafts.api.all('tasks', pool_id=drafts.state['objects']['bbox_main']) if not t.get('known_solutions')]
    assert len(real) == 400
    account = drafts.api.get('requester')
    assert account['balance'] == 850
    save_json(WORK / 'state/quality_upload_verified.json', {
        'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(), 'unique_photos': 66,
        'uploaded_counts': EXPECTED, 'shared_control_photos': 30, 'cross_role_exclusion': True,
        'review_scoring': 'status_only', 'paid_assignments': 0, 'balance': account['balance'],
        'status': 'all_closed', 'bbox_exam_scoring': 'manual/external; no exact-coordinate golden check',
    })
    print('Все наборы загружены и проверены. Пулы CLOSED, ответов 0, баланс 850.', flush=True)


if __name__ == '__main__':
    main()
