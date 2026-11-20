"""Recreate never-started drafts on the current project version, preserving tasks.

No opening or moderation actions exist here. Old drafts are archived only after
their replacement is verified. The ledger permits safe resumption.
"""
import json
from build_projects import Drafts, WORK, PREFIX, save_json

LEDGER = WORK / 'state/schema_refresh.json'
KEYS = ['bbox_training', 'bbox_exam', 'bbox_main', 'review_training', 'review_exam', 'review_main']


def prepare():
    drafts = Drafts()
    if not LEDGER.exists():
        save_json(LEDGER, {'old': {k: drafts.state['objects'][k] for k in KEYS}, 'new': {}, 'archived': []})
    ledger = json.loads(LEDGER.read_text())
    for key in KEYS:
        collection = 'trainings' if key.endswith('training') else 'pools'
        old = drafts.api.get(collection + '/' + ledger['old'][key])
        assert old['status'] in ['CLOSED', 'ARCHIVED']
        assert not old.get('last_started')
        assert not drafts.api.all('assignments', pool_id=old['id'])
        save_json(WORK / 'state' / (key + '_before_refresh.json'), old)
        payload = json.loads((WORK / 'configs' / (key + '.json')).read_text())
        marker = 'private_name' if collection == 'trainings' else 'private_comment'
        payload[marker] += ' [v3]'
        if key == 'bbox_main':
            reference = json.loads((WORK / 'configs/review_main.json').read_text())
            payload.update(reward_per_assignment=3.2, mixer_config=reference['mixer_config'], quality_control=reference['quality_control'])
        if key not in ledger['new']:
            # Unique marker is checked before each non-idempotent creation.
            matches = [v for v in drafts.api.all(collection) if v.get(marker) == payload[marker]]
            assert len(matches) <= 1
            if matches:
                new = matches[0]
            else:
                new = drafts.api.post(collection, payload)
            ledger['new'][key] = str(new['id'])
            save_json(LEDGER, ledger)
        actual = drafts.api.get(collection + '/' + ledger['new'][key])
        assert actual['status'] == 'CLOSED' and actual['owner']['myself'] is True
        drafts.state['objects'][key] = str(actual['id'])
        save_json(drafts.state_path, drafts.state)
        save_json(WORK / 'configs' / (key + '.json'), payload)
        save_json(WORK / 'state' / (key + '.json'), actual)
        print(key, actual['id'], 'CLOSED', flush=True)
    # Keep original upload operation artifacts under their original pool ID.
    if not ledger.get('upload_prepared'):
        archive = WORK / 'state' / ('previous_upload_' + ledger['old']['bbox_main'])
        archive.mkdir(exist_ok=True)
        for path in (WORK / 'state').glob('bbox_upload*.json'):
            path.replace(archive / path.name)
        old_tasks = WORK / 'state/bbox_tasks.json'
        if old_tasks.exists():
            old_tasks.replace(archive / old_tasks.name)
        source = WORK / 'bbox_400_для_загрузки.jsonl'
        rows = [json.loads(s) for s in source.read_text().splitlines()]
        for row in rows:
            row['pool_id'] = ledger['new']['bbox_main']
        source.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
        ledger['upload_prepared'] = True
        save_json(LEDGER, ledger)


def archive_old():
    drafts = Drafts()
    ledger = json.loads(LEDGER.read_text())
    assert len(drafts.api.all('tasks', pool_id=ledger['new']['bbox_main'])) == 400
    assert drafts.api.get('pools/' + ledger['new']['bbox_main'])['status'] == 'CLOSED'
    for key in [k for k in KEYS if not k.endswith('training')] + [k for k in KEYS if k.endswith('training')]:
        collection = 'trainings' if key.endswith('training') else 'pools'
        target = collection + '/' + ledger['old'][key]
        actual = drafts.api.get(target)
        assert not drafts.api.all('assignments', pool_id=actual['id'])
        if actual['status'] != 'ARCHIVED':
            assert actual['status'] == 'CLOSED'
            operation = drafts.api.post(target + '/archive')
            if operation:
                save_json(WORK / 'state' / (key + '_archive_operation.json'), operation)
            actual = drafts.api.get(target)
            assert actual['status'] == 'ARCHIVED', 'Archive still in progress; rerun verification.'
        if key not in ledger['archived']:
            ledger['archived'].append(key)
        save_json(LEDGER, ledger)
        print(key, actual['id'], 'ARCHIVED', flush=True)


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'prepare':
        prepare()
    elif sys.argv[1] == 'archive-old':
        archive_old()
