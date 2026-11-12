import json,glob,os,re,sys
from collections import Counter
MARK=re.compile(r'(아까|앞서|처음에|처음|그때|전에|이전|하셨던|말씀하신|말씀하셨|하셨잖|하셨는데|하셨죠|하셨을 때)')
def grams(s,n=4):
    s=re.sub(r'[^가-힣A-Za-z0-9]','',s); return {s[i:i+n] for i in range(len(s)-n+1)}
def load(b):
    j=json.load(open(f'runs/profile_batches/{b}/job.json'));out={}
    for r in j['rows']:
        s=json.load(open(f"runs/paired/{r['session_id']}/session.json"))
        current=f"{s.get('client_storage_dir','')}/{r['version']}/{r['session_id']}/memory_state.json"
        fs=[current] if os.path.isfile(current) else glob.glob(f"runs/paired/{r['session_id']}/client_attempts/*/{r['version']}/*/memory_state.json")
        out[(r['version'],r['profile_id'])]=json.load(open(max(fs,key=os.path.getmtime)))['records'][:20]
    return out
def events(recs):
    U={x['turn']:x['utterance'] for x in recs}; ev=[]
    for x in recs:
        t=x['turn']; c=x['counselor_utterance']
        if t<4 or not MARK.search(c): continue
        cg=grams(c)-grams(U.get(t-1,''))
        best=None
        for s in range(1,t-1):
            k=len(cg & grams(U[s]))
            if k>=3 and (best is None or k>best[1]): best=(s,k)
        if best: ev.append((t,best[0],best[1]))
    return ev
if __name__=='__main__':
    for b in sys.argv[1:]:
        D=load(b); c=Counter(); prof=Counter()
        for (v,p),recs in D.items():
            e=events(recs); c[v]+=len(e); prof[v]+=bool(e)
        print(b[:12],dict(c),'대화수',dict(prof))
