/* The platform grades output.status against known_solutions; status is the first committed choice. */
exports.Task=extendReview(TolokaHandlebarsTask,function(o){TolokaHandlebarsTask.call(this,o);},{
 onRender:function(){
  var self=this,root=this.getDOMElement(),v=this.getSolution().output_values||{};this._root=root;this._input=this.getTask().input_values;
  this._first=v.feedback_seen&&['accept','reject'].indexOf(v.status)>=0?v.status:null;this._draft=v.draft_status||this._first||'';
  var svg=root.querySelector('svg');svg.setAttribute('viewBox','0 0 1000 1000');svg.setAttribute('preserveAspectRatio','none');
  (this._input.regions||[]).forEach(function(r){var el=document.createElementNS('http://www.w3.org/2000/svg','rect');['left','top','width','height'].forEach(function(k,i){el.setAttribute(['x','y','width','height'][i],r[k]*1000);});svg.appendChild(el);});
  root.querySelector('img').src=this._input.image;
  root.querySelectorAll('input[type=radio]').forEach(function(el){el.name='review-learning-'+self.getTask().id;el.addEventListener('change',function(){if(!self._first){self._draft=el.value;self._sync();self._paint();}});});
  root.querySelector('[data-action=check]').addEventListener('click',function(){self._action('check');});
  this._paint();
 },
 _sync:function(){this.setSolutionOutputValues({status:this._first||'',draft_status:this._draft,feedback_seen:!!this._first,understood:!!this._first});},
 _paint:function(){var r=this._root;if(!r)return;var locked=!!this._first;r.querySelectorAll('input[type=radio]').forEach(function(el){el.disabled=locked;el.checked=el.value===(this._first||this._draft);},this);r.querySelector('[data-action=check]').hidden=locked;r.querySelector('.feedback').hidden=!locked;
  if(locked){var correct=this._first===this._input.reference_status;r.querySelector('.feedback').className='feedback '+(correct?'correct':'incorrect');r.querySelector('.verdict').textContent=correct?'Правильно':'Неправильно — разберите пример';r.querySelector('.expected').textContent='Правильный вердикт: '+(this._input.reference_status==='accept'?'«Разметка верна».':'«Требует исправления».');r.querySelector('.hint').textContent=this._input.training_hint||'';r.querySelector('.ready').hidden=false;}
 },
 _action:function(action){var error=this._root.querySelector('.lesson-error');error.textContent='';
  if(this._first)return;var image=this._root.querySelector('img');
  if(!image.complete||!image.naturalWidth){error.textContent='Дождитесь загрузки фотографии. При сбое обновите страницу.';return;}
  if(['accept','reject'].indexOf(this._draft)<0){error.textContent='Выберите вердикт.';return;}
  this._first=this._draft;this._sync();this._paint();
 },
 validate:function(){if(!this._first){var m='Выберите вердикт и нажмите «Проверить ответ».';if(this._root)this._root.querySelector('.lesson-error').textContent=m;return {task_id:this.getTask().id,errors:{understood:{code:'REQUIRED',message:m}}};}return null;},
 setSolution:function(s){TolokaHandlebarsTask.prototype.setSolution.call(this,s);if(this._root){var v=s.output_values||{};this._first=v.feedback_seen&&['accept','reject'].indexOf(v.status)>=0?v.status:null;this._draft=v.draft_status||this._first||'';this._paint();}}
});
function extendReview(P,C,M){C.prototype=Object.create(P.prototype);Object.keys(M).forEach(function(k){C.prototype[k]=M[k];});return C;}
