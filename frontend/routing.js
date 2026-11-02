'use strict';
(() => {
  const $=id=>document.getElementById(id),node=(tag,value='',cls='')=>Object.assign(document.createElement(tag),{textContent:value,className:cls});
  const api=path=>LabHTTP.requestJSON(path);let catalog=[],results=[],revision=0;
  const pct=n=>n==null?'—':(100*n).toFixed(1)+'%',num=n=>n==null?'—':n.toLocaleString('ko-KR');
  function download(name,body,type){const u=URL.createObjectURL(new Blob([body],{type})),a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
  function sessions(){const id=$('routing-batch').value;return catalog.filter(s=>id==='all'||s.batch_id===id);}
  function choices(){
    $('routing-sessions').replaceChildren();for(const s of sessions()){
      const label=node('label'),input=document.createElement('input');input.type='checkbox';input.value=s.key;
      input.checked=$('routing-batch').value!=='all';input.setAttribute('aria-label',s.version+' '+s.session_id+' 선택');
      label.append(input,node('span',s.version+' · '+s.session_id+' · 내담자 '+s.client_turns+'턴'));$('routing-sessions').append(label);
    }
  }
  async function refresh(){
    $('routing-refresh').disabled=true;$('routing-status').textContent='저장된 Q3 계열 세션을 확인합니다…';
    try{
      const data=await api('/api/routing/catalog');catalog=data.sessions;
      const old=$('routing-batch').value,groups=new Map();for(const s of catalog)if(s.batch_id){if(!groups.has(s.batch_id))groups.set(s.batch_id,[]);groups.get(s.batch_id).push(s);}
      $('routing-batch').replaceChildren();for(const [id,list] of groups)$('routing-batch').add(new Option(list.map(s=>s.version).join(' · ')+' / '+list.map(s=>s.session_id).sort()[0]+' / 배치 '+id.slice(0,8),id));
      $('routing-batch').add(new Option('전체 세션 목록에서 직접 선택','all'));
      const requested=new URLSearchParams(location.search).get('batch');if(groups.has(requested))$('routing-batch').value=requested;else if([...$('routing-batch').options].some(o=>o.value===old))$('routing-batch').value=old;
      choices();$('routing-status').textContent=catalog.length+'개 세션 · 배치와 분석할 세션을 확인하세요.';
      if($('routing-batch').value!=='all')await load();
    }catch(error){$('routing-status').textContent=error.message;}finally{$('routing-refresh').disabled=false;}
  }
  async function load(){
    const keys=[...document.querySelectorAll('#routing-sessions input:checked')].map(e=>e.value),from=Number($('routing-from').value),to=$('routing-to').value;
    if(!keys.length){$('routing-status').textContent='분석할 세션을 선택하세요.';return;}
    if(!Number.isInteger(from)||from<1||(to!==''&&(!Number.isInteger(Number(to))||Number(to)<from))){$('routing-status').textContent='내담자 턴 범위를 확인하세요.';return;}
    const rev=++revision;$('routing-load').disabled=true;$('routing-status').textContent='완료 턴의 실제 라우팅 기록을 읽습니다…';
    try{
      const loaded=await Promise.all(keys.map(key=>api('/api/routing?'+new URLSearchParams({key,from,...(to?{to}:{})}))));
      if(rev!==revision)return;results=loaded;$('routing-results').replaceChildren();
      const hashes=new Set(results.map(r=>r.session.scenario_hash));if(hashes.size>1)$('routing-results').append(node('p','서로 다른 초기 자료가 선택되어 있습니다. 각 세션의 빈도와 분모를 별도로 표시합니다.','warning'));
      for(const data of results)render(data);
      $('routing-status').textContent=results.map(d=>d.session.version+' '+d.turns.length+'개 내담자 턴').join(' / ')+' · 원본 기록을 변경하지 않습니다.';
      $('routing-json').disabled=$('routing-csv').disabled=false;
    }catch(error){if(rev===revision)$('routing-status').textContent=error.message;}finally{if(rev===revision)$('routing-load').disabled=false;}
  }
  function svg(tag,attrs={},value){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(value!==undefined)n.textContent=value;return n;}
  function lineChart(target,turns,series,click){
    const width=Math.max(620,turns.length*45),height=205,left=40,right=18,top=18,bottom=34;
    const max=Math.max(1,...turns.flatMap(t=>series.map(s=>s.value(t))).filter(v=>v!=null)),chart=svg('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':series.map(s=>s.label).join(' · ')});
    const xx=i=>left+(width-left-right)*(turns.length===1?.5:i/Math.max(1,turns.length-1)),yy=v=>height-bottom-v/max*(height-top-bottom);
    for(let j=0;j<=4;j++){const value=max*j/4,y=yy(value);chart.append(svg('line',{x1:left,x2:width-right,y1:y,y2:y,stroke:'#e3ebef'}),svg('text',{x:left-7,y:y+3,'text-anchor':'end',fill:'#708894','font-size':9},Number.isInteger(value)?value:value.toFixed(1)));}
    for(const s of series){
      let segment=[];const flush=()=>{if(segment.length)chart.append(svg('polyline',{points:segment.join(' '),fill:'none',stroke:s.color,'stroke-width':2,'pointer-events':'none'}));segment=[];};
      turns.forEach((t,i)=>{const value=s.value(t);if(i>0&&t.turn!==turns[i-1].turn+1)flush();if(value==null){flush();return;}segment.push(xx(i)+','+yy(value));const dot=svg('circle',{cx:xx(i),cy:yy(value),r:4,fill:s.color,tabindex:0,role:'button','aria-label':t.turn+'턴 '+s.label+' '+value});dot.append(svg('title',{},t.turn+'턴 · '+s.label+': '+value));dot.onclick=()=>click(t);dot.onkeydown=e=>{if(e.key==='Enter'||e.key===' ')click(t);};chart.append(dot);});flush();
    }
    turns.forEach((t,i)=>chart.append(svg('text',{x:xx(i),y:height-13,'text-anchor':'middle',fill:'#657f8c','font-size':10},t.turn)));
    const plot=node('div','','plot');plot.append(chart);target.append(plot);const legend=node('div','','legend');for(const s of series){const label=node('span',s.label);label.style.setProperty('--color',s.color);legend.append(label);}target.append(legend);
  }
  function candidateBars(target,selected,available){const box=node('div','','bars');for(const [label,total] of Object.entries(available)){const count=selected[label]||0,row=node('div','','bar-row'),track=node('div','','bar-track'),fill=node('div','','bar-fill');fill.style.width=(total?count/total*100:0)+'%';track.append(fill);row.append(node('span',label),track,node('span',count+' / '+total+' ('+pct(total?count/total:null)+')'));box.append(row);}target.append(box);}
  function section(parent,title,hint){const box=node('section','','chart-section');box.append(node('h3',title));if(hint)box.append(node('p',hint,'hint'));parent.append(box);return box;}
  function inspect(target,data,turn,kind,id){
    target.replaceChildren();target.append(node('h3',data.session.version+' · 내담자 '+turn.turn+'턴 · 공개 발화 '+turn.public_step));
    const detail=(title,value,open=false)=>{const box=node('details');box.open=open;box.append(node('summary',title),node('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));target.append(box);};
    detail('이번 상담사 발화',turn.counselor,true);detail('생성된 내담자 발화',turn.utterance,true);
    if(kind==='note'){
      const n=turn.notes.find(n=>n.id===id);if(n){detail((n.selected?'선택됨':'후보였으나 선택 안 됨')+' · '+id+' · '+n.kind,n.content,true);target.append(node('p','생성 턴 '+(n.created_turn ?? '미상')+' · 나이 '+(n.age==null?'미상':n.age+'턴')+' · 본문 '+num(Array.from(n.content).length)+'글자','hint'));}
    }
    if(kind==='trait'){
      const source=data.traits.find(t=>t.id===id),selected=turn.selected_traits.find(t=>t.trait_id===id);
      if(source)detail((selected?'선택됨':'선택 안 됨')+' · '+source.label,source.content,true);
      if(selected)detail('라우터가 작성한 관련성 설명과 연결 배경',selected,true);
    }
    detail('선택한 특성 전체 · 원문과 관련성',turn.selected_traits);
    for(const note of turn.notes.filter(n=>n.selected)){const item=node('details','','selected-item');item.append(node('summary',note.id+' · '+note.kind),node('pre',note.content));target.append(item);}
    detail('노트 선택 지표 · note_ids', {selected_note_ids:turn.selected_note_ids,stats:turn.stats});
    target.append(node('p','기록 출처: '+turn.record_source,'metadata'));target.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  function heatmap(target,data,type,show,click){
    const holder=node('div','','heat-scroll'),table=node('table','','heatmap'),head=node('thead'),hr=node('tr');hr.append(node('th',type==='trait'?'특성 · 원문':'노트 ID · 최신 본문'));
    for(const t of data.turns){const th=node('th'),b=node('button',t.turn);b.title='내담자 '+t.turn+'턴 전체 보기';b.onclick=()=>click(t);th.append(b);hr.append(th);}hr.append(node('th','선택 / 후보'));head.append(hr);table.append(head);
    const entries=type==='trait'?data.traits:data.notes;
    const items=entries.filter(e=>show || e.selected_turns.length).sort((a,b)=>(type==='note'?a.available_turns[0]-b.available_turns[0]:0)||b.selected_turns.length-a.selected_turns.length || a.id.localeCompare(b.id));
    const body=node('tbody');for(const item of items){
      const tr=node('tr'),label=node('th'),button=node('button','','row-name');
      const content=typeof item.content==='string'?item.content:JSON.stringify(item.content);
      const short=type==='trait'?item.label+' · '+content:item.id.slice(-7)+' · '+item.kind+' · '+content.replace(/^\[[^\n]+\]\n|^요약\n|^(?:appraisal|turn_goal|response_plan)\.[^\n]+\n/,'').replace(/\s+/g,' ');
      button.textContent=short;button.title=item.id+'\n'+content;button.onclick=()=>{const t=data.turns.find(t=>item.selected_turns.includes(t.turn)) || data.turns.find(t=>type==='trait'?t.trait_available_ids.includes(item.id):t.notes.some(n=>n.id===item.id));if(t)click(t,type,item.id);};label.append(button);tr.append(label);
      for(const t of data.turns){
        const selected=item.selected_turns.includes(t.turn),available=type==='trait'?t.trait_available_ids.includes(item.id):item.available_turns.includes(t.turn),known=type==='trait'?t.traits_recorded:t.notes_recorded;
        const td=node('td'),cell=node('button',!known?'?':selected?'●':available?'·':'','cell '+(!known?'unknown':selected?'selected':available?'':'empty'));
        cell.disabled=!available;cell.title=short+'\n'+t.turn+'턴 · '+(!known?'선택 기록 미확인':selected?'선택됨':available?'후보였으나 선택 안 됨':'당시 후보 아님');cell.setAttribute('aria-label',cell.title);cell.onclick=()=>click(t,type,item.id);td.append(cell);tr.append(td);
      }
      const denom=type==='trait'?item.available_turns:item.routing_available_turns.length;tr.append(node('td',item.selected_turns.length+'/'+denom+' ('+pct(item.rate)+')','frequency'));body.append(tr);
    }table.append(body);holder.append(table);target.append(holder);if(!items.length)holder.append(node('p','표시할 선택 항목이 없습니다.','hint'));
    return holder;
  }
  function render(data){
    const card=node('article','','session-card');card.dataset.session=data.session.id;const head=node('div','','session-head');head.append(node('h2',data.session.version+' · '+data.session.id),node('span',data.session.profile_id+' · '+data.session.model,'metadata'));card.append(head);
    card.append(node('p','완료된 공개 발화 '+data.session.public_steps+'개 / 내담자 기록 '+data.session.recorded_client_turns+'/'+data.session.expected_client_turns+'턴 · 표시 범위 '+data.session.range.join('~')+'턴 · 배치 '+(data.session.batch_id || '미기록'),'metadata'));
    card.append(node('p','선택 기록 확보: 특성 '+data.totals.traits_recorded+'/'+data.totals.turns+'턴 · 노트 '+data.totals.notes_recorded+'/'+data.totals.turns+'턴','hint'));
    for(const warning of data.warnings)card.append(node('p',warning,'warning'));
    const totals=data.totals,metrics=node('div','','metrics');for(const [value,label] of [[totals.turns,'분석 내담자 턴'],[totals.selected_notes+' / '+totals.available_notes,'선택 노트 / 전체 후보 합계'],[pct(totals.note_selection_rate),'노트 항목 선택률'],[totals.eligible_again?totals.selected_again+' / '+totals.eligible_again:'— (대상 0)','연속 재선택 / 재선택 가능']]){const box=node('div','','metric');box.append(node('strong',String(value)),node('span',label));metrics.append(box);}card.append(metrics);
    const inspector=node('section','','inspector');inspector.append(node('h3','원문과 선택 근거'),node('p','히트맵의 칸이나 그래프의 점을 누르면 해당 턴의 상담사·내담자 발화, 특성의 관련성 설명, 선택된 노트 원문이 표시됩니다.','hint'));
    const click=(turn,kind,id)=>inspect(inspector,data,turn,kind,id);
    const grid=node('div','','charts-grid');card.append(grid);
    const counts=section(grid,'턴별 노트 후보와 선택','후보 없는 턴의 선택률은 집계에서 제외합니다. 점을 눌러 원문을 확인하세요.');
    lineChart(counts,data.turns,[{label:'후보 항목',color:'#b1bdc7',value:t=>t.notes_recorded?t.stats.available:null},{label:'선택 항목',color:'#287762',value:t=>t.stats.selected}],click);
    const reuse=section(grid,'ID 재사용과 교체','교체된 이전 선택 ID는 재선택 가능 분모에서 제외합니다. 새 ID가 많다는 것만으로 새로운 마음이 많다고 해석하지 않습니다.');
    lineChart(reuse,data.turns,[{label:'처음 관측한 선택 ID',color:'#3c8b78',value:t=>t.stats.first_selected},{label:'연속 재선택',color:'#4884bd',value:t=>t.stats.selected_again},{label:'직전 선택 중 이미 교체됨',color:'#c49155',value:t=>t.stats.no_longer_available}],click);
    const traitBox=section(card,'특성 선택 히트맵','빈도 분모는 해당 특성이 후보였던 기록 턴입니다. 각 세션의 분포는 독립적으로 계산합니다.');const filter=node('label','','heat-filter'),check=document.createElement('input');check.type='checkbox';filter.append(check,document.createTextNode('선택되지 않은 특성도 표시'));traitBox.append(filter);let th=heatmap(traitBox,data,'trait',false,click);check.onchange=()=>{th.remove();th=heatmap(traitBox,data,'trait',check.checked,click);};
    const ng=section(card,'노트 선택 히트맵 · 판단 직전의 항목','행은 개별 ID이며 표시 범위에서 후보로 처음 등장한 턴 순서입니다. 내용이 바뀌며 ID가 교체되는 버전은 행이 새로 생깁니다. 칸을 누르면 그 턴의 원문을 표시합니다.');const nf=node('label','','heat-filter'),nc=document.createElement('input');nc.type='checkbox';nf.append(nc,document.createTextNode('선택되지 않은 후보 항목도 표시'));ng.append(nf);let nh=heatmap(ng,data,'note',false,click);nc.onchange=()=>{nh.remove();nh=heatmap(ng,data,'note',nc.checked,click);};
    const dist=node('div','','charts-grid');card.append(dist);candidateBars(section(dist,'노트 종류별 선택률','필드·출처 표기에 따른 분류. 선택 횟수 / 해당 종류의 후보 출현 횟수이며, 같은 ID가 여러 턴에 있으면 매번 셉니다.'),totals.kind_distribution,totals.kind_candidates);candidateBars(section(dist,'노트 ID 나이별 선택률','선택 횟수 / 해당 나이의 후보 출현 횟수. ID 생성 후 지난 턴 수이며 요약문 안의 경험 시점과 다릅니다.'),totals.age_distribution,totals.age_candidates);
    const bg=section(card,'배경 선택 빈도','선택 턴 / 해당 배경이 후보였던 기록 턴. 여러 특성에 연결된 같은 배경은 한 턴에서 한 번만 집계합니다.');const selectedBackgrounds=data.backgrounds.filter(b=>b.selected_turns.length);candidateBars(bg,Object.fromEntries(selectedBackgrounds.map(b=>[b.label,b.selected_turns.length])),Object.fromEntries(selectedBackgrounds.map(b=>[b.label,b.available_turns])));
    const detail=node('details','','summary-detail');detail.append(node('summary','턴별 수치 표 · 선택률·글자 수·특성 교체'));const wrap=node('div','','table-scroll'),table=node('table','','summary-table'),thead=node('thead'),hr=node('tr');
    for(const label of ['내담자 턴','특성 수','특성 신규/유지/제외','노트 후보','선택','선택률','선택 본문 / 후보 본문 글자','재선택 / 가능','최초 관측 선택 / 간격 후 재선택'])hr.append(node('th',label));thead.append(hr);table.append(thead);const tbody=node('tbody');
    for(const t of data.turns){const tr=node('tr'),s=t.stats,c=t.trait_change;for(const value of [t.turn,t.traits_recorded?t.selected_traits.length:'—',c?[c.new.length,c.kept.length,c.dropped.length].join('/'):'—',t.notes_recorded?s.available:'—',num(s.selected),pct(s.selection_rate),num(s.selected_chars)+' / '+num(s.available_chars),num(s.selected_again)+' / '+num(s.eligible_again),num(s.first_selected)+' / '+num(s.after_gap_selected)])tr.append(node('td',String(value)));tbody.append(tr);}table.append(tbody);wrap.append(table);detail.append(wrap);card.append(detail,inspector);$('routing-results').append(card);
  }
  $('routing-batch').onchange=choices;$('routing-refresh').onclick=refresh;$('routing-load').onclick=load;
  $('routing-json').onclick=()=>download('routing-analysis.json',JSON.stringify({generated_at:new Date().toISOString(),sessions:results},null,2),'application/json');
  $('routing-csv').onclick=()=>{
    const rows=[['version','session_id','client_turn','public_step','selected_traits','available_notes','selected_notes','note_selection_rate','available_chars','selected_chars','eligible_again','selected_again','replaced_previous_selected','first_selected','after_gap_selected']];
    for(const d of results)for(const t of d.turns){const s=t.stats;rows.push([d.session.version,d.session.id,t.turn,t.public_step,t.traits_recorded?t.selected_traits.length:null,s.available,s.selected,s.selection_rate,s.available_chars,s.selected_chars,s.eligible_again,s.selected_again,s.no_longer_available,s.first_selected,s.after_gap_selected]);}
    download('routing-turns.csv','\ufeff'+rows.map(r=>r.map(v=>'"'+String(v ?? '').replaceAll('"','""')+'"').join(',')).join('\r\n'),'text/csv;charset=utf-8');
  };refresh();
})();
