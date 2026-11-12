"""Descriptive, dialogue-level summaries. No clinical or causal score."""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
import statistics

from data import read, write
from schema import validate

# label, units, interpretation. Events within a dialogue are not independent.
METRICS = {
    'reported_checks': ('Recorded reaction checks', 'count', 'descriptive'),
    'reported_interventions': ('Recorded suggestions / interpretations / tasks', 'count', 'descriptive'),
    'reported_markers': ('Recorded withdrawal markers', 'count', 'descriptive'),
    'observer_following': ('Observer recommendation following', 'rate', 'descriptive'),
    'generation_tokens': ('Recorded post-fork generation tokens', 'count', 'cost'),
    'checks': ('LLM-verified reaction checks', 'count', 'opportunity'),
    'any_check': ('Dialogues with a reaction check', 'rate', 'opportunity'),
    'client_problem': ('Public client inconsistency', 'rate', 'lower'),
    'counselor_problem': ('Leading / repetitive / unsupported checks', 'rate', 'lower'),
    'naturalness': ('Dialogue naturalness (1–5)', 'score', 'higher'),
    'hidden_any': ('Dialogues with a hidden reaction (whole context)', 'rate', 'opportunity'),
    'hidden_new': ('New hidden reactions after fork', 'count', 'descriptive'),
    'followup_any': ('Dialogues with a relevant followup', 'rate', 'opportunity'),
    'contradiction': ('Conflict with earlier private experience', 'rate', 'lower'),
    'unsupported_change': ('Unsupported change among judged changes', 'rate', 'lower'),
    'explicit_disclosure': ('Explicit disclosure among judged followups', 'rate', 'descriptive'),
    'target_injected': ('Target record injected at followup', 'rate', 'descriptive'),
    'hidden_target_checks': ('Verified checks targeting hidden reactions', 'rate', 'opportunity'),
    'immediate_checks': ('Immediate checks among located checks', 'rate', 'descriptive'),
    'check_delay': ('Intervening turns before a reaction check', 'count', 'descriptive'),
    'major_problem': ('Dialogues with a severity-2 public issue', 'rate', 'lower'),
    'issue_severity': ('Mean severity among public issues (1–2)', 'score', 'lower'),
    'continuity_coverage': ('Judgeable continuity among relevant followups', 'rate', 'coverage'),
    'withheld_followups': ('Still withheld among judged followups', 'rate', 'descriptive'),
    'partial_disclosure': ('Partial disclosure among judged followups', 'rate', 'descriptive'),
}
for _kind in ('contradiction','unexplained_shift','leading','repeated_check','unsupported_assumption','abrupt_transition'):
    METRICS['issues_'+_kind]=('Public issues: '+_kind,'count','lower')


