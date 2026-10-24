"""독립 재계산: before/after 카드의 values만 보고 calc_criteria v1대로 회사 합산을 다시 만든 뒤 after.dilution과 대조."""
import json, sys, collections
AS_OF = '2026-10-22'
B = json.load(open(sys.argv[1])); A = json.load(open(sys.argv[2]))

def cards(corp):
    out = {}
    for src in (B.get(corp, {}), A.get(corp, {})):
        for e in src.get('live_events', []):
            out[e['rcept_no']] = e
    return out

def recompute(corp):
    cs = cards(corp)
    live_r, live_c = [], []
    for e in cs.values():
        v = e['values']
        if e['type'] == '유상증자':
            if v.get('listing_date') and v['listing_date'] > AS_OF:
                live_r.append(e)
        else:
            end = v.get('conv_end') or v.get('maturity')
            if end and end > AS_OF:
                live_c.append(e)
    # 정의 v1: 가장 최근 날짜의 CB 전부(같은 날 여러 건). 형제 중복은 cb-v4 old_cb_shares에서 제거됨
    last = max(e['rcept_dt'] for e in live_c) if live_c else None
    cbs = [e for e in live_c if e['rcept_dt'] == last]
    live = sorted(live_r + cbs, key=lambda e: (e['rcept_dt'], e['rcept_no']))
    if not live:
        return None
    basis = live[-1]
    sb = basis['values']['shares_before']
    # 단위 불일치 (감소만 독립 재현; 증가는 전체 공시가 없어 재현 불가 → after 값 그대로 신뢰하되 표시)
    mism = []
    for a, b in zip(live, live[1:]):
        x, y = a['values']['shares_before'], b['values']['shares_before']
        if x and y and y < x / 1.5:
            mism.append((a['rcept_no'], b['rcept_no']))
    use = [basis] if mism else live
    def ns(e, worst=False):
        v, f = e['values'], e['flags']
        if e['type'] == '유상증자':
            return 0 if f.get('preferred_only') else (v['new_shares'] or 0)
        c = (e['calc'].get('worst_shares') if worst else None) or v['conv_shares'] or 0
        # 옛 CB 잔량은 같은 날 CB들이 같은 옛 사채를 적으므로 한 번만: 마지막 CB 것만
        last_cb = max((x for x in use if x['type']=='CB'), key=lambda x: x['rcept_no'])
        return c + ((v['old_cb_shares'] or 0) if e is last_cb else 0)
    new = sum(ns(e) for e in use); worst = sum(ns(e, True) for e in use)
    pct = lambda n: round(n / (sb + n) * 100, 1) if sb and sb + n else None
    return {'live': sorted(e['rcept_no'] for e in live), 'basis': basis['rcept_no'], 'shares_before': sb,
            'new_shares': new, 'max_pct': pct(new), 'worst_pct': pct(worst), 'mism': mism}

rows, diff = [], []
for corp, a in A.items():
    me = recompute(corp); d = a['dilution']
    got = None if d is None else {'live': sorted(e['rcept_no'] for e in a['live_events']), 'basis': d['basis_rcept_no'],
           'shares_before': d['shares_before'], 'new_shares': d['new_shares'], 'max_pct': d['max_pct'], 'worst_pct': d['worst_pct'],
           'mism': a['flags'].get('unit_mismatch')}
    rows.append((corp, a['corp_name'], me, got))
    if (me is None) != (got is None):
        diff.append((corp, a['corp_name'], 'live없음/있음', me, got)); continue
    if me is None: continue
    for k in ('live', 'basis', 'shares_before', 'new_shares', 'max_pct', 'worst_pct'):
        if me[k] != got[k]:
            diff.append((corp, a['corp_name'], k, me[k], got[k]))
print('회사', len(A), '합산 있음(after)', sum(1 for r in rows if r[3]), '불일치 회사', len({d[0] for d in diff}), '불일치 칸', len(diff))
print(collections.Counter(d[2] for d in diff))
json.dump(diff, open('diff.json', 'w'), ensure_ascii=False, indent=1)
json.dump({r[0]: {'name': r[1], 'mine': r[2], 'db': r[3]} for r in rows}, open('all.json', 'w'), ensure_ascii=False)
