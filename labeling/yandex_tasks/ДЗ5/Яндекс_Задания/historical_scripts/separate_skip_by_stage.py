"""Keep work pools on their skip-enabled snapshot; replace qualification drafts.

Never opens pools, submits moderation, pays, or assigns worker skills.
"""
import copy
import json
import sys
from build_projects import Drafts, WORK, save_json
from upload_quality_examples import perform_batch

FOLDER = WORK / 'state/skip_stage_separation'
LEDGER = FOLDER / 'migration.json'
QUALIFICATION = ['bbox_training', 'bbox_exam', 'review_training', 'review_exam']


def load():
    before = json.loads((FOLDER/'before.json').read_text())
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {'new': {}, 'archived': []}
    return Drafts(), before, ledger


def prepare_projects():
    d, before, ledger = load()
    for kind in ['bbox', 'review']:
        old = before['data'][kind+'_project']['object']
        body = {k: copy.deepcopy(old[k]) for k in ['public_name','public_description','private_comment',
                'public_instructions','task_spec','assignments_issuing_type','assignments_automerge_enabled']}
        body['task_spec']['input_spec']['interface_variant'] = {
            'type':'string','required':True,'hidden':True,'allowed_values':['qualification']}
        body['task_spec']['view_spec']['settings']['showSkip'] = False
        d.api.request('PUT','/api/v1/projects/'+old['id'],body=body)
        actual = d.api.get('projects/'+old['id'])
        assert actual['task_spec']['view_spec']['settings']['showSkip'] is False
        assert actual['task_spec']['view_spec']['config'] == old['task_spec']['view_spec']['config']
        assert actual['task_spec']['output_spec'] == old['task_spec']['output_spec']
        save_json(WORK/'configs'/(kind+'_project.json'),body)
        save_json(WORK/'state'/(kind+'_project.json'),actual)
    print('Qualification project snapshots prepared; main pool IDs unchanged.',flush=True)


def migrate():
    d, before, ledger = load()
    rows = json.loads((WORK.parent/'Проверка_эталонов/annotations_reviewed.json').read_text())['items']
    slots = {str(e['photo_id']):e['slot'] for e in rows}
    for key in QUALIFICATION:
        training = key.endswith('training')
        collection = 'trainings' if training else 'pools'
        old = before['data'][key]['object']
        config_path = WORK/'configs'/(key+'.json')
        payload = json.loads(config_path.read_text())
        marker = 'private_name' if training else 'private_comment'
        if '[no-skip-v4]' not in payload[marker]:
            payload[marker] += ' [no-skip-v4]'
        if not training:
            training_key = key.replace('_exam','_training')
            payload['quality_control']['training_requirement']['training_pool_id'] = ledger['new'][training_key]
        if key not in ledger['new']:
            matches = [p for p in d.api.all(collection) if p.get(marker)==payload[marker]]
            assert len(matches)<=1
            new = matches[0] if matches else d.api.post(collection,payload)
            assert new['status']=='CLOSED'
            ledger['new'][key] = str(new['id']); save_json(LEDGER,ledger)
        new_id = ledger['new'][key]
        assert d.api.get(collection+'/'+new_id)['status']=='CLOSED'
        assert not d.api.all('assignments',pool_id=new_id)
        old_tasks = (before['data'][key]['tasks'] if training else before['data'][key]['suites'][0]['tasks'])
        if training:
            old_tasks = sorted(old_tasks,key=lambda t:slots[str(t['input_values']['photo_id'])])
        tasks = []
        for t in old_tasks:
            row = {k:copy.deepcopy(t[k]) for k in ['input_values','known_solutions','message_on_unknown_solution'] if k in t}
            row['input_values']['interface_variant']='qualification'
            if training:
                row.update(pool_id=new_id,infinite_overlap=True)
            tasks.append(row)
        operation_key = key+'_noskip_v4'
        d.state['objects'][operation_key]=new_id
        if training:
            perform_batch(d,operation_key,'tasks',tasks)
            actual_tasks = d.api.all('tasks',pool_id=new_id)
            assert len(actual_tasks)==8
            expected = {t['input_values']['photo_id']:t for t in tasks}
            for t in actual_tasks:
                wanted = expected[t['input_values']['photo_id']]
                assert t['input_values']==wanted['input_values'] and t['known_solutions']==wanted['known_solutions']
                assert t['message_on_unknown_solution']==wanted['message_on_unknown_solution']
            save_json(WORK/'state'/('quality_tasks_'+key+'.json'),actual_tasks)
        else:
            old_suite = before['data'][key]['suites'][0]
            suite_payload = {'pool_id':new_id,'tasks':tasks,'overlap':old_suite['overlap'],'infinite_overlap':False}
            perform_batch(d,operation_key,'task-suites',[suite_payload])
            suites = d.api.all('task-suites',pool_id=new_id); assert len(suites)==1
            actual_tasks = suites[0]['tasks']
            assert len(actual_tasks)==len(tasks) and suites[0]['overlap']==10
            assert [t['input_values'] for t in actual_tasks]==[t['input_values'] for t in tasks]
            assert [t.get('known_solutions',[]) for t in actual_tasks]==[t.get('known_solutions',[]) for t in tasks]
            save_json(WORK/'state'/('quality_suite_'+key+'.json'),suites[0])
            if key=='bbox_exam': ledger['bbox_exam_suite_id']=suites[0]['id']
        save_json(config_path,payload)
        save_json(WORK/'state'/(key+'.json'),d.api.get(collection+'/'+new_id))
        save_json(LEDGER,ledger)
        print(key,new_id,'verified CLOSED',flush=True)
    # Switch all pointers together before retiring the old exam watched by the grader.
    state = json.loads(d.state_path.read_text()); state['objects'].update(ledger['new']);save_json(d.state_path,state)
    policy_path = WORK/'configs/bbox_exam_scoring.json'; policy=json.loads(policy_path.read_text())
    policy.update(pool_id=ledger['new']['bbox_exam'],suite_id=ledger['bbox_exam_suite_id']);save_json(policy_path,policy)
    ledger['pointers_updated']=True;save_json(LEDGER,ledger)


def archive_old():
    d,before,ledger=load(); assert ledger.get('pointers_updated')
    for key in ['bbox_exam','review_exam','bbox_training','review_training']:
        collection='trainings' if key.endswith('training') else 'pools'
        old_id=before['objects'][key]; obj=d.api.get(collection+'/'+old_id)
        assert not d.api.all('assignments',pool_id=old_id)
        if obj['status']!='ARCHIVED':
            assert obj['status']=='CLOSED'
            d.api.post(collection+'/'+old_id+'/archive')
            assert d.api.get(collection+'/'+old_id)['status']=='ARCHIVED'
        if key not in ledger['archived']:ledger['archived'].append(key)
        save_json(LEDGER,ledger)
    print('Previous qualification drafts archived; main pools retained.',flush=True)


if __name__=='__main__':
    {'prepare-projects':prepare_projects,'migrate':migrate,'archive-old':archive_old}[sys.argv[1]]()
