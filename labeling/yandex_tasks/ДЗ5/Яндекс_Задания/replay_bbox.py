"""Recompute historical bbox scores from local exports. Never uses the network."""
import argparse, json
from pathlib import Path
from collections import defaultdict
from bbox_exam_control import load_policy, grade_assignment
from bbox_training_control import score_learning
from portable_runtime import WORK, save_json

def read_rows(path):
    raw=Path(path).read_text(encoding='utf-8')
    try:
        data=json.loads(raw)
    except json.JSONDecodeError:
        data=[json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(data,dict):
        if 'items' not in data or data.get('has_more') is True:
            raise ValueError('Expected a complete list; combine all API pages first.')
        data=data['items']
    if not isinstance(data,list): raise ValueError('Expected JSON array or JSONL.')
    if len({a['id'] for a in data})!=len(data):raise ValueError('Duplicate assignment IDs.')
    return data

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exam-assignments')
    p.add_argument('--exam-suite',default=str(WORK/'fixtures/bbox_exam_suite.json'))
    p.add_argument('--training-assignments')
    p.add_argument('--output',required=True)
    args=p.parse_args()
    if not args.exam_assignments and not args.training_assignments:p.error('Provide assignments to replay.')
    policy,gold=load_policy();out={'mode':'offline_replay_no_api_no_admissions'}
    if args.exam_assignments:
        suite=json.loads(Path(args.exam_suite).read_text(encoding='utf-8'))
        if suite['id']!=policy['suite_id'] or str(suite['pool_id'])!=policy['pool_id']:
            raise ValueError('Foreign exam suite')
        answers=read_rows(args.exam_assignments)
        if any(str(a.get('pool_id'))!=policy['pool_id'] for a in answers):raise ValueError('Foreign exam pool')
        answers=[a for a in answers if a['status'] in ('ACCEPTED','SUBMITTED')]
        if len({a['user_id'] for a in answers})!=len(answers):raise ValueError('Repeated exam user; manual review required')
        out['exam']=[grade_assignment(a,suite,gold,policy) for a in answers]
    if args.training_assignments:
        training=json.loads((WORK/'configs/bbox_training_scoring.json').read_text())
        raw=(WORK/training['reference_file']).read_bytes()
        import hashlib
        if hashlib.sha256(raw).hexdigest()!=training['reference_sha256']:raise ValueError('Training reference hash changed')
        gold={str(e['photo_id']):e for e in json.loads(raw)['items'] if e['section']=='01_bbox_training'}
        if len(gold)!=8:raise ValueError('Expected 8 training reference images')
        grouped=defaultdict(list)
        for a in read_rows(args.training_assignments):
            if str(a.get('pool_id'))!=training['pool_id']:raise ValueError('Foreign training pool')
            grouped[str(a['user_id'])].append(a)
        out['training']=[]
        for user,rows in grouped.items():
            try:result=score_learning(rows,gold)
            except (ValueError,KeyError,TypeError) as error:
                result={'passed':False,'needs_review':True,'error_type':type(error).__name__}
            if result is not None:out['training'].append(dict(result,user_id=user))
    target=Path(args.output).resolve()
    protected={p.resolve() for p in (WORK/'state').glob('bbox_*_results.json')}
    if target in protected:raise ValueError('Choose a new output path, preserving the historical reports')
    save_json(target,out)
    print('Offline scores saved; no API calls or skill changes.')
if __name__=='__main__':main()
