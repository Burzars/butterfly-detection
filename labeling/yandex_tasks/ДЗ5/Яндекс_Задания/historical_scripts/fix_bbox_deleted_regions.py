"""Replace unused frozen bbox drafts; never open, moderate, pay, or grant skills."""
import copy
import json
import os
import signal
import subprocess
from pathlib import Path
from build_projects import Drafts, WORK, save_json
from upload_quality_examples import perform_batch

FOLDER = WORK / 'state/bbox_clear_fix'
LEDGER = FOLDER / 'migration.json'
FIELDS = ['public_name', 'public_description', 'public_instructions', 'private_comment',
          'task_spec', 'assignments_issuing_type', 'assignments_automerge_enabled']


def fixed_project(original, exam=False):
    body = {k: copy.deepcopy(original[k]) for k in FIELDS if k in original}
    spec = body['task_spec']
    spec['input_spec']['bbox_clear_version'] = {
        'type': 'string', 'required': True, 'hidden': True,
        'allowed_values': ['20261118']}
    if exam:
        spec['input_spec']['interface_variant'] = {
            'type': 'string', 'required': True, 'hidden': True,
            'allowed_values': ['qualification']}
    view = spec['view_spec']
    config = json.loads(view['config'])
    changed = []

    def walk(node):
        if isinstance(node, dict):
            if (node.get('type') == 'condition.schema' and
                    node.get('data', {}).get('path') == 'regions' and
                    node.get('schema') == {'type': 'array', 'maxItems': 0}):
                node.clear()
                node.update(type='condition.empty', data={'type': 'data.output', 'path': 'regions'},
                            hint='Для выбранного ответа удалите рамки.')
                changed.append(True)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(config)
    assert len(changed) == 2
    view['config'] = json.dumps(config, ensure_ascii=False)
    view['lock']['condition.empty'] = '1.0.0'
    view['settings']['showSkip'] = not exam
    return body


