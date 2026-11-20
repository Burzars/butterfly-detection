"""Correct four accepted/reference examples, retaining a full audit trail."""
import copy
import hashlib
import json
from build_projects import Drafts, WORK, save_json
from upload_quality_examples import perform_batch, inputs, known

FOLDER = WORK / 'state/remaining_antennae_repair'
FIXES = {
    '764083569': [(.1989451144, .2000799724, .720, .8467666060)],
    '762680274': [(.392, .287, .763, .550)],
    '763489544': [(.230, .080, .725, .887)],
    '667490055': [(.112, .214, .792, .901), (.290, 0, .792, .598)],
}
GROUPS = {'bbox_training': ['764083569'], 'review_training': ['763489544'],
          'review_main': ['762680274', '667490055']}
REPAIR_TAG = 'antennae_v2'


def active(t):
    return t.get('infinite_overlap') or t.get('overlap', 0) > 0


def prepare():
    FOLDER.mkdir(exist_ok=True)
    source = WORK.parent / 'Проверка_эталонов/annotations_reviewed.json'
    data = json.loads(source.read_text())
    if not (FOLDER / 'annotations_before.json').exists():
        (FOLDER / 'annotations_before.json').write_bytes(source.read_bytes())
    changes = []
    for e in data['items']:
        pid = str(e['photo_id'])
        if pid not in FIXES:
            continue
        native = [{'shape': 'rectangle', 'left': x, 'top': y, 'width': r-x, 'height': b-y}
                  for x, y, r, b in FIXES[pid]]
        internal = [{'type': 'rectangle', 'data': {'p1': {'x': x, 'y': y}, 'p2': {'x': r, 'y': b}}}
                    for x, y, r, b in FIXES[pid]]
        if e['gold']['regions'] != native:
            changes.append({'photo_id': pid, 'author': 'Codex', 'type': 'assistant_geometry_correction',
                            'reason': 'Include visible antennae, legs and wing tails; verdict unchanged.',
                            'before': copy.deepcopy(e['gold']['regions']), 'after': copy.deepcopy(native)})
        e['gold']['regions'] = native
        data['annotations'][pid]['gold_regions'] = copy.deepcopy(internal)
        if 'review' in e:
            assert e['review']['expected_status'] == 'accept'
            e['review']['regions'] = copy.deepcopy(native)
            data['annotations'][pid]['review_regions'] = copy.deepcopy(internal)
    save_json(source, data)
    source.with_suffix('.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False)+'\n' for e in data['items']))
    app = WORK.parent / 'Локальная_разметка/saved/annotations.json'
    state = json.loads(app.read_text())
    for pid in FIXES:
        for field in ('gold_regions', 'review_regions'):
            state['annotations'][pid][field] = copy.deepcopy(data['annotations'][pid][field])
    save_json(app, state)
    history_path = WORK.parent / 'Проверка_эталонов/changes.json'
    if changes:
        history = json.loads(history_path.read_text()); history.extend(changes)
        save_json(history_path, history); save_json(FOLDER / 'changes.json', changes)
    policy_path = WORK / 'configs/bbox_exam_scoring.json'
    policy = json.loads(policy_path.read_text())
    policy['reference_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    save_json(policy_path, policy)
    return {str(e['photo_id']): e for e in data['items']}


def main():
    rows = prepare()
    d = Drafts()
    report = {}
    for key, photos in GROUPS.items():
        pool_id = d.state['objects'][key]
        training = key.endswith('training')
        pool = d.api.get(('trainings/' if training else 'pools/') + pool_id)
        assert pool['status'] == 'CLOSED' and not pool.get('last_started')
        assert not d.api.all('assignments', pool_id=pool_id)
        old_path = FOLDER / (key + '_original_tasks.json')
        if not old_path.exists():
            tasks = [t for t in d.api.all('tasks', pool_id=pool_id)
                     if active(t) and str(t['input_values']['photo_id']) in photos]
            assert len(tasks) == len(photos)
            save_json(old_path, tasks)
        originals = json.loads(old_path.read_text())
        kind = 'bbox' if key.startswith('bbox') else 'review'
        payload = [{'pool_id': pool_id, 'input_values': inputs(rows[pid], kind, training),
                    'known_solutions': known(rows[pid], kind), 'infinite_overlap': True,
                    **({'message_on_unknown_solution': rows[pid]['training_hint']} if training else {})}
                   for pid in photos]
        operation_key = key + '_' + REPAIR_TAG
        d.state['objects'][operation_key] = pool_id
        perform_batch(d, operation_key, 'tasks', payload)
        tasks = d.api.all('tasks', pool_id=pool_id)
        original_ids = {t['id'] for t in originals}
        replacements = [t for t in tasks if t['id'] not in original_ids and
                        str(t['input_values']['photo_id']) in photos and active(t)]
        assert len(replacements) == len(photos)
        desired = {str(t['input_values']['photo_id']): t for t in payload}
        for t in replacements:
            expected = desired[str(t['input_values']['photo_id'])]
            assert t['input_values'] == expected['input_values']
            assert t['known_solutions'] == expected['known_solutions']
        for t in originals:
            d.api.request('PATCH', '/api/v1/tasks/' + t['id'] + '/set-overlap-or-min',
                          body={'overlap': 0})
        final = d.api.all('tasks', pool_id=pool_id)
        assert all(not active(t) for t in final if t['id'] in original_ids)
        final_active = [t for t in final if active(t)]
        assert len(final_active) == (8 if training else 30)
        assert len({t['input_values']['photo_id'] for t in final_active}) == len(final_active)
        save_json(WORK / 'state' / ('quality_tasks_' + key + '.json'), final_active)
        report[key] = {'pool_id': pool_id, 'photos': photos, 'active_tasks': len(final_active),
                       'retired_ids': sorted(original_ids), 'replacement_ids': [t['id'] for t in replacements],
                       'verified_inputs_and_keys': True, 'status': pool['status']}
        save_json(FOLDER / 'verification.json', report)
        print(key, 'corrected', photos, 'active', len(final_active), flush=True)
    print('All requested examples corrected; pools remain closed.', flush=True)


if __name__ == '__main__':
    main()
