import sys,json,math,itertools
sys.path.insert(0,'scripts/win5_bt')
import numpy as np, pandas as pd
from select_features import DEV_MONTHS, HOLDOUT_MONTHS
from backtest import parse_yen
S=sys.argv[1]
df=pd.read_pickle(f'{S}/pc.pkl'); df['month']=df.date.str[:6]
days={json.loads(l)['date']:json.loads(l) for l in open('win5_bt_data/win5_days.jsonl')}
frames={}
for ds,i in days.items():
    sub=[df[df.race_id==r] for r in i['race_ids']]
    if all(len(s) and (s.finish==1).any() for s in sub): frames[ds]=sub
toi=lambda s:int(str(s).replace(',',''))
dev=[d for d in sorted(frames) if d[:6] in DEV_MONTHS]; hold=[d for d in sorted(frames) if d[:6] in HOLDOUT_MONTHS]
X=[];Y=[];pools=[];sales=[]
for ds in dev:
    i=days[ds]
    if not i.get('hit_tickets') or toi(i['hit_tickets'])==0: continue
    X.append(sum(math.log(s.loc[s.finish==1,'q_m'].iloc[0]) for s in frames[ds])); Y.append(math.log(toi(i['hit_tickets'])/toi(i['sales_tickets'])))
    pools.append(parse_yen(i['payout'])*toi(i['hit_tickets'])); sales.append(toi(i['sales_tickets']))
a,b=np.polyfit(X,Y,1); xmin,xmax=min(X),max(X)
POOL,SALES=float(np.median(pools)),float(np.median(sales))
print(f'share a={a:.3f} b={b:.3f}  DEV Σlog q range [{xmin:.1f},{xmax:.1f}]  pool/sales={POOL/SALES:.3f}')
def run(dates,theta,budget=100,topk=9,minp=0.0):
    rows=[]
    for ds in dates:
        sub=frames[ds]
        cand=[]
        for s in sub:
            s=s.sort_values('pc',ascending=False).head(topk)
            cand.append(list(zip(s.umaban.astype(int),s.pc,np.log(s.q_m))))
        ch=[]
        for c in itertools.product(*cand):
            x=sum(t[2] for t in c)
            if x<xmin or x>xmax: continue          # 配当推定式の外挿はしない
            pc=math.prod(t[1] for t in c)
            if pc<minp: continue
            est=min(POOL/(math.exp(a*x+b)*SALES),6e8)
            ev=pc*est/100
            if ev>=theta: ch.append((ev,tuple(t[0] for t in c),pc,est))
        ch.sort(reverse=True); ch=ch[:budget]
        win=tuple(int(s.loc[s.finish==1,'umaban'].iloc[0]) for s in sub)
        hit=any(c[1]==win for c in ch); pay=parse_yen(days[ds].get('payout')) or 0
        rows.append(dict(date=ds,points=len(ch),hit=hit,ret=pay if hit else 0,p_hit=sum(c[2] for c in ch),
                         exp_ret=sum(c[2]*c[3] for c in ch)))
    e=pd.DataFrame(rows); cost=e.points.sum()*100
    return e,dict(bet_days=int((e.points>0).sum()),days=len(e),points=int(e.points.sum()),hits=int(e.hit.sum()),
                  exp_hits=round(e.p_hit.sum(),2),cost=int(cost),ret=int(e.ret.sum()),
                  roi=round(e.ret.sum()/cost,3) if cost else None,model_exp_roi=round(e.exp_ret.sum()/cost,3) if cost else None)
for minp in (0.0,1e-4,3e-4):
    for th in (1.0,1.2,1.5,2.0):
        print('DEV  minp',minp,'θ',th,run(dev,th,minp=minp)[1])
print('---- HOLDOUT ----')
for minp in (0.0,1e-4,3e-4):
    for th in (1.0,1.2,1.5,2.0):
        e,sm=run(hold,th,minp=minp); print('HOLD minp',minp,'θ',th,sm)
        e.to_csv(f'{S}/hold_ev_{minp}_{th}.csv',index=False)