def csv_write(path, rows):
    rows = list(rows)
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        if rows:
            w = csv.DictWriter(stream, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def measure(doc, public=None, private=None):
    """Each metric is (numerator, eligible denominator); zero denom = NA."""
    out = {}
    if public is not None:
        checks = [a for a in public['counselor_actions'] if a['reaction_check']=='yes']
        status = public['client_consistency']['status']
        bad = [x for x in public['issues'] if x['actor']=='counselor' and x['kind'] in ('leading','repeated_check','unsupported_assumption')]
        score = public['naturalness']['score']
        out.update(checks=(len(checks),1), any_check=(int(bool(checks)),1),
                   client_problem=(int(status=='problem'),int(status!='undetermined')),
                   counselor_problem=(int(bool(bad)),1), naturalness=(score or 0,int(score is not None)))
        located=[a for a in checks if a['target_turn'] is not None]
        out['immediate_checks']=(sum(a['turn']-a['target_turn']==1 for a in located),len(located))
        out['check_delay']=(sum(a['turn']-a['target_turn']-1 for a in located),len(located))
        out['major_problem']=(int(any(x['severity']==2 for x in public['issues'])),1)
        out['issue_severity']=(sum(x['severity'] for x in public['issues']),len(public['issues']))
        for k in METRICS:
            if k.startswith('issues_'):
                out[k]=(sum(x['kind']==k[len('issues_'):] for x in public['issues']),1)
    if private is not None:
        hidden = {h['turn'] for h in private['hidden_reactions'] if h['status'] in ('partial','unvoiced')}
        all_known = all(h['status']!='undetermined' for h in private['hidden_reactions'])
        fs = private['followups']
        evaluable = [f for f in fs if f['continuity']!='undetermined']
        changed = [f for f in fs if f['change'] in ('supported','unsupported')]
        disclosed = [f for f in fs if f['disclosure']!='undetermined']
        injected = [(f, doc['memory_input'].get(str(f['response_turn']), doc['memory_input'].get(f['response_turn']))) for f in fs]
        injected = [(f, m) for f, m in injected if m is not None]
        out.update(hidden_any=(int(bool(hidden)),int(bool(hidden) or all_known)),
                   hidden_new=(sum(t>=doc['fork_turn'] for t in hidden), int(all(h['status']!='undetermined' for h in private['hidden_reactions'] if h['turn']>=doc['fork_turn']))),
                   followup_any=(int(bool(fs)),1),
                   contradiction=(sum(f['continuity']=='contradiction' for f in evaluable),len(evaluable)),
                   unsupported_change=(sum(f['change']=='unsupported' for f in changed),len(changed)),
                   explicit_disclosure=(sum(f['disclosure']=='explicit' for f in disclosed),len(disclosed)),
                   target_injected=(sum(f['target_turn'] in m for f,m in injected),len(injected)))
        out['continuity_coverage']=(len(evaluable),len(fs))
        out['withheld_followups']=(sum(f['disclosure']=='withheld' for f in disclosed),len(disclosed))
        out['partial_disclosure']=(sum(f['disclosure']=='partial' for f in disclosed),len(disclosed))
        if public is not None:
            checks = [a for a in public['counselor_actions'] if a['reaction_check']=='yes' and a['target_turn'] is not None]
            known = {h['turn'] for h in private['hidden_reactions'] if h['status']!='undetermined'}
            checks = [a for a in checks if a['target_turn'] in known]
            out['hidden_target_checks']=(sum(a['target_turn'] in hidden for a in checks),len(checks))
    return out


def code_measure(doc):
    c = doc['code']; cov = c['coverage']; labels = c['labels']
    complete = cov.get('counselor_turns_with_labels',0)==cov.get('counselor_turns',0) and cov.get('counselor_turns',0)>0
    usage_complete=cov.get('steps_with_usage',0)==cov.get('steps',0) and cov.get('steps',0)>0 and 'total_tokens' in c['usage']
    return {'reported_checks':(len(c['checks']),int(complete)),
            'reported_interventions':(sum(labels.get(k,0) for k in ('suggest','interpret','task')),int(complete)),
            'reported_markers':(cov.get('reported_markers',0),int(complete)),
            'observer_following':(cov.get('observer_followed',0),cov.get('observer_calls',0)),
            'generation_tokens':(c['usage'].get('total_tokens',0),int(usage_complete))}


def generate(run):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    run = Path(run)
    manifest, docs = read(run/'manifest.json'), read(run/'normalized.json')
    by_id = {d['id']:d for d in docs}
    judged, failures, events, task_rows = {}, [], [], []
    for task in manifest['tasks']:
        base = run/'judgments'/task['id']
        result_path, status_path = base/'result.json', base/'status.json'
        status = read(status_path) if status_path.exists() else {'status':'pending'}
        state = status.get('status','pending')
        error = status.get('error','')
        if state=='completed':
            try:
                packet = read(run/'packets'/f'{task["id"]}.json')
                result = validate(read(result_path),packet,task['kind'])
                judged[(task['document'],task['repeat'],task['kind'])]=result
                for name in ('issues','counselor_actions','hidden_reactions','followups'):
                    for item in result.get(name,[]):
                        events.append({'document':task['document'],'repeat':task['repeat'],'kind':name,'data':json.dumps(item,ensure_ascii=False)})
            except Exception as exc:
                state, error = 'invalid', str(exc)
        if state!='completed':
            failures.append({'task':task['id'],'status':state,'error':error})
        task_rows.append({'task':task['id'],'document':task['document'],'kind':task['kind'],'repeat':task['repeat'],
                          'status':state,'elapsed_seconds':status.get('elapsed_seconds'),
                          'usage':json.dumps(status.get('usage'),ensure_ascii=False),'error':error})
    rows = []
    for doc in docs:
        samples = [('code',code_measure(doc))]
        for repeat in range(manifest['config']['repeats']):
            pub=judged.get((doc['id'],repeat,'public'));priv=judged.get((doc['id'],repeat,'private'))
            samples.append((repeat,measure(doc,pub,priv)))
        for repeat, metrics in samples:
            for metric,(num,den) in metrics.items():
                rows.append({'document':doc['id'],'batch':doc['batch'],'counselor':doc['counselor'],'client':doc['client'],
                             'branch':doc['branch'],'profile':doc['profile'],'repeat':repeat,'metric':metric,
                             'numerator':num,'denominator':den,'value':num/den if den else None})
    # First average repeated judgments of one dialogue; then average dialogues.
    per_doc=defaultdict(list)
    for row in rows:
        per_doc[(row['document'],row['metric'])].append(row)
    points=[]
    for (did,metric), rs in per_doc.items():
        values=[r['value'] for r in rs if r['value'] is not None]
        d=by_id[did]
        points.append({'document':did,'batch':d['batch'],'counselor':d['counselor'],'client':d['client'],'branch':d['branch'],
                       'profile':d['profile'],'metric':metric,'value':statistics.mean(values) if values else None,
                       'n_ratings':len(rs),'n_evaluable_ratings':len(values),
                       'numerator':sum(r['numerator'] for r in rs if r['denominator']),
                       'denominator':sum(r['denominator'] for r in rs)})
    grouped=defaultdict(list)
    for p in points:
        grouped[(p['batch'],p['counselor'],p['client'],p['branch'],p['metric'])].append(p)
    summary=[]
    # Include absent metrics as NA, never as zero.
    for batch in manifest['batches']:
        for branch in ('provided','withheld'):
            n_expected=sum(d['batch']==batch['id'] and d['branch']==branch for d in docs)
            for metric in METRICS:
                key=(batch['id'],batch['counselor'],batch['client'],branch,metric)
                ps=grouped.get(key,[]);vs=[p['value'] for p in ps if p['value'] is not None]
                num=sum(p['numerator'] for p in ps);den=sum(p['denominator'] for p in ps)
                summary.append(dict(zip(('batch','counselor','client','branch','metric'),key),
                                    value=statistics.mean(vs) if vs else None, median=statistics.median(vs) if vs else None,
                                    n_dialogues=n_expected,n_evaluable_dialogues=len(vs),
                                    n_ratings=sum(p['n_ratings'] for p in ps),numerator=num,denominator=den,
                                    pooled_value=num/den if den else None,units=METRICS[metric][1]))
    differences=[]
    for batch in manifest['batches']:
        for metric in METRICS:
            matched=defaultdict(dict)
            for p in points:
                if p['batch']==batch['id'] and p['metric']==metric and p['value'] is not None:
                    matched[p['profile']][p['branch']]=p['value']
            ds=[v['provided']-v['withheld'] for v in matched.values() if set(v)=={'provided','withheld'}]
            differences.append({'batch':batch['id'],'counselor':batch['counselor'],'client':batch['client'],'metric':metric,
                                'n_pairs':len(ds),'provided_minus_withheld':statistics.mean(ds) if ds else None})
    csv_write(run/'metrics.csv',summary);csv_write(run/'dialogue_metrics.csv',points)
    csv_write(run/'ratings.csv',rows);csv_write(run/'events.csv',events);csv_write(run/'tasks.csv',task_rows)
    csv_write(run/'paired_differences.csv',differences)
    write(run/'summary.json',{'metrics':summary,'paired_differences':differences,'incomplete_tasks':failures,
                             'definitions':METRICS,'complete':not failures})
    figures=run/'figures';figures.mkdir(exist_ok=True)
    for name, metrics in [('opportunities',['reported_interventions','checks','hidden_any','followup_any','hidden_target_checks','reported_markers']),
                           ('outcomes',['client_problem','contradiction','unsupported_change','naturalness','counselor_problem','explicit_disclosure'])]:
        fig,axes=plt.subplots(3,2,figsize=(max(12,len(manifest['batches'])*2.6),13))
        labels=[b['counselor']+'\n'+b['client'].replace('Q3compare_memory','Q3cmp')+'\n'+b['id'][-6:] for b in manifest['batches']]
        for ax,metric in zip(axes.flat,metrics):
            for bi,branch in enumerate(('provided','withheld')):
                xs=np.arange(len(labels))+(bi-.5)*.34
                color=('#2878B5','#E07A35')[bi]
                for i,batch in enumerate(manifest['batches']):
                    s=next(s for s in summary if s['batch']==batch['id'] and s['branch']==branch and s['metric']==metric)
                    val=s['value'];unit=METRICS[metric][1];factor=100 if unit=='rate' else 1
                    if val is None:
                        ax.text(xs[i],0,'N/A',rotation=90,ha='center',va='bottom',fontsize=8,color=color)
                    else:
                        ax.bar(xs[i],val*factor,width=.3,color=color,alpha=.7,label=branch if i==0 else None)
                        ax.annotate(f'{val*factor:.1f}\nn={s["n_evaluable_dialogues"]}/{s["n_dialogues"]}',(xs[i],val*factor),xytext=(0,4),textcoords='offset points',ha='center',va='bottom',fontsize=7)
                        vs=[p['value']*factor for p in points if p['batch']==batch['id'] and p['branch']==branch and p['metric']==metric and p['value'] is not None]
                        jitter=np.linspace(-.09,.09,len(vs)) if vs else []
                        ax.scatter(xs[i]+jitter,vs,s=12,color='black',alpha=.5,zorder=3)
            ax.set_title(METRICS[metric][0],fontsize=10)
            ax.set_xticks(np.arange(len(labels)),labels,fontsize=7)
            ax.set_xlim(-.6,len(labels)-.4)
            unit=METRICS[metric][1]
            ax.set_ylabel({'rate':'% (mean of dialogue rates)','count':'per dialogue','score':'score'}[unit],fontsize=8)
            ax.set_ylim(0,120 if unit=='rate' else 6 if unit=='score' else max(1,ax.get_ylim()[1]*1.22))
            ax.grid(axis='y',alpha=.2)
        from matplotlib.patches import Patch
        fig.legend(handles=[Patch(color='#2878B5',label='Memory provided'),Patch(color='#E07A35',label='Memory withheld')],loc='upper center',ncol=2)
        fig.suptitle(('DRAFT / INCOMPLETE - ' if failures else '')+'Post-fork evaluation | dots: dialogues; bars: means',y=.975,fontsize=12)
        fig.tight_layout(rect=(0,0,1,.95));fig.savefig(figures/(name+'.png'),dpi=160);fig.savefig(figures/(name+'.svg'));plt.close(fig)
    lines=['# 상담 대화 평가 결과','',f'- 상태: {"완료" if not failures else "미완료 — 실패/미평가를 0점으로 처리하지 않음"}',
           f'- 평가 설정: `{manifest["config"]["model"]}`, effort `{manifest["config"]["effort"]}`, 반복 {manifest["config"]["repeats"]}',
           f'- 평가 작업: {len(task_rows)-len(failures)}/{len(task_rows)} 유효. 대화 {len(docs)}개.',
           '- 범위: 내담자 n턴부터, 상담사 n+1턴부터. n턴 상담사 발언은 공통 생성이므로 비교 집계에서 제외.',
           '- 막대는 대화별 값의 평균, 점은 대화별 값. 반복 평가는 먼저 대화 안에서 평균. N/A는 부재/미평가이며 0점 아님.',
           '- 비공개 반응 전체맥락 지표에는 공통 구간이 포함됨. 공통 사건을 조건 간 독립 표본으로 해석하지 않음.',
           '- 모든 비율의 유효 대화 수와 사건 분자/분모는 metrics.csv에 있음. 반복 수>1이면 사건 분모에는 반복 판정이 포함됨.',
           '- 하나의 종합점수나 유의성 주장은 만들지 않음. 다른 배치의 생성 시점/프롬프트/프로필 차이는 버전 효과와 혼재할 수 있음.','',
           '| 상담사 / 내담자 | 배치 | 프로필 수 | 턴 / 분기 |','|---|---|---:|---|']
    for b in manifest['batches']:
        lines.append(f'| {b["counselor"]} / {b["client"]} | {b["id"]} | {len(b["profiles"])} | {b["turns"]} / {b["fork_turn"]} |')
    if len({(b['turns'],b['fork_turn']) for b in manifest['batches']})>1:
        lines+=['','**비교 범위 차이:** 배치별 턴 수 또는 분기 시점이 다르다. 횟수 차이를 버전 성능 차이로 직접 해석하지 말 것.']
    if len({json.dumps(b['profile_hashes'],sort_keys=True) for b in manifest['batches']})>1:
        lines+=['','**프로필 구성 차이:** 배치별 프로필 목록/내용이 다르다. 동일 구성으로 제한한 후 비교할 것.']
    lines+=['','## 지표별 결과','', '| 버전 / 조건 | 지표 | 평균 | 유효 대화 |','|---|---|---:|---:|']
    for s in summary:
        value='N/A' if s['value'] is None else f'{s["value"]*100:.1f}%' if s['units']=='rate' else f'{s["value"]:.2f}'
        lines.append(f'| {s["counselor"]} / {s["client"]} / {s["branch"]} | {s["metric"]} | {value} | {s["n_evaluable_dialogues"]}/{s["n_dialogues"]} |')
    lines+=['','![상황 및 기회](figures/opportunities.png)','', '![연속성 및 공개 대화](figures/outcomes.png)','',
            '## 검토할 제한','', '실험 상황의 발생이나 공개 빈도 자체를 품질로 간주하지 않는다. LLM 판정도 독립적인 임상 정답이 아니다.',
            '동일 프로필의 반복/분기는 독립 표본이 아니다. paired_differences.csv는 같은 배치·프로필에서 두 조건 모두 평가 가능한 경우만 비교한다.',
            '평가가 없는 경우 자연스러움/문제율을 추정하지 않는다. 원문 근거는 events.csv와 judgments/에 보존된다.']
    if failures:
        lines+=['','### 미완료 작업','']+[f'- {f["task"]}: {f["status"]} — {f["error"]}' for f in failures]
    (run/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return len(failures)
