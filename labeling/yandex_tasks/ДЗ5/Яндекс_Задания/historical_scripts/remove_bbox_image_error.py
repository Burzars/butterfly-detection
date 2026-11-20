"""Remove the technical answer from all bbox stages; keep all drafts closed."""
import copy
import hashlib
import json
import os
import signal
import subprocess
from build_projects import Drafts, WORK, save_json, clean_bbox_instructions as clean_instructions
from upload_quality_examples import perform_batch

FOLDER = WORK / 'state/bbox_two_answers'
FIELDS = ['public_name', 'public_description', 'public_instructions', 'private_comment',
          'task_spec', 'assignments_issuing_type', 'assignments_automerge_enabled']


def upload_once(drafts, key, collection, payload):
    """A successful saved operation is verified, never submitted again."""
    path = WORK/'state'/('quality_upload_'+key+'.json')
    if path.exists():
        record = json.loads(path.read_text())
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        assert digest == record['payload_sha256']
        assert record['pool_id'] == drafts.state['objects'][key]
        if record['status'] == 'SUCCESS':
            operation = drafts.api.get('operations/'+record['operation_id'])
            assert operation['status'] == 'SUCCESS'
            print(key, 'reusing successful saved operation; no upload repeated', flush=True)
            return
    perform_batch(drafts, key, collection, payload)


def clean_tb(view):
    view = copy.deepcopy(view)
    config = json.loads(view['config'])
    items = config['view']['items']
    config['view']['items'] = [n for n in items if not (
        n.get('type') == 'helper.if' and n.get('condition', {}).get('to') == 'image_error')]
    conditions = config['view']['validation']['conditions']
    conditions[:] = [c for c in conditions if 'image_error' not in json.dumps(c)]

    def walk(node):
        if isinstance(node, dict):
            if node.get('type') == 'field.radio-group' and node.get('data', {}).get('path') == 'status':
                node['options'] = [o for o in node['options'] if o['value'] != 'image_error']
                assert [o['value'] for o in node['options']] == ['objects', 'empty']
            if (node.get('type') == 'condition.not' and
                    node.get('condition', {}).get('to') == 'objects' and
                    node['condition'].get('data', {}).get('path') == 'status'):
                node.clear()
                node.update(type='condition.equals', data={'type': 'data.output', 'path': 'status'}, to='empty')
            if isinstance(node.get('hint'), str):
                node['hint'] = node['hint'].replace('при отсутствии бабочек или ошибке загрузки', 'при отсутствии бабочек')
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(config)
    conditions.append({'type': 'condition.any', 'conditions': [
        {'type': 'condition.equals', 'data': {'type': 'data.output', 'path': 'status'}, 'to': status}
        for status in ['objects', 'empty']], 'hint': 'Укажите, есть ли взрослые бабочки на фотографии.'})
    assert 'image_error' not in json.dumps(config)
    assert 'Изображение не загрузилось' not in json.dumps(config, ensure_ascii=False)
    # Keep the previously fixed empty-binding check in both validations.
    assert json.dumps(config).count('"type": "condition.empty", "data": {"type": "data.output", "path": "regions"}') == 2
    view['config'] = json.dumps(config, ensure_ascii=False)
    return view


