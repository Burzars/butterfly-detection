import copy,json,unittest
from unittest.mock import patch
from portable_runtime import WORK, load_runtime_ids
from bbox_training_control import score_learning

class LearningTests(unittest.TestCase):
    def fixture(self,n):
        gold={str(e['photo_id']):e for e in json.loads((WORK.parent/'Проверка_эталонов/annotations_reviewed.json').read_text())['items'] if e['section']=='01_bbox_training'}
        answers=[]
        for i,(pid,e) in enumerate(gold.items()):
            first=copy.deepcopy(e['gold']) if i<n else {'status':'image_error','regions':[],'comment':'test'}
            answers.append({'id':str(i),'status':'ACCEPTED','tasks':[{'input_values':{'photo_id':pid,'image':e['image']}}],
                'solutions':[{'output_values':{'first_answer':first,'training_complete':True,'regions':e['gold']['regions']}}]})
        return gold,answers
    def test_first_attempt_seven_pass_six_fail(self):
        for n in [6,7,8]:
            gold,answers=self.fixture(n);result=score_learning(answers,gold)
            self.assertEqual(result['correct'],n);self.assertEqual(result['passed'],n>=7)
    def test_partial_and_partial_repeat_wait(self):
        gold,a=self.fixture(8);self.assertIsNone(score_learning(a[:-1],gold))
        duplicate=copy.deepcopy(a[0]);duplicate['id']='repeat';self.assertIsNone(score_learning(a+[duplicate],gold))
    def test_missing_raw_answer_or_wrong_image_rejected(self):
        gold,a=self.fixture(8);a[0]['solutions'][0]['output_values'].pop('first_answer')
        with self.assertRaises(ValueError):score_learning(a,gold)

    def test_only_training_gate_is_granted_and_not_twice(self):
        from bbox_training_control import run_training_once
        from portable_runtime import ACCOUNT_ID
        objects=load_runtime_ids()
        policy=json.loads((WORK/'configs/bbox_training_scoring.json').read_text())
        for n in [6,7]:
            _,answers=self.fixture(n)
            for a in answers:a.update(pool_id=policy['pool_id'],user_id='test-user')
            class Fake:
                skills=[]
                writes=[]
                def get(self,path):return {'id':ACCOUNT_ID} if path=='requester' else {'id':policy['pool_id'],'project_id':objects['bbox_project']}
                def all(self,path,**kw):
                    if path=='assignments':return answers
                    return [s for s in self.skills if s['skill_id']==kw['skill_id']]
                def request(self,method,path,body):
                    assert method=='PUT' and path=='/api/v1/user-skills'
                    assert body['skill_id']==objects['bbox_training_gate']
                    self.skills.append(body);self.writes.append(body)
            api=Fake()
            with patch('bbox_training_control.save_json'):
                run_training_once(api,True);run_training_once(api,True)
            self.assertEqual(len(api.writes),1 if n==7 else 0)
        gold,a=self.fixture(8);a[0]['tasks'][0]['input_values']['image']='https://example.org/another.jpg'
        with self.assertRaises(ValueError):score_learning(a,gold)

if __name__=='__main__':unittest.main()
