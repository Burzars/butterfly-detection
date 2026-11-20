"""Admission from eight immutable first learning answers, recomputed privately."""
import hashlib
import json
from collections import defaultdict
from portable_runtime import WORK, ACCOUNT_ID, save_json, load_runtime_ids


def score_learning(assignments, gold):
    from bbox_exam_control import grade_photo
    grouped=defaultdict(list)
    for a in assignments:
        if a['status']!='ACCEPTED':continue
        if len(a.get('tasks',[]))!=1 or len(a.get('solutions',[]))!=1:
            raise ValueError('Expected one learning photo per page')
        task=a['tasks'][0];pid=str(task['input_values']['photo_id'])
        if pid not in gold or task['input_values']['image']!=gold[pid]['image']:
            raise ValueError('Unexpected training image')
        output=a['solutions'][0]['output_values']
        if output.get('training_complete') is not True or not isinstance(output.get('first_answer'),dict):
            raise ValueError('Missing confirmed first answer')
        grouped[pid].append((a.get('submitted') or a.get('accepted') or a['id'],a,output['first_answer']))
    # A partial repeat must not be combined with the previous completed course.
    counts=[len(grouped[p]) for p in gold]
    if not counts or min(counts)==0 or len(set(counts))!=1:return None
    photos=[]
    for pid in sorted(gold):
        _,a,answer=sorted(grouped[pid],key=lambda v:v[0])[-1]
        photos.append({'photo_id':pid,'assignment_id':a['id'],**grade_photo(answer,gold[pid]['gold'],.75)})
    correct=sum(p['correct'] for p in photos)
    return {'correct':correct,'total':8,'passed':correct>=7,'photos':photos,'course_number':counts[0]}


def run_training_once(api,apply=False):
    policy=json.loads((WORK/'configs/bbox_training_scoring.json').read_text())
    objects=load_runtime_ids()
    if policy['pool_id']!=objects['bbox_training'] or policy['skill_id']!=objects['bbox_training_gate']:
        raise RuntimeError('Wrong training admission binding')
    if str(api.get('requester')['id'])!=ACCOUNT_ID:raise RuntimeError('Wrong account')
    pool=api.get(policy.get('collection','trainings')+'/'+policy['pool_id'])
    if str(pool['project_id'])!=objects['bbox_project']:raise RuntimeError('Wrong training project')
    raw=(WORK/policy['reference_file']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=policy['reference_sha256']:raise RuntimeError('Training references changed')
    gold={str(e['photo_id']):e for e in json.loads(raw)['items'] if e['section']=='01_bbox_training'}
    if len(gold)!=8:raise RuntimeError('Expected eight reference photos')
    answers=api.all('assignments',pool_id=policy['pool_id']);users=defaultdict(list)
    for a in answers:
        if str(a.get('pool_id'))!=policy['pool_id']:raise RuntimeError('Foreign training pool')
        users[str(a['user_id'])].append(a)
    skills={str(s['user_id']) for s in api.all('user-skills',skill_id=policy['skill_id']) if float(s['value'])==100}
    other={str(s['user_id']) for s in api.all('user-skills',skill_id=objects['review_skill'])}
    report={'pool_id':policy['pool_id'],'iou_threshold':.75,'pass_count':7,'results':[],
            'active_pages':sum(a['status']=='ACTIVE' for a in answers)}
    for user,rows in users.items():
        try:
            result=score_learning(rows,gold)
        except (ValueError,KeyError,TypeError) as error:
            report['results'].append({'user_id':user,'passed':False,'needs_review':True,'error_type':type(error).__name__})
            continue
        if result is None:continue
        result['user_id']=user
        if user in other:result['blocked_reason']='opposite_role'
        elif result['passed'] and apply:
            if not api.all('user-skills',skill_id=objects['review_skill'],user_id=user):
                if user not in skills:
                    api.request('PUT','/api/v1/user-skills',body={'skill_id':policy['skill_id'],'user_id':user,'value':100})
                if not any(float(s['value'])==100 for s in api.all('user-skills',skill_id=policy['skill_id'],user_id=user)):
                    raise RuntimeError('Training admission could not be verified')
                result['admission_verified']=True
        report['results'].append(result)
    from bbox_exam_control import now
    report['checked_at']=now();save_json(WORK/'state/bbox_training_results.json',report)
    return report
