"""Update only the current learning snapshot, after checking version isolation."""
import json
from build_projects import Drafts,WORK,save_json

def main():
 proof=json.loads((WORK/'state/bbox_learning_v5/isolation_before_minor_update.json').read_text())
 assert all(proof[k]['frozenVersion'] and proof[k]['originalAnnotation'] for k in ['exam','work'])
 d=Drafts();p=d.api.get('projects/11893');assert p['task_spec']['view_spec']['type']=='classic'
 assert p['task_spec']['input_spec']['learning_ui_version']['required']
 payload={k:p[k] for k in ['public_name','public_description','private_comment','public_instructions','task_spec','assignments_issuing_type','assignments_automerge_enabled']}
 ui=WORK/'qualification_ui';payload['task_spec']['view_spec'].update(markup=(ui/'training.html').read_text(),script=(ui/'training.js').read_text(),styles=(ui/'training.css').read_text())
 d.api.request('PUT','/api/v1/projects/11893',body=payload)
 actual=d.api.get('projects/11893');assert actual['task_spec']['view_spec']==payload['task_spec']['view_spec']
 save_json(WORK/'configs/bbox_learning_project.json',payload);save_json(WORK/'configs/bbox_project.json',payload);save_json(WORK/'state/bbox_project.json',actual)
 print('Learning-only snapshot updated; exam/work remain version-pinned.',flush=True)

if __name__=='__main__':main()
