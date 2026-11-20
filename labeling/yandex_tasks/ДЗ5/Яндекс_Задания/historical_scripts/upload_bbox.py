"""Upload the fixed 400-image set into the existing closed bbox pool."""
import json
import time
import uuid
from pathlib import Path
from build_projects import Drafts, WORK, APIError, save_json


def task_map(tasks):
    result = {}
    for task in tasks:
        photo_id = task['input_values']['photo_id']
        if photo_id in result:
            raise RuntimeError('Повторное задание для photo_id ' + photo_id)
        result[photo_id] = task
    return result


def main():
    drafts = Drafts()
    pool_id = drafts.state['objects']['bbox_main']
    pool = drafts.api.get('pools/' + pool_id)
    assert pool['status'] == 'CLOSED', 'Пул должен оставаться закрытым.'
    assert pool.get('last_started') is None
    source = WORK / 'bbox_400_для_загрузки.jsonl'
    tasks = [json.loads(line) for line in source.read_text().splitlines()]
    expected = {task['input_values']['photo_id']: task for task in tasks}
    assert len(tasks) == len(expected) == 400
    assert all(task['pool_id'] == pool_id and task['overlap'] == 1 for task in tasks)
    existing = task_map(drafts.api.all('tasks', pool_id=pool_id))
    assert set(existing) <= set(expected), 'В пуле найдены неизвестные задания.'
    for photo_id, task in existing.items():
        assert task['input_values'] == expected[photo_id]['input_values']
        assert task['overlap'] == 1 and not task.get('infinite_overlap')
    ledger_path = WORK / 'state/bbox_upload.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else None
    if len(existing) < 400:
        if ledger is None:
            missing = [task for task in tasks if task['input_values']['photo_id'] not in existing]
            save_json(WORK / 'state/bbox_upload_payload.json', missing)
            ledger = {'operation_id': str(uuid.uuid4()), 'status': 'PENDING', 'count': len(missing)}
            save_json(ledger_path, ledger)
        if ledger['status'] == 'SUCCESS':
            raise RuntimeError('Загрузка завершилась, но не все задания видны. Повторите чтение позднее.')
        payload = json.loads((WORK / 'state/bbox_upload_payload.json').read_text())
        try:
            response = drafts.api.post('tasks', payload, async_mode='true', skip_invalid_items='false',
                                       open_pool='false', operation_id=ledger['operation_id'])
        except APIError as error:
            if error.status != 409:
                raise
            response = drafts.api.get('operations/' + ledger['operation_id'])
        operation_id = response['id']
        assert operation_id == ledger['operation_id']
        for _ in range(30):
            operation = drafts.api.get('operations/' + operation_id)
            save_json(WORK / 'state/bbox_upload_operation.json', operation)
            if operation['status'] == 'SUCCESS':
                details = operation.get('details') or {}
                assert not any(details.get(key, 0) for key in ['not_valid_count','invalid_count','failed_count']), details
                assert details.get('success_count', len(payload)) == len(payload), details
                ledger['status'] = 'SUCCESS'
                save_json(ledger_path, ledger)
                break
            if operation['status'] == 'FAIL':
                raise RuntimeError('Ошибка операции загрузки: ' + str(operation.get('details')))
            time.sleep(1)
        else:
            raise RuntimeError('Операция ещё выполняется. Её ID сохранён для безопасного продолжения.')
    actual = task_map(drafts.api.all('tasks', pool_id=pool_id))
    assert set(actual) == set(expected)
    assert all(actual[key]['input_values'] == expected[key]['input_values'] for key in expected)
    assert all(actual[key]['overlap'] == 1 and not actual[key].get('infinite_overlap') for key in expected)
    assert not drafts.api.all('assignments', pool_id=pool_id)
    pool = drafts.api.get('pools/' + pool_id)
    assert pool['status'] == 'CLOSED' and pool.get('last_started') is None
    account = drafts.api.get('requester')
    assert account['balance'] == 850
    save_json(WORK / 'state/bbox_tasks.json', list(actual.values()))
    save_json(WORK / 'state/bbox_main.json', pool)
    save_json(WORK / 'state/bbox_upload_verified.json', {
        'pool_id': pool_id, 'tasks': len(actual), 'status': pool['status'],
        'balance': account['balance'], 'assignments': 0, 'verified_date': '2026-11-18',
    })
    print('Пул', pool_id, '| фотографий',len(actual),'| статус',pool['status'],'| ответов 0 | баланс',account['balance'],flush=True)


if __name__ == '__main__':
    main()