def main():
    d = Drafts()
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {'new': {}}
    original = json.loads((WORK/'state/skip_stage_separation/before.json').read_text())['data']['bbox_project']['object']
    current = json.loads((FOLDER/'original_current_project.json').read_text())
    if 'before' not in ledger:
        ledger['before'] = {}
        for key in ['bbox_main', 'bbox_exam']:
            pool_id = d.state['objects'][key]
            pool = d.api.get('pools/' + pool_id)
            assert pool['status'] == 'CLOSED' and not pool.get('last_started')
            assert not d.api.all('assignments', pool_id=pool_id)
            data = {'pool': pool, 'tasks': d.api.all('tasks', pool_id=pool_id)}
            if key == 'bbox_exam':
                data['suites'] = d.api.all('task-suites', pool_id=pool_id)
                assert len(data['suites']) == 1 and len(data['suites'][0]['tasks']) == 8
            else:
                assert len(data['tasks']) == 430
                assert sum(bool(t.get('known_solutions')) for t in data['tasks']) == 30
            ledger['before'][key] = data
        save_json(LEDGER, ledger)
    process_path = WORK/'state/bbox_exam_control_process.json'
    process = json.loads(process_path.read_text())
    command = subprocess.run(['ps', '-p', str(process['pid']), '-o', 'command='], capture_output=True, text=True).stdout
    if command.strip():
        assert 'bbox_exam_control.py --watch --apply' in command
        os.kill(process['pid'], signal.SIGTERM)
    try:
        for key in ['bbox_main', 'bbox_exam']:
            old = ledger['before'][key]
            if key not in ledger['new']:
                body = fixed_project(original, exam=key == 'bbox_exam')
                d.api.request('PUT', '/api/v1/projects/11893', body=body)
                save_json(FOLDER/(key+'_project_fixed.json'), body)
                payload = {k: copy.deepcopy(v) for k, v in old['pool'].items()
                           if k not in ['id', 'status', 'created', 'owner', 'last_started', 'last_stopped', 'last_close_reason']}
                payload['private_comment'] = payload.get('private_comment', '') + ' [bbox-clear-fix-20261118]'
                matches = [p for p in d.api.all('pools', project_id='11893')
                           if p.get('private_comment') == payload['private_comment']]
                assert len(matches) <= 1
                pool = matches[0] if matches else d.api.post('pools', payload)
                assert pool['status'] == 'CLOSED'
                ledger['new'][key] = pool['id']
                save_json(LEDGER, ledger)
            new_id = ledger['new'][key]
            assert d.api.get('pools/' + new_id)['status'] == 'CLOSED'
            assert not d.api.all('assignments', pool_id=new_id)
            tasks = []
            source = old['suites'][0]['tasks'] if key == 'bbox_exam' else old['tasks']
            for task in source:
                row = {k: copy.deepcopy(task[k]) for k in ['input_values', 'known_solutions', 'message_on_unknown_solution'] if k in task}
                row['input_values']['bbox_clear_version'] = '20261118'
                if key == 'bbox_main':
                    row.update(pool_id=new_id)
                    if task.get('infinite_overlap'):
                        row['infinite_overlap'] = True
                    else:
                        row['overlap'] = task.get('overlap', 1)
                tasks.append(row)
            upload_key = key + '_clear_fix_20261118_v2'
            d.state['objects'][upload_key] = new_id
            if key == 'bbox_main':
                perform_batch(d, upload_key, 'tasks', tasks)
                actual = d.api.all('tasks', pool_id=new_id)
                assert len(actual) == 430
                by_photo = {t['input_values']['photo_id']: t for t in actual}
                for wanted in tasks:
                    got = by_photo[wanted['input_values']['photo_id']]
                    assert got['input_values'] == wanted['input_values']
                    assert got.get('known_solutions', []) == wanted.get('known_solutions', [])
                    assert got.get('message_on_unknown_solution') == wanted.get('message_on_unknown_solution')
                    assert got.get('infinite_overlap', False) == wanted.get('infinite_overlap', False)
                save_json(FOLDER/'bbox_main_tasks.json', actual)
            else:
                suite = {'pool_id': new_id, 'tasks': tasks, 'overlap': old['suites'][0]['overlap'], 'infinite_overlap': False}
                perform_batch(d, upload_key, 'task-suites', [suite])
                actual = d.api.all('task-suites', pool_id=new_id)
                assert len(actual) == 1 and len(actual[0]['tasks']) == 8 and actual[0]['overlap'] == 10
                assert [t['input_values'] for t in actual[0]['tasks']] == [t['input_values'] for t in tasks]
                assert [t.get('known_solutions', []) for t in actual[0]['tasks']] == [t.get('known_solutions', []) for t in tasks]
                ledger['exam_suite_id'] = actual[0]['id']
                save_json(WORK/'state/quality_suite_bbox_exam.json', actual[0])
            save_json(LEDGER, ledger)
            pool = d.api.get('pools/' + new_id)
            for k in ['filter', 'quality_control', 'mixer_config', 'reward_per_assignment', 'defaults',
                      'auto_accept_solutions', 'auto_accept_period_day', 'assignment_max_duration_seconds']:
                assert pool.get(k) == old['pool'].get(k), (key, k)
            save_json(WORK/'state'/(key+'.json'), pool)
            config = {k: v for k, v in pool.items() if k not in ['id', 'owner', 'created', 'status']}
            save_json(WORK/'configs'/(key+'.json'), config)
            print(key, new_id, 'CLOSED, data and settings preserved', flush=True)
    finally:
        body = {k: current[k] for k in FIELDS if k in current}
        d.api.request('PUT', '/api/v1/projects/11893', body=body)
        assert d.api.get('projects/11893')['task_spec'] == current['task_spec']
    state = json.loads(d.state_path.read_text())
    state['objects'].update(ledger['new'])
    save_json(d.state_path, state)
    policy_path = WORK/'configs/bbox_exam_scoring.json'
    policy = json.loads(policy_path.read_text())
    policy.update(pool_id=ledger['new']['bbox_exam'], suite_id=ledger['exam_suite_id'])
    save_json(policy_path, policy)
    ledger['pointers_updated'] = True
    save_json(LEDGER, ledger)


if __name__ == '__main__':
    main()