def main():
    FOLDER.mkdir(exist_ok=True)
    d = Drafts()
    ledger_path = FOLDER/'migration.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {'new': {}}
    before_path = FOLDER/'before.json'
    if not before_path.exists():
        before = {'project': d.api.get('projects/11893'), 'stages': {}}
        for key in ['bbox_main', 'bbox_exam', 'bbox_training']:
            pool = d.api.get('pools/' + d.state['objects'][key])
            assert pool['status'] == 'CLOSED' and not pool.get('last_started')
            assert not d.api.all('assignments', pool_id=pool['id'])
            before['stages'][key] = {'pool': pool, 'tasks': d.api.all('tasks', pool_id=pool['id']),
                                     'suites': d.api.all('task-suites', pool_id=pool['id'])}
        save_json(before_path, before)
    before = json.loads(before_path.read_text())
    process = json.loads((WORK/'state/bbox_exam_control_process.json').read_text())
    command = subprocess.run(['ps', '-p', str(process['pid']), '-o', 'command='], capture_output=True, text=True).stdout
    if command.strip():
        assert 'bbox_exam_control.py --watch --apply' in command
        os.kill(process['pid'], signal.SIGTERM)
    project_payloads = {}
    for key in ['bbox_main', 'bbox_exam', 'bbox_training']:
        if key == 'bbox_training':
            template = before['project']
        else:
            template = json.loads((WORK/'state/bbox_clear_fix'/(key+'_project_fixed.json')).read_text())
        body = {k: copy.deepcopy(template[k]) for k in FIELDS if k in template}
        body['public_instructions'] = clean_instructions(body['public_instructions'])
        if key == 'bbox_training':
            ui = WORK/'qualification_ui'
            body['task_spec']['view_spec'].update(markup=(ui/'training.html').read_text(),
                script=(ui/'training.js').read_text(), styles=(ui/'training.css').read_text())
            assert 'image_error' not in body['task_spec']['view_spec']['markup']
            assert 'image_error' not in body['task_spec']['view_spec']['script']
        else:
            body['task_spec']['view_spec'] = clean_tb(body['task_spec']['view_spec'])
        project_payloads[key] = body
    try:
        for key in ['bbox_main', 'bbox_exam', 'bbox_training']:
            old = before['stages'][key]
            body = project_payloads[key]
            if key not in ledger['new']:
                d.api.request('PUT', '/api/v1/projects/11893', body=body)
                save_json(FOLDER/(key+'_project.json'), body)
                payload = {k: copy.deepcopy(v) for k, v in old['pool'].items()
                           if k not in ['id', 'status', 'created', 'owner', 'last_started', 'last_stopped', 'last_close_reason']}
                payload['private_comment'] = payload.get('private_comment', '') + ' [bbox-two-answers-20261118]'
                matches = [p for p in d.api.all('pools', project_id='11893') if p.get('private_comment') == payload['private_comment']]
                assert len(matches) <= 1
                pool = matches[0] if matches else d.api.post('pools', payload)
                assert pool['status'] == 'CLOSED'
                ledger['new'][key] = pool['id']
                save_json(ledger_path, ledger)
            pool_id = ledger['new'][key]
            assert d.api.get('pools/'+pool_id)['status'] == 'CLOSED'
            assert not d.api.all('assignments', pool_id=pool_id)
            upload_key = key+'_two_answers_20261118'
            d.state['objects'][upload_key] = pool_id
            if key == 'bbox_main':
                tasks = []
                assert len(old['tasks']) == 430
                for t in old['tasks']:
                    row = {k: copy.deepcopy(t[k]) for k in ['input_values', 'known_solutions', 'message_on_unknown_solution'] if k in t}
                    row['pool_id'] = pool_id
                    if t.get('infinite_overlap'):
                        row['infinite_overlap'] = True
                    else:
                        row['overlap'] = t.get('overlap', 1)
                    tasks.append(row)
                upload_once(d, upload_key, 'tasks', tasks)
                actual = d.api.all('tasks', pool_id=pool_id)
                assert len(actual) == 430
                expected = {t['input_values']['photo_id']: t for t in tasks}
                for t in actual:
                    e = expected[t['input_values']['photo_id']]
                    assert bool(t.get('infinite_overlap')) == bool(e.get('infinite_overlap'))
                    for field in ['input_values', 'known_solutions', 'message_on_unknown_solution', 'overlap']:
                        assert t.get(field) == e.get(field), (key, field)
                save_json(FOLDER/'bbox_main_tasks.json', actual)
            else:
                suites = []
                assert len(old['suites']) == (8 if key == 'bbox_training' else 1)
                for s in old['suites']:
                    row = {'pool_id': pool_id, 'tasks': [
                        {k: copy.deepcopy(t[k]) for k in ['input_values', 'known_solutions', 'message_on_unknown_solution'] if k in t}
                        for t in s['tasks']]}
                    if s.get('infinite_overlap'):
                        row['infinite_overlap'] = True
                    else:
                        row.update(infinite_overlap=False, overlap=s['overlap'])
                    if 'issuing_order_override' in s:
                        row['issuing_order_override'] = s['issuing_order_override']
                    suites.append(row)
                upload_once(d, upload_key, 'task-suites', suites)
                actual = d.api.all('task-suites', pool_id=pool_id)
                assert len(actual) == len(suites)
                expected = {s['tasks'][0]['input_values']['photo_id']: s for s in suites}
                for s in actual:
                    e = expected[s['tasks'][0]['input_values']['photo_id']]
                    for field in ['infinite_overlap', 'overlap', 'issuing_order_override']:
                        assert s.get(field) == e.get(field), (key, field)
                    assert [{k: t.get(k) for k in ['input_values', 'known_solutions', 'message_on_unknown_solution']} for t in s['tasks']] == [
                        {k: t.get(k) for k in ['input_values', 'known_solutions', 'message_on_unknown_solution']} for t in e['tasks']]
                save_json(FOLDER/(key+'_suites.json'), actual)
                if key == 'bbox_exam':
                    ledger['exam_suite_id'] = actual[0]['id']
                    save_json(WORK/'state/quality_suite_bbox_exam.json', actual[0])
                else:
                    save_json(WORK/'state/quality_suites_bbox_training.json', actual)
            pool = d.api.get('pools/'+pool_id)
            for field in ['filter', 'quality_control', 'mixer_config', 'defaults', 'reward_per_assignment', 'type',
                          'auto_accept_solutions', 'auto_accept_period_day', 'assignment_max_duration_seconds']:
                assert pool.get(field) == old['pool'].get(field), (key, field)
            save_json(WORK/'state'/(key+'.json'), pool)
            save_json(WORK/'configs'/(key+'.json'), {k: v for k, v in pool.items() if k not in ['id', 'owner', 'created', 'status']})
            save_json(ledger_path, ledger)
            print(key, pool_id, 'CLOSED, identical data and controls; two answers', flush=True)
    finally:
        d.api.request('PUT', '/api/v1/projects/11893', body=project_payloads['bbox_training'])
    actual_project = d.api.get('projects/11893')
    assert actual_project['task_spec'] == project_payloads['bbox_training']['task_spec']
    for name in ['bbox_learning_project', 'bbox_project']:
        save_json(WORK/'configs'/(name+'.json'), project_payloads['bbox_training'])
    save_json(WORK/'state/bbox_project.json', actual_project)
    state = json.loads(d.state_path.read_text())
    state['objects'].update(ledger['new'])
    save_json(d.state_path, state)
    for key in ['bbox_training', 'bbox_exam']:
        path = WORK/'configs'/(key+'_scoring.json')
        policy = json.loads(path.read_text())
        policy['pool_id'] = ledger['new'][key]
        if key == 'bbox_exam':
            policy['suite_id'] = ledger['exam_suite_id']
        save_json(path, policy)
    for name in ['bbox.html', 'bbox_published.html']:
        path = WORK/'instructions'/name
        path.write_text(clean_instructions(path.read_text()))
    ledger['pointers_updated'] = True
    save_json(ledger_path, ledger)


if __name__ == '__main__':
    main()
