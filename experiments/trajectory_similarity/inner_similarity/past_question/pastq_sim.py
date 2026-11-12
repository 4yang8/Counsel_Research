import sys,json,numpy as np
from pathlib import Path; sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,'experiments/trajectory_similarity')
from pastq import load,events
from plot_similarity import embed
B=['batch_28cf4899dde548a59f3f','batch_0d9d6d8d8c9a465a814f','batch_c9c8c4f98fef4f7781c6','batch_08f865c61ebd4284a5ae']
V=['Q3inner_memory','inner_memory_no']
def inner_all(x): return '\n'.join(str(v) for part in ('appraisal','turn_goal','response_plan') for v in x['inner'][part].values()) or '없음'
E=[]  # (batch, version, profile, t, s, resp, inner_s, utt_s)
for b in B:
    D=load(b)
    for (v,p),recs in D.items():
        R={x['turn']:x for x in recs}
        for t,s,k in events(recs):
            E.append((b,v,p,t,s,R[t]['utterance'],inner_all(R[s]),R[s]['utterance']))
texts=[e[5] for e in E]+[e[6] for e in E]+[e[7] for e in E]
X=embed(texts,'http://192.168.0.32:8002/v1','Qwen/Qwen3-Embedding-4B',16,180)
n=len(E); resp,inn,utt=X[:n],X[n:2*n],X[2*n:]
cond=np.array([e[1]==V[0] for e in E]); unit=[(e[0],e[2]) for e in E]; prof=np.array([e[2] for e in E])
a_inner=(resp*inn).sum(1); a_utt=(resp*utt).sum(1)
def stats(c):
    out={}
    for val,name in ((True,'mem'),(False,'no')):
        idx=np.where(c==val)[0]
        S=resp[idx]@resp[idx].T; P=prof[idx]
        mask=(P[:,None]!=P[None,:]) & np.triu(np.ones_like(S,dtype=bool),1)
        out[name]=dict(div=S[mask].mean(), inner=a_inner[idx].mean(), utt=a_utt[idx].mean(), n=len(idx))
    return out
obs=stats(cond)
units=sorted(set(unit)); rng=np.random.default_rng(0); null={'div':[],'inner':[],'utt':[]}
for _ in range(5000):
    flip={u:rng.random()<.5 for u in units}
    c=np.array([cond[i]^flip[unit[i]] for i in range(n)])
    st=stats(c)
    for k in null: null[k].append(st['mem'][k]-st['no'][k])
for k in ('div','inner','utt'):
    d=obs['mem'][k]-obs['no'][k]; nl=np.abs(np.array(null[k]))
    print(f"{k:6} mem {obs['mem'][k]:.3f} (n={obs['mem']['n']}) / no {obs['no'][k]:.3f} (n={obs['no']['n']})  diff {d:+.3f}  perm p={(nl>=abs(d)).mean():.2f}")
lag=np.array([e[3]-e[4] for e in E])
for lo in (4,):
    idx=lag>=lo
    print(f"lag>={lo}: mem n={int((idx&cond).sum())}, no n={int((idx&~cond).sum())}; inner-align mem {a_inner[idx&cond].mean():.3f} / no {a_inner[idx&~cond].mean():.3f}; utt-align mem {a_utt[idx&cond].mean():.3f} / no {a_utt[idx&~cond].mean():.3f}")
