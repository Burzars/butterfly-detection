/* Training feedback only. Admission is independently recomputed from raw first answers. */
function gradeLesson(a,g){
  var p=a.regions||[],r=g.regions||[];
  if(g.status==='empty')return {correct:a.status==='empty'&&p.length===0,reason:'На этом фото взрослых бабочек нет. Правильный ответ — без рамок.'};
  if(a.status!=='objects')return {correct:false,reason:'На фото есть взрослая бабочка. Нужно выделить все целевые объекты.'};
  if(p.length!==r.length)return {correct:false,reason:'Число рамок не совпадает: у вас '+p.length+', в эталоне '+r.length+'. Проверьте пропуски и лишние рамки.'};
  function overlap(x,y){var area=Math.max(0,Math.min(x.left+x.width,y.left+y.width)-Math.max(x.left,y.left))*Math.max(0,Math.min(x.top+x.height,y.top+y.height)-Math.max(x.top,y.top));return area/(x.width*x.height+y.width*y.height-area);}
  var matrix=p.map(function(x){return r.map(function(y){return overlap(x,y);});}),matched={};
  function match(i,seen){for(var j=0;j<r.length;j++){if(matrix[i][j]<.75-1e-12||seen[j])continue;seen[j]=true;if(matched[j]===undefined||match(matched[j],seen)){matched[j]=i;return true;}}return false;}
  var n=0;for(var i=0;i<p.length;i++)if(match(i,{}))n++;
  return {correct:n===r.length,reason:n===r.length?'Все объекты сопоставлены с эталоном при IoU не ниже 0,75.':'Не все рамки совпали с эталоном при IoU ≥ 0,75. Сравните границы, усики и лапки. Сопоставлено '+n+' из '+r.length+'.'};
}
exports.Task=extend(TolokaHandlebarsTask,function(options){TolokaHandlebarsTask.call(this,options);},{
  onRender:function(){
    var self=this,root=this.getDOMElement();this._root=root;this._input=this.getTask().input_values;
    var saved=this.getSolution().output_values||{};
    this._draft={status:saved.status||'',regions:JSON.parse(JSON.stringify(saved.regions||[])),comment:saved.comment||''};
    this._first=saved.first_answer?JSON.parse(JSON.stringify(saved.first_answer)):null;
    this._done=!!saved.training_complete;
    this._resize=function(){root.querySelectorAll('.board').forEach(function(board){var im=board.querySelector('img');if(im.naturalWidth&&board.parentElement.clientWidth)board.style.width=Math.min(board.parentElement.clientWidth,Math.min(window.innerHeight*.60,480)*im.naturalWidth/im.naturalHeight)+'px';});};
    window.addEventListener('resize',this._resize);
    root.querySelectorAll('img').forEach(function(im){im.onload=self._resize;im.src=self._input.image;});
    root.querySelectorAll('input[type=radio]').forEach(function(el){el.checked=el.value===self._draft.status;el.addEventListener('change',function(){if(self._first)return;self._draft.status=el.value;self._sync();self._paint();});});
    root.querySelectorAll('[data-action]').forEach(function(el){el.addEventListener('click',function(){self._action(el.dataset.action);});});
    var svg=root.querySelector('.student svg'),start=null,temp=null;
    function point(e){var b=svg.getBoundingClientRect();return {x:Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),y:Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))};}
    function rect(a,b){return {shape:'rectangle',left:Math.min(a.x,b.x),top:Math.min(a.y,b.y),width:Math.abs(a.x-b.x),height:Math.abs(a.y-b.y)};}
    svg.addEventListener('pointerdown',function(e){if(self._first||e.button>0)return;start=point(e);svg.setPointerCapture(e.pointerId);e.preventDefault();});
    svg.addEventListener('pointermove',function(e){if(!start)return;temp=rect(start,point(e));self._draw(svg,self._draft.regions.concat([temp]));});
    svg.addEventListener('pointerup',function(e){if(!start)return;var b=rect(start,point(e));start=null;temp=null;if(b.width>.003&&b.height>.003){self._draft.regions.push(b);self._draft.status='objects';root.querySelector('input[value=objects]').checked=true;self._sync();}self._paint();});
    svg.addEventListener('pointercancel',function(){start=null;temp=null;self._paint();});
    this._paint();
  },
  _draw:function(svg,regions){svg.setAttribute('viewBox','0 0 1000 1000');svg.setAttribute('preserveAspectRatio','none');svg.replaceChildren();regions.forEach(function(r){var el=document.createElementNS('http://www.w3.org/2000/svg','rect');el.setAttribute('x',r.left*1000);el.setAttribute('y',r.top*1000);el.setAttribute('width',r.width*1000);el.setAttribute('height',r.height*1000);svg.appendChild(el);});},
  _sync:function(){this.setSolutionOutputValues({status:this._draft.status,regions:this._draft.regions,comment:this._draft.comment,first_answer:this._first,training_complete:this._done});},
  _paint:function(){
    var root=this._root;if(!root)return;
    this._draw(root.querySelector('.student svg'),this._first?this._first.regions:this._draft.regions);
    root.querySelector('.count').textContent='Рамок: '+this._draft.regions.length;
    root.querySelector('.reference').hidden=!this._first;root.querySelector('.feedback').hidden=!this._first;
    root.querySelector('.draft-controls').hidden=!!this._first;
    if(this._first){
      var g={status:this._input.reference_regions.length?'objects':'empty',regions:this._input.reference_regions},result=gradeLesson(this._first,g);
      this._draw(root.querySelector('.gold svg'),g.regions);
      root.querySelector('.feedback').className='feedback '+(result.correct?'correct':'incorrect');
      root.querySelector('.verdict').textContent=result.correct?'Правильно':'Неправильно — разберите эталон';
      root.querySelector('.explanation').textContent=result.reason;
      root.querySelector('.hint').textContent=this._input.training_hint||'';
      root.querySelector('.ready').hidden=!this._done;
      root.querySelector('[data-action=ack]').hidden=this._done;
    }
    if(this._resize)this._resize();
  },
  _action:function(action){
    var err=this._root.querySelector('.lesson-error');err.textContent='';
    if(action==='ack'&&this._first){this._done=true;this._sync();this._paint();return;}
    if(this._first)return;
    if(action==='undo')this._draft.regions.pop();
    if(action==='clear')this._draft.regions=[];
    if(action==='check'){
      var a=this._draft;
      var image=this._root.querySelector('.student img');
      if(!image.complete||!image.naturalWidth){err.textContent='Дождитесь загрузки фотографии. Если она не появилась, обновите страницу и проверьте соединение.';return;}
      if(a.status!=='objects'&&a.status!=='empty'){err.textContent='Выберите результат по фотографии.';return;}
      if((a.status==='objects')!==(a.regions.length>0)){err.textContent='При наличии бабочек нужны рамки; для других ответов удалите рамки.';return;}
      this._first=JSON.parse(JSON.stringify(a));
    }
    this._sync();this._paint();
  },
  validate:function(){
    if(!this._first||!this._done){var message='Сначала проверьте свою разметку, посмотрите эталон и нажмите «Я разобрал(а) эталон».';if(this._root)this._root.querySelector('.lesson-error').textContent=message;return {task_id:this.getTask().id,errors:{training_complete:{code:'REQUIRED',message:message}}};}
    return null;
  },
  onValidationFail:function(){if(this._root&&(!this._first||!this._done))this._root.querySelector('.lesson-error').textContent='Сначала проверьте разметку и подтвердите, что разобрали эталон.';},
  setSolution:function(solution){TolokaHandlebarsTask.prototype.setSolution.call(this,solution);if(this._root){var v=solution.output_values||{};this._draft={status:v.status||'',regions:JSON.parse(JSON.stringify(v.regions||[])),comment:v.comment||''};this._first=v.first_answer?JSON.parse(JSON.stringify(v.first_answer)):null;this._done=!!v.training_complete;this._paint();}},
  onDestroy:function(){if(this._resize)window.removeEventListener('resize',this._resize);}
});
function extend(Parent,ctor,methods){ctor.prototype=Object.create(Parent.prototype);Object.keys(methods).forEach(function(k){ctor.prototype[k]=methods[k];});return ctor;}
