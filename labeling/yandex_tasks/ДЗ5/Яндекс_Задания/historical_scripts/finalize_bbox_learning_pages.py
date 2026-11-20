"""Publish eight explicit free learning pages, without mixed task allocation."""
import copy,json
from build_projects import Drafts,WORK,save_json
from upload_quality_examples import perform_batch

def main():
 d=Drafts();folder=WORK/'state/bbox_learning_v5';ledger_path=folder/'migration.json';ledger=json.loads(ledger_path.read_text());old_id=ledger['learning_pool_id']
 config=json.loads((WORK/'configs/bbox_training.json').read_text());config.pop('mixer_config',None);config.update(private_name='Обучение bbox: 8 самостоятельных ответов и эталоны [learning-pages-v5]',private_comment='bbox-learning-pages-v5')
 if 'page_pool_id' not in ledger:
  matches=[p for p in d.api.all('pools') if p.get('private_comment')==config['private_comment']];assert len(matches)<=1
  p=matches[0] if matches else d.api.post('pools',config);ledger['page_pool_id']=str(p['id']);save_json(ledger_path,ledger)
 pid=ledger['page_pool_id'];assert d.api.get('pools/'+pid)['status']=='CLOSED';assert not d.api.all('assignments',pool_id=pid)
 tasks=d.api.all('tasks',pool_id=old_id);assert len(tasks)==8
 rows=json.loads((WORK.parent/'Проверка_эталонов/annotations_reviewed.json').read_text())['items'];slots={str(e['photo_id']):e['slot'] for e in rows}
 payload=[]
 for t in sorted(tasks,key=lambda t:slots[str(t['input_values']['photo_id'])]):
  task={k:copy.deepcopy(t[k]) for k in ['input_values','known_solutions','message_on_unknown_solution']}
  payload.append({'pool_id':pid,'tasks':[task],'infinite_overlap':True,'issuing_order_override':float(9-slots[str(t['input_values']['photo_id'])])})
 d.state['objects']['bbox_learning_pages_v5']=pid;perform_batch(d,'bbox_learning_pages_v5','task-suites',payload)
 suites=d.api.all('task-suites',pool_id=pid);assert len(suites)==8 and all(len(s['tasks'])==1 and s['infinite_overlap'] for s in suites)
 expected={s['tasks'][0]['input_values']['photo_id']:s['tasks'][0] for s in payload}
 for s in suites:
  t=s['tasks'][0];e=expected[t['input_values']['photo_id']];assert t['input_values']==e['input_values'] and t['known_solutions']==e['known_solutions']
 state=json.loads(d.state_path.read_text());state['objects']['bbox_training']=pid;save_json(d.state_path,state)
 polpath=WORK/'configs/bbox_training_scoring.json';policy=json.loads(polpath.read_text());policy['pool_id']=pid;save_json(polpath,policy)
 save_json(WORK/'configs/bbox_training.json',config);save_json(WORK/'state/bbox_training.json',d.api.get('pools/'+pid));save_json(WORK/'state/quality_suites_bbox_training.json',suites)
 if d.api.get('pools/'+old_id)['status']!='ARCHIVED':d.api.post('pools/'+old_id+'/archive')
 ledger['active_learning_pool_id']=pid;save_json(ledger_path,ledger)
 save_json(folder/'manual_pages_verified.json',{'pool_id':pid,'pages':8,'tasks_per_page':1,'old_pool_archived':old_id,'status':'CLOSED'})
 print('8 free learning pages ready',pid,'gate',ledger['gate_id'],'CLOSED',flush=True)

if __name__=='__main__':main()
