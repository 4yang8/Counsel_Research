import json,sys,numpy as np,os
sys.path.insert(0,'experiments/trajectory_similarity')
from plot_similarity import embed
SP=str(__import__('pathlib').Path(__file__).resolve().parent)+'/cache_'
V=['Q3inner_memory','inner_memory_no']
B={'28cf48':'batch_28cf4899dde548a59f3f','0d9d6d':'batch_0d9d6d8d8c9a465a814f','c9c8c4':'batch_c9c8c4f98fef4f7781c6','08f865':'batch_08f865c61ebd4284a5ae'}
def get(short):
    f=SP+f'win_{short}.npz'
    if os.path.exists(f): z=np.load(f); return z['cl'],z['ln']
    j=json.load(open(f'runs/profile_batches/{B[short]}/job.json'))
    D={}
    for r in j['rows']:
        s=json.load(open(f"runs/paired/{r['session_id']}/session.json"))
        D[(r['version'],r['profile_id'])]=[x['utterance'] for x in s['steps'] if x['actor']=='client'][:20]
    P=sorted({p for _,p in D}); texts=[t for v in V for p in P for t in D[(v,p)]]
    X=embed(texts,'http://192.168.0.32:8002/v1','Qwen/Qwen3-Embedding-4B',32,180).reshape(2,len(P),20,-1)
    ln=np.array([[[len(t) for t in D[(v,p)]] for p in P] for v in V])
    np.savez(f,cl=X,ln=ln); return X,ln
def cen(X,a,b):
    C=X[:,a:b].mean(1); C/=np.linalg.norm(C,axis=1,keepdims=True); n=len(C)
    return (C@C.T)[~np.eye(n,dtype=bool)].mean()
def late(X): return cen(X,15,20)
rng=np.random.default_rng(0)
print('batch   16-20구간중심 mem/no  diff   perm95  p   | 1-5→16-20 상승 mem/no | 후반 발화길이 mem/no | LOO diff 범위')
for s in B:
    cl,ln=get(s)
    m,n=late(cl[0]),late(cl[1]); d=m-n
    null=[]
    for _ in range(5000):
        f=rng.random(10)<.5
        A=np.where(f[:,None,None],cl[1],cl[0]);Bx=np.where(f[:,None,None],cl[0],cl[1]); null.append(late(A)-late(Bx))
    null=np.abs(null)
    up=(late(cl[0])-cen(cl[0],0,5), late(cl[1])-cen(cl[1],0,5))
    loo=[late(np.delete(cl[0],i,0))-late(np.delete(cl[1],i,0)) for i in range(10)]
    print(f"{s}  {m:.3f}/{n:.3f}  {d:+.3f}  ±{np.quantile(null,.95):.3f}  {(null>=abs(d)).mean():.2f} | +{up[0]:.3f}/+{up[1]:.3f} | {ln[0,:,15:].mean():.0f}/{ln[1,:,15:].mean():.0f}자 | {min(loo):+.3f}~{max(loo):+.3f}")
