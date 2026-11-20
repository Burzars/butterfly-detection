"""Install a learning-only snapshot, preserving exam/work interfaces and prices."""
import copy,json,hashlib
from build_projects import Drafts,WORK,save_json
from upload_quality_examples import perform_batch

FOLDER=WORK/'state/bbox_learning_v5'
FIELDS=['public_name','public_description','private_comment','public_instructions','task_spec','assignments_issuing_type','assignments_automerge_enabled']

def main():
    if (FOLDER/'manual_pages_verified.json').exists():
        raise RuntimeError('Learning is already finalized. Use publish_bbox_learning_ui.py for interface-only updates.')
    d=Drafts();ids=d.state['objects'];FOLDER.mkdir(exist_ok=True)
    ledger_path=FOLDER/'migration.json';ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    if not (FOLDER/'before.json').exists():
        before={'objects':copy.deepcopy(ids),'project':d.api.get('projects/'+ids['bbox_project']),
                'training':d.api.get('trainings/'+ids['bbox_training']),
                'exam':d.api.get('pools/'+ids['bbox_exam']),
                'tasks':d.api.all('tasks',pool_id=ids['bbox_training'])}
        assert all(o['status']=='CLOSED' for o in [before['training'],before['exam']])
        assert not d.api.all('assignments',pool_id=ids['bbox_training'])
        assert not d.api.all('assignments',pool_id=ids['bbox_exam'])
        save_json(FOLDER/'before.json',before)
    before=json.loads((FOLDER/'before.json').read_text())
    if 'gate_id' not in ledger:
        name='ДЗ5 bbox: обучение 7 из 8, IoU 0.75 [learning-v5]'
        matches=[s for s in d.api.all('skills') if s['name']==name];assert len(matches)<=1
        skill=matches[0] if matches else d.api.post('skills',{'name':name,'hidden':True,'private_comment':'Learning admission, recomputed from first answers; not main bbox admission.'})
        ledger['gate_id']=str(skill['id']);save_json(ledger_path,ledger)
    project={k:copy.deepcopy(before['project'][k]) for k in FIELDS}
    project['task_spec']['input_spec']['learning_ui_version']={'type':'string','required':True,'hidden':True,'allowed_values':['v5']}
    out=project['task_spec']['output_spec']
    for value in out.values():value['required']=False
    out.update(first_answer={'type':'json','required':False},training_complete={'type':'boolean','required':True})
    ui=WORK/'qualification_ui'
    project['task_spec']['view_spec']={'type':'classic','markup':(ui/'training.html').read_text(),
        'script':(ui/'training.js').read_text(),'styles':(ui/'training.css').read_text(),
        'assets':{'script_urls':['$TOLOKA_ASSETS/js/toloka-handlebars-templates.js']},
        'settings':{'showSkip':False,'showFinish':True,'showSubmit':True,'showTimer':True,'showTitle':True,'showInstructions':True,'showFullscreen':True,'showMessage':True}}
    notice='<p><strong>Обучение bbox:</strong> сначала самостоятельная разметка, затем «Проверить разметку», просмотр эталона и «Я разобрал(а) эталон». Для доступа к экзамену нужны 7 из 8 первых ответов с IoU ≥ 0,75. Исправления после показа эталона не повышают балл. Проверка допуска занимает около минуты.</p>'
    project['public_instructions']=notice+project['public_instructions']
    d.api.request('PUT','/api/v1/projects/'+ids['bbox_project'],body=project)
    save_json(FOLDER/'project_payload.json',project);save_json(WORK/'state/bbox_project.json',d.api.get('projects/'+ids['bbox_project']))
    payload={'project_id':ids['bbox_project'],'private_name':'Обучение bbox: первая попытка, IoU 0.75, 7/8 [learning-pool-v5]',
        'private_comment':'bbox-learning-v5','public_description':'Бесплатное обучение: 8 фото. Сначала своя разметка, потом эталон. Доступ к экзамену от 7 из 8 первых ответов при IoU ≥ 0,75. Проверка допуска занимает около минуты.',
        'will_expire':before['exam']['will_expire'],'may_contain_adult_content':False,
        'reward_per_assignment':0,'assignment_max_duration_seconds':1800,'auto_accept_solutions':True,'type':'TRAINING',
        'defaults':{'default_overlap_for_new_tasks':1,'default_overlap_for_new_task_suites':1},
        'filter':{'and':[{'category':'profile','key':'languages','operator':'IN','value':'RU'},
                         {'category':'skill','key':ids['bbox_skill'],'operator':'EQ','value':None},
                         {'category':'skill','key':ids['review_skill'],'operator':'EQ','value':None},
                         {'category':'skill','key':ledger['gate_id'],'operator':'EQ','value':None}]},
        'mixer_config':{'real_tasks_count':0,'golden_tasks_count':1,'training_tasks_count':0,'min_golden_tasks_count':1,
                        'mix_tasks_in_creation_order':True,'shuffle_tasks_in_task_suite':False}}
    if 'learning_pool_id' not in ledger:
        matches=[p for p in d.api.all('pools') if p.get('private_comment')=='bbox-learning-v5'];assert len(matches)<=1
        training=matches[0] if matches else d.api.post('pools',payload)
        ledger['learning_pool_id']=str(training['id']);save_json(ledger_path,ledger)
    training_id=ledger['learning_pool_id'];training=d.api.get('pools/'+training_id);assert training['status']=='CLOSED'
    tasks=[]
    for old in before['tasks']:
        if not old.get('infinite_overlap'):continue
        values=copy.deepcopy(old['input_values']);values['learning_ui_version']='v5'
        tasks.append({'pool_id':training_id,'input_values':values,'known_solutions':[{'output_values':{'training_complete':True},'correctness_weight':1}],
                      'message_on_unknown_solution':'Сначала проверьте свою разметку, посмотрите эталон и подтвердите, что разобрали пример.','infinite_overlap':True})
    assert len(tasks)==8
    d.state['objects']['bbox_learning_pool_v5_upload']=training_id
    perform_batch(d,'bbox_learning_pool_v5_upload','tasks',tasks)
    actual=d.api.all('tasks',pool_id=training_id);assert len(actual)==8
    expected={t['input_values']['photo_id']:t for t in tasks}
    for t in actual:
        assert t['input_values']==expected[t['input_values']['photo_id']]['input_values']
        assert t['known_solutions']==expected[t['input_values']['photo_id']]['known_solutions']
    exam=json.loads((WORK/'configs/bbox_exam.json').read_text())
    exam['quality_control'].pop('training_requirement',None)
    exam['filter']['and'].append({'category':'skill','key':ledger['gate_id'],'operator':'EQ','value':100})
    d.api.request('PUT','/api/v1/pools/'+ids['bbox_exam'],body=exam)
    state=json.loads(d.state_path.read_text());state['objects'].update(bbox_training=training_id,bbox_training_gate=ledger['gate_id']);state['bbox_training_collection']='pools';save_json(d.state_path,state)
    ref=WORK.parent/'Проверка_эталонов/annotations_reviewed.json'
    save_json(WORK/'configs/bbox_training_scoring.json',{'pool_id':training_id,'collection':'pools','skill_id':ledger['gate_id'],'iou_threshold':.75,'pass_count':7,
        'reference_file':'../Проверка_эталонов/annotations_reviewed.json','reference_sha256':hashlib.sha256(ref.read_bytes()).hexdigest()})
    save_json(WORK/'configs/bbox_training.json',payload);save_json(WORK/'configs/bbox_exam.json',exam)
    save_json(WORK/'state/bbox_training.json',training);save_json(WORK/'state/bbox_exam.json',d.api.get('pools/'+ids['bbox_exam']))
    save_json(WORK/'state/quality_tasks_bbox_training.json',actual)
    old_id=before['objects']['bbox_training']
    for retired in [old_id]+([ledger['training_id']] if ledger.get('training_id') else []):
        old=d.api.get('trainings/'+retired)
        if old['status']!='ARCHIVED':d.api.post('trainings/'+retired+'/archive')
    save_json(FOLDER/'verification.json',{'training_id':training_id,'gate_id':ledger['gate_id'],'collection':'pools',
        'exam_id':ids['bbox_exam'],'tasks':8,'old_training_id':old_id,'status':'CLOSED','exam_filter':exam['filter']})
    print('Learning installed',training_id,'gate',ledger['gate_id'],'exam',ids['bbox_exam'],'all CLOSED',flush=True)

if __name__=='__main__':main()
