'use strict';
// Reuse controls and their IDs so the experiment, step selection and raw-context code stay shared.
(() => {
  const $=id=>document.getElementById(id);
  const el=(tag,cls='')=>Object.assign(document.createElement(tag),{className:cls});
  const shell=el('div','lab-shell'),nav=el('aside','config-sidebar'),split=el('div','split-workspace');
  const left=el('section','lab-results'),scroll=el('div','results-scroll'),right=el('section','conversation-column');
  right.id='conversation-column';
  nav.setAttribute('aria-label','설정과 결과 탐색');right.setAttribute('aria-label','대화와 상담사 발화');
  left.append($('notice'),scroll);
  const divider=el('div','column-divider');divider.id='column-divider';divider.tabIndex=0;
  divider.setAttribute('role','separator');divider.setAttribute('aria-label','설정·결과와 대화의 열 너비 조절');
  divider.setAttribute('aria-orientation','vertical');divider.title='드래그 또는 좌우 방향키로 너비 조절 · 더블클릭으로 초기화';
  split.append(left,divider,right);shell.append(nav,split);document.body.append(shell);
  const conversationToggle=el('button');conversationToggle.id='conversation-toggle';conversationToggle.type='button';
  conversationToggle.setAttribute('aria-controls',right.id);
  function showConversation(visible){
    const top=scroll.scrollTop,chatScrolls=[...right.querySelectorAll('#chat,#paired-transcript')].map(element=>[element,element.scrollTop]);
    if(!visible && right.contains(document.activeElement))conversationToggle.focus({preventScroll:true});
    shell.dataset.conversation=visible?'expanded':'hidden';
    conversationToggle.textContent=visible?'대화 숨기기':'대화 펼치기';
    conversationToggle.setAttribute('aria-expanded',String(visible));
    requestAnimationFrame(()=>{scroll.scrollTop=top;for(const [element,position] of chatScrolls)element.scrollTop=position;});
  }
  conversationToggle.onclick=()=>showConversation(shell.dataset.conversation!=='expanded');
  showConversation(true);

  const config=el('section','config-scroll');config.id='settings-dashboard';
  const heading=el('div','dashboard-heading');heading.innerHTML='<h2>실험 설정</h2>';
  const originalEditor=$('initial-editor'),editor=el('section');editor.id=originalEditor.id;
  editor.append(...originalEditor.childNodes);originalEditor.replaceWith(editor);
  const materials=editor.parentElement,oldSettings=document.querySelector('.settings-wrap');
  const tools=document.querySelector('section.toolbox'),model=oldSettings.querySelector('.settings');
  const staging=el('div');staging.hidden=true;staging.append(...editor.childNodes);document.body.append(staging);
  const grid=el('div','settings-grid');scroll.append(config);config.append(heading,editor);editor.replaceChildren(grid);editor.open=true;
  const card=(id,title,optional, ...children)=>{
    const section=el('section','setting-block');section.id='block-'+id;
    const head=el('div','block-heading'),name=el('h3');name.textContent=title;head.append(name);
    if(optional){
      const label=el('label','block-toggle'),input=el('input');input.type='checkbox';input.checked=true;
      input.id='block-enabled-'+optional;input.dataset.block=optional;label.append(input,document.createTextNode('입력에 포함'));head.append(label);
      input.addEventListener('change',()=>{section.dataset.enabled=String(input.checked);});
      section.dataset.enabled='true';
    } else {const badge=el('span','block-badge');badge.textContent=id==='reference'?'선택 제공':id==='model'?'생성 설정':'필수 입력';head.append(badge);}
    section.append(head,...children);grid.append(section);return section;
  };
  $('version-settings').firstChild.nodeValue='내담자 버전';
  card('system','내담자 · 버전 / 실행 지침','system',$('version-settings'),$('system_prompt').closest('label'),$('summary-settings'),$('prompt-boundary-hint'));
  const clientSubagent=card('client-subagent','내담자 · 서브에이전트 프롬프트',null);
  clientSubagent.hidden=true;
  clientSubagent.innerHTML+='<label id="client-subagent-picker">서브에이전트<select id="client-subagent-select"></select></label><label id="client-subagent-prompt-label">특성 라우터 지침<textarea id="client-subagent-prompt" rows="12"></textarea></label><p class="hint">원본 파일을 편집합니다. 저장하면 기존 세션을 포함해 다음 해당 서브에이전트 호출부터 적용됩니다.</p>';
  const clientBudget=el('fieldset','counselor-budget-settings');clientBudget.id='client-reasoning-budgets';clientBudget.hidden=true;
  clientBudget.innerHTML='<legend>Reasoning token budget · 내담자 추론 토큰 한도</legend><div class="counselor-budget-options">'+[['main','메인'],['subagent','특성 라우터']].map(([key,label])=>'<div class="counselor-budget-option"><label><input id="client-'+key+'-budget-enabled" type="checkbox">'+label+'</label><input id="client-'+key+'-budget" type="number" min="1" max="1000000" step="1" value="512" disabled aria-label="내담자 '+label+' 추론 토큰 한도"></div>').join('')+'</div><p class="hint">체크 해제: 한도 미지정 · 각 모델 호출에 독립 적용 · 새 대화 시작 시 저장</p>';
  $('version-settings').after(clientBudget);
  const q1Hint=el('p','hint');q1Hint.id='q1-input-hint';q1Hint.hidden=true;q1Hint.textContent='Q1은 기본 정보와 자동 선정 특성, 공개 대화, 최신 비공개 내면 노트를 사용합니다. 기존 작업 지침·고정 상태값·taxonomy는 입력하지 않습니다. 성장 경험·상황 DB 조회는 이번 버전에 포함하지 않습니다.';
  $('block-system').append(q1Hint);
  card('task','내담자 · 작업 지침','task',$('edit-task').closest('label'));
  card('profile','내담자 · 프로필 / 입력 범위','profile',$('profile-exposure'),$('edit-profile').closest('label'));
  card('state','내담자 · 초기 내면 상태',null,$('state-controls'),$('state-input-hint'));
  card('taxonomy','내담자 · 반응 taxonomy',null,$('edit-taxonomy').closest('label'));
  const common=card('model','공통 · 연결 / 생성 / 실행',null,model);
  grid.before(common);
  const connectionActions=el('div','common-connection-actions');connectionActions.append($('check'),$('reload'));common.querySelector('.block-heading').append(connectionActions);
  const lockHint=el('p','hint');lockHint.id='settings-lock-hint';lockHint.hidden=true;common.append(lockHint);
  card('reference','내담자 · DB / 도구',null,tools);
  const dialogueBlock=card('dialogue','공유 · 초기 대화','dialogue',$('history-settings'),$('dialogue-editor'),$('add-turn'));
  grid.id='settings-client-panel';grid.setAttribute('role','tabpanel');grid.setAttribute('aria-labelledby','settings-client-tab');
  const counselorGrid=el('div','settings-grid');counselorGrid.id='settings-counselor-panel';counselorGrid.hidden=true;
  counselorGrid.setAttribute('role','tabpanel');counselorGrid.setAttribute('aria-labelledby','settings-counselor-tab');grid.after(counselorGrid);
  const dialogueGrid=el('div','settings-grid');dialogueGrid.id='settings-dialogue-panel';dialogueGrid.hidden=true;
  dialogueGrid.setAttribute('role','tabpanel');dialogueGrid.setAttribute('aria-labelledby','settings-dialogue-tab');dialogueGrid.append(dialogueBlock);counselorGrid.after(dialogueGrid);
  const panels={client:grid,counselor:counselorGrid,dialogue:dialogueGrid},roles=Object.keys(panels);
  const roleTabs=el('div','settings-role-tabs');roleTabs.setAttribute('role','tablist');roleTabs.setAttribute('aria-label','실험 설정 종류');heading.after(roleTabs);
  let settingsRole='client';const roleScroll={};
  const chooseRole=role=>{
    const top=settingsRole!==role?(roleScroll[role] || 0):scroll.scrollTop;
    if(settingsRole!==role)roleScroll[settingsRole]=scroll.scrollTop;
    settingsRole=role;for(const [name,panel] of Object.entries(panels))panel.hidden=name!==role;
    for(const button of roleTabs.children){const selected=button.dataset.role===role;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;}
    scroll.scrollTop=top;
  };
  for(const [role,label] of [['client','내담자'],['counselor','상담사'],['dialogue','초기 대화']]){
    const button=el('button');button.type='button';button.id='settings-'+role+'-tab';button.dataset.role=role;button.textContent=label;
    button.setAttribute('role','tab');button.setAttribute('aria-controls','settings-'+role+'-panel');button.onclick=()=>chooseRole(role);
    button.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?roles[0]:event.key==='End'?roles.at(-1):roles[(roles.indexOf(role)+(event.key==='ArrowRight'?1:-1)+roles.length)%roles.length];chooseRole(next);$('settings-'+next+'-tab').focus({preventScroll:true});}};
    roleTabs.append(button);
  }
  window.selectSettingsRole=chooseRole;chooseRole('client');
  const raw=el('details','initial-raw'),rawTitle=el('summary');rawTitle.textContent='편집한 초기 자료 JSON · 실제 프롬프트와 구분';raw.append(rawTitle,$('scenario'));
  const source=el('details','source-details'),sourceTitle=el('summary');sourceTitle.textContent='자료 출처 · 기록용 / 모델에 미전달';source.append(sourceTitle,$('edit-source').closest('label'));
  const guide=el('details','compact-guide'),guideTitle=el('summary');guideTitle.textContent='실습 안내 / 예문';guide.append(guideTitle,document.querySelector('.nested-guide'));
  const metadata=el('section','settings-metadata');metadata.style.gridColumn='1 / -1';metadata.append(source,raw,guide);grid.append(metadata);
  materials.remove();oldSettings.remove();staging.remove();
  const library=el('section','library-view');library.append($('group1-profiles'));library.hidden=true;
  scroll.append(config,library,document.querySelector('.workspace'));

  const chat=document.querySelector('.chat-panel'),trial=document.querySelector('.trial-controls');
  const dock=el('section','composer-dock');
  const directOptions=el('details','direct-input-options'),directTitle=el('summary'),directHint=el('p','hint');
  directTitle.textContent='내담자 직접 입력 실습 옵션';directHint.textContent='왼쪽 ‘대화 / 입력’에서 사용하는 설정입니다. 양방향 대화에는 적용되지 않습니다.';
  directOptions.append(directTitle,$('trial-mode').closest('label'),directHint);directOptions.hidden=true;common.append(directOptions);
  const preset=$('counselor-preset').closest('label');preset.firstChild.nodeValue='마지막 상담사 발화 예문';$('history-settings').append(preset);
  dock.append($('composer'),$('trial-hint'));trial.remove();
  right.append(chat,dock);
  const memory=el('details','conversation-memory'),memoryTitle=el('summary');memoryTitle.textContent='내면 상태 / 요약 / 저장 기록';
  memory.append(memoryTitle,$('state-memory'));right.append(memory);memory.hidden=true;
  new MutationObserver(()=>{memory.hidden=$('state-memory').hidden;}).observe($('state-memory'),{attributes:true,attributeFilter:['hidden']});

  const settingsTabs=el('div','config-tabs');settingsTabs.setAttribute('role','tablist');settingsTabs.setAttribute('aria-label','작업 화면');
  const button=(id,text,view)=>{const b=el('button','config-tab');b.id=id;b.type='button';b.textContent=text;b.setAttribute('role','tab');b.onclick=()=>choose(view);settingsTabs.append(b);return b;};
  button('settings-tab','실험 설정','settings');button('config-tab-profile-library','프로필 원본','library');
  const resultLabel=el('div','nav-label');resultLabel.textContent='결과';
  const resultTabs=document.querySelector('.context-tabs');resultTabs.className='context-tabs';
  const chatButton=el('button');chatButton.id='chat-tab';chatButton.type='button';chatButton.textContent='대화 / 입력';chatButton.setAttribute('role','tab');chatButton.onclick=()=>{choose('visual');$('message').focus({preventScroll:true});};
  chatButton.hidden=true;nav.append(settingsTabs,resultLabel,resultTabs,chatButton);
  const navTop=el('div','sidebar-top');navTop.setAttribute('aria-label','설정과 결과 메뉴');navTop.append(...nav.childNodes);nav.append(navTop);
  navTop.prepend(conversationToggle);navTop.append($('status'));
  document.querySelector('header').hidden=true;
  const rememberScroll={},extraViews=new Map();let current='settings';
  function choose(view){
    view=view==='chat'?'visual':view;
    if(current!==view){rememberScroll[current]=scroll.scrollTop;scroll.scrollTop=rememberScroll[view] || 0;}
    current=view;shell.dataset.view=view;config.hidden=view!=='settings';library.hidden=view!=='library';
    for(const b of settingsTabs.children)b.setAttribute('aria-selected',String((b.id==='settings-tab'?'settings':b.id==='config-tab-profile-library'?'library':b.dataset.view)===view));
    for(const [name,element] of extraViews)element.hidden=view!==name;
    for(const name of ['visual','raw','activation','workflow']){$(name+'-context').hidden=view!==name;$(name+'-tab').setAttribute('aria-selected',String(name===view));}
    for(const tab of resultTabs.querySelectorAll('[data-view]'))tab.setAttribute('aria-selected',String(tab.dataset.view===view));
    chatButton.setAttribute('aria-selected','false');
  }
  window.showLabResult=choose;
  window.registerLabView=(name,element)=>{extraViews.set(name,element);element.hidden=true;};
  window.openLabSettings=id=>{
    $('settings-tab').click();
    if(id!=='model')chooseRole(id==='dialogue'?'dialogue':id==='counselor'?'counselor':'client');
    const block=$('block-'+id);if(!block)return;
    block.scrollIntoView({block:'start',behavior:'instant'});
    block.classList.remove('settings-highlight');void block.offsetWidth;block.classList.add('settings-highlight');
  };
  for(const group of [settingsTabs,resultTabs])group.addEventListener('keydown',event=>{
    if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
    const list=[...group.querySelectorAll('button')],index=list.indexOf(document.activeElement);if(index<0)return;
    event.preventDefault();const next=event.key==='Home'?list[0]:event.key==='End'?list.at(-1):list[(index+(event.key==='ArrowDown'?1:-1)+list.length)%list.length];next.click();next.focus();
  });
  // Switching views preserves the selected execution snapshot.
  for(const id of ['system_prompt','summary_system_prompt','edit-task','edit-profile','edit-taxonomy','edit-source']){
    const input=$(id);input.rows=id==='edit-source'?3:5;
    const expand=el('button','expand-field');expand.type='button';expand.textContent='확대';
    expand.onclick=()=>{const expanded=input.classList.toggle('expanded');expand.textContent=expanded?'축소':'확대';};input.parentElement.append(expand);
  }
  $('message').rows=5;
  let ratio=65;
  try{ratio=Number(LabStorage.getItem('counseling-column-ratio')) || 65;}catch{}
  function resize(value,persist=true){
    if(shell.dataset.conversation==='hidden' || window.matchMedia('(max-width:700px)').matches)return;
    const width=split.clientWidth-10,min=Math.max(25,Math.min(45,280/width*100)),max=Math.min(80,Math.max(55,100-280/width*100));
    ratio=Math.max(min,Math.min(max,value));split.style.setProperty('--split-left',ratio+'fr');split.style.setProperty('--split-right',(100-ratio)+'fr');
    divider.setAttribute('aria-valuemin',Math.round(min));divider.setAttribute('aria-valuemax',Math.round(max));divider.setAttribute('aria-valuenow',Math.round(ratio));
    if(persist)try{LabStorage.setItem('counseling-column-ratio',String(ratio));}catch{}
  }
  divider.addEventListener('pointerdown',event=>{if(event.button!==0)return;divider.setPointerCapture(event.pointerId);document.body.classList.add('resizing-columns');event.preventDefault();});
  divider.addEventListener('pointermove',event=>{if(!divider.hasPointerCapture(event.pointerId))return;const bounds=split.getBoundingClientRect();resize((event.clientX-bounds.left)/(bounds.width-10)*100);});
  const end=()=>document.body.classList.remove('resizing-columns');divider.addEventListener('pointerup',end);divider.addEventListener('lostpointercapture',end);
  divider.addEventListener('dblclick',()=>resize(65));divider.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home'].includes(event.key)){event.preventDefault();resize(event.key==='Home'?65:ratio+(event.key==='ArrowLeft'?-3:3));}});
  new ResizeObserver(()=>resize(ratio,false)).observe(split);
  resize(ratio,false);choose('settings');
})();
