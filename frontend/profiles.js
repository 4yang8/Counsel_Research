'use strict';
(() => {
  const byId=id=>document.getElementById(id);
  const tabs=byId('profile-library-tabs'), body=byId('profile-library-body');
  const sections=[
    ['demographic_context','기본 배경'], ['presenting_problem','현재 고민과 상담 계기'],
    ['core_belief','핵심 신념'], ['childhood_data','성장 경험'],
    ['intermediate_beliefs','태도와 규칙'], ['coping_strategies','대처 행동'],
    ['situations','상황별 반응 예시'],
  ];
  const fieldNames={age:'나이',occupation:'직업',life_stage:'생활 단계',background:'배경',
    young_domain:'영역',young_schema:'스키마',core_belief_statement:'핵심 신념',schema_definition:'스키마 설명',
    attitude:'태도',rule:'규칙',assumption_positive:'긍정적 조건부 믿음',assumption_negative:'부정적 조건부 믿음',
    situation:'상황',automatic_thought:'자동사고',emotion:'감정',physiological_response:'신체 반응',behavior:'행동'};
  let profiles=[], selected='001';
  function text(value, depth=0) {
    if(Array.isArray(value)) return value.map((item,i)=>`${i+1}. ${text(item,depth+1)}`).join('\n\n');
    if(value && typeof value==='object') return Object.entries(value).map(([key,item])=>
      '  '.repeat(depth)+(fieldNames[key] || key)+': '+text(item,depth+1)).join('\n');
    return typeof value==='string'?value:JSON.stringify(value);
  }
  function select(number, focus=false) {
    const entry=profiles.find(row=>row.number===number);
    if(!entry)return;
    selected=number;
    for(const button of tabs.children){
      const active=button.dataset.number===number;
      button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
      if(active && focus)button.focus();
    }
    const profile=entry.profile;
    body.setAttribute('aria-labelledby','profile-library-tab-'+number);
    body.dataset.profileId=profile.profile_id;
    byId('profile-library-title').textContent=entry.label || number+' · '+profile.core_belief.young_schema;
    byId('profile-library-source').textContent=entry.source || 'resources/profiles/group1/'+entry.filename;
    body.replaceChildren();
    for(const [key,title] of sections){
      const detail=document.createElement('details');detail.className='profile-section';
      detail.open=['demographic_context','presenting_problem','core_belief'].includes(key);
      const summary=document.createElement('summary');summary.textContent=title;
      const content=document.createElement('pre');content.textContent=text(profile[key]);
      detail.append(summary,content);body.append(detail);
    }
    const raw=document.createElement('details');raw.className='profile-section';
    const summary=document.createElement('summary');summary.textContent='원본 JSON 전체 · 관리 정보 포함';
    const content=document.createElement('pre');content.id='profile-library-json';content.textContent=JSON.stringify(profile,null,2);
    raw.append(summary,content);body.append(raw);
  }
  async function load() {
    byId('profiles-refresh').disabled=true;
    byId('profile-library-status').textContent='프로필 읽는 중…';
    try {
      const result=await LabHTTP.requestJSON('/api/profiles');
      profiles=result.profiles;tabs.replaceChildren();
      for(const entry of profiles){
        const button=document.createElement('button');button.type='button';
        button.id='profile-library-tab-'+entry.number;button.dataset.number=entry.number;
        button.setAttribute('role','tab');button.setAttribute('aria-controls',body.id);
        button.textContent=entry.label || entry.number+' · '+entry.profile.core_belief.young_schema;
        button.onclick=()=>select(entry.number);
        button.onkeydown=event=>{
          if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
          event.preventDefault();const index=profiles.indexOf(entry);
          const next=event.key==='Home'?0:event.key==='End'?profiles.length-1:
            (index+(event.key==='ArrowRight'?1:-1)+profiles.length)%profiles.length;
          select(profiles[next].number,true);
        };
        tabs.append(button);
      }
      select(profiles.some(row=>row.number===selected)?selected:profiles[0].number);
      byId('profile-library-status').textContent='그룹1 + 신뢰 대조 · 한글 프로필 '+profiles.length+'개';
    } catch(error) {
      byId('profile-library-status').textContent=error.message;
    } finally {byId('profiles-refresh').disabled=false;}
  }
  byId('profiles-refresh').onclick=load;
  load();
})();
