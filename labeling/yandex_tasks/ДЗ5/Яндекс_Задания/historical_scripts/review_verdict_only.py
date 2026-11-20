"""Keep only binary review verdicts; preserve all data; never open or moderate."""
import copy,json
from build_projects import Drafts,WORK,save_json
from remove_bbox_image_error import upload_once
F=WORK/'state/review_verdict_only';FIELDS=['public_name','public_description','public_instructions','private_comment','task_spec','assignments_issuing_type','assignments_automerge_enabled']
def clean(p):
 p=copy.deepcopy(p);v=p['task_spec']['view_spec'];c=json.loads(v['config']);c['view']['items']=[x for x in c['view']['items'] if not (x.get('type')=='helper.if' and x.get('condition',{}).get('to')=='reject')];c['plugins']=[x for x in c['plugins'] if x.get('type')!='plugin.trigger'];assert 'field.checkbox-group' not in json.dumps(c);assert 'field.textarea' not in json.dumps(c);v['config']=json.dumps(c,ensure_ascii=False)
 p['public_instructions']=p['public_instructions'].replace('Выберите «Требует исправления». Причины и комментарий можно указать по желанию: они помогают разобрать ошибку, но на балл экзамена и контроля влияет только правильный вердикт. Например: «Справа внизу пропущена бабочка» или «Рамка отрезает усики».','Выберите «Требует исправления», если найдена хотя бы одна ошибка. В форме нужно указать только итоговый вердикт: «Разметка верна» или «Требует исправления».')
 return {k:p[k] for k in FIELDS if k in p}
def main():
 d=Drafts();F.mkdir(exist_ok=True);lp=F/'migration.json';m=json.loads(lp.read_text()) if lp.exists() else {'new':{}}
 if not (F/'before.json').exists():
  b={'project':d.api.get('projects/11894'),'stages':{}}
  for k in ['review_exam','review_main']:
   pid=d.state['objects'][k];p=d.api.get('pools/'+pid);assert p['status']=='CLOSED';assert not d.api.all('assignments',pool_id=pid);b['stages'][k]={'pool':p,'tasks':d.api.all('tasks',pool_id=pid),'suites':d.api.all('task-suites',pool_id=pid)}
  save_json(F/'before.json',b)
 b=json.loads((F/'before.json').read_text());templates={'review_exam':WORK/'state/review_two_answers/qualification_project.json','review_main':WORK/'state/review_two_answers/main_project.json'}
 try:
  for k in ['review_exam','review_main']:
   old=b['stages'][k];body=clean(json.loads(templates[k].read_text()))
   if k not in m['new']:
    d.api.request('PUT','/api/v1/projects/11894',body=body);save_json(F/(k+'_project.json'),body);payload={x:copy.deepcopy(v) for x,v in old['pool'].items() if x not in ['id','status','owner','created','last_started','last_stopped','last_close_reason']};payload['private_comment']+=' [binary-verdict-only-v1]';found=[p for p in d.api.all('pools',project_id='11894') if p.get('private_comment')==payload['private_comment']];assert len(found)<=1;p=found[0] if found else d.api.post('pools',payload);assert p['status']=='CLOSED';m['new'][k]=p['id'];save_json(lp,m)
   pid=m['new'][k];assert d.api.get('pools/'+pid)['status']=='CLOSED';assert not d.api.all('assignments',pool_id=pid);key=k+'_binary_verdict_only_v1';d.state['objects'][key]=pid
   if k=='review_main':
    assert len(old['tasks'])==430;payload=[]
    for t in old['tasks']:
     row={x:copy.deepcopy(t[x]) for x in ['input_values','known_solutions','message_on_unknown_solution','unavailable_for','reserved_for'] if x in t};row['pool_id']=pid
     if t.get('infinite_overlap'):row['infinite_overlap']=True
     else:row['overlap']=t['overlap']
     payload.append(row)
    upload_once(d,key,'tasks',payload);actual=d.api.all('tasks',pool_id=pid);assert len(actual)==430;expected={x['input_values']['photo_id']:x for x in payload}
    for t in actual:
     e=expected[t['input_values']['photo_id']]
     for field in ['input_values','known_solutions','unavailable_for','reserved_for','overlap']:assert t.get(field)==e.get(field),(k,field)
     assert bool(t.get('infinite_overlap'))==bool(e.get('infinite_overlap'))
    save_json(F/'main_tasks.json',actual);save_json(WORK/'state/quality_tasks_review_main.json',[t for t in actual if t.get('known_solutions')]);save_json(WORK/'state/review_400_upload/work_tasks.json',[t for t in actual if not t.get('known_solutions')])
   else:
    assert len(old['suites'])==1;src=old['suites'][0];tasks=[{x:copy.deepcopy(t[x]) for x in ['input_values','known_solutions','message_on_unknown_solution'] if x in t} for t in src['tasks']];assert len(tasks)==12;payload=[{'pool_id':pid,'tasks':tasks,'overlap':src['overlap'],'infinite_overlap':False}];upload_once(d,key,'task-suites',payload);actual=d.api.all('task-suites',pool_id=pid);assert len(actual)==1 and actual[0]['overlap']==10;assert [t['input_values'] for t in actual[0]['tasks']]==[t['input_values'] for t in tasks];assert [t['known_solutions'] for t in actual[0]['tasks']]==[t['known_solutions'] for t in tasks];save_json(WORK/'state/quality_suite_review_exam.json',actual[0])
   p=d.api.get('pools/'+pid)
   for field in ['filter','quality_control','reward_per_assignment','mixer_config','defaults','auto_accept_solutions']:assert p.get(field)==old['pool'].get(field),(k,field)
   save_json(WORK/'configs'/(k+'.json'),{x:v for x,v in p.items() if x not in ['id','status','owner','created','last_started','last_stopped','last_close_reason']});save_json(WORK/'state'/(k+'.json'),p);print(k,pid,'CLOSED; data, overlap, admission and price preserved',flush=True)
 finally:
  d.api.request('PUT','/api/v1/projects/11894',body={k:b['project'][k] for k in FIELDS if k in b['project']})
 state=json.loads(d.state_path.read_text());state['objects'].update(m['new']);save_json(d.state_path,state);m['pointers_updated']=True;save_json(lp,m)
 for k in ['review_exam','review_main']:
  pid=b['stages'][k]['pool']['id'];assert not d.api.all('assignments',pool_id=pid)
  if d.api.get('pools/'+pid)['status']!='ARCHIVED':d.api.post('pools/'+pid+'/archive')
 m['old_archived']={k:b['stages'][k]['pool']['id'] for k in ['review_exam','review_main']};save_json(lp,m);print(json.dumps(m),flush=True)
if __name__=='__main__':main()
