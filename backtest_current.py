# -*- coding: utf-8 -*-
"""
현재 설정 기준 백테스팅
- 필터 14개 커버율
- 추천 풀(POOL_SIZE개) 포함율 (매 회차 직전 데이터로 풀 생성)
"""
import csv
import os
from collections import Counter, defaultdict

# 스크립트 위치 기준 상대경로 — 어느 작업 디렉터리에서 실행해도 동작한다.
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lotto645_전체.csv')

# 운영 코드(lotto.py / app.py)와 동일한 가중치·풀 크기를 사용해야 검증 의미가 있다.
W_SKIP = 1.0
W_Z    = 1.0
W_PAIR = 1.0
POOL_SIZE = 15   # 프로덕션 기준 (커밋 c21264a에서 13 -> 15 확대)

PRIMES  = {2,3,5,7,11,13,17,19,23,29,31,37,41,43}
SQUARES = {1,4,9,16,25,36}
DOUBLES = {11,22,33,44}

def load_data():
    data = []
    with open(CSV_PATH, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            nums = sorted([int(row[f'번호{i}']) for i in range(1,7)])
            data.append({'회차': int(row['회차']), '번호': nums})
    data.sort(key=lambda x: x['회차'])
    return data

def ac(n):
    d = set()
    for i in range(len(n)):
        for j in range(i+1,len(n)): d.add(n[j]-n[i])
    return len(d)-(len(n)-1)

def zone_max(n):
    return max(sum(1 for x in n if lo<=x<=hi)
               for lo,hi in [(1,9),(10,19),(20,29),(30,39),(40,45)])

def consec_runs(n):
    runs, run = [], 1
    for i in range(1,len(n)):
        if n[i]-n[i-1]==1: run+=1
        else:
            if run>=2: runs.append(run)
            run=1
    if run>=2: runs.append(run)
    return runs

# ── 현재 스크린샷 기준 필터 ──────────────────────────────────────
def apply_filters(nums):
    n   = sorted(nums)
    s   = sum(n)
    odd = sum(1 for x in n if x%2==1)
    hi  = sum(1 for x in n if x>=23)
    ends= [x%10 for x in n]
    ec  = Counter(ends)
    es  = sum(ends)
    ac_v= ac(n)
    runs= consec_runs(n)
    prime_c  = sum(1 for x in n if x in PRIMES)
    sq_c     = sum(1 for x in n if x in SQUARES)
    double_c = sum(1 for x in n if x in DOUBLES)
    mul3_c   = sum(1 for x in n if x%3==0)
    mul5_c   = sum(1 for x in n if x%5==0)
    comp_c   = sum(1 for x in n if x > 1 and x not in PRIMES)
    zm       = zone_max(n)
    max_end  = max(ec.values())

    results = {}
    results['F01 번호총합 80~190']      = 80 <= s <= 190
    results['F02 AC값 6~10']            = 6 <= ac_v <= 10
    results['F03 홀짝 1~5개']           = odd in {1,2,3,4,5}
    results['F04 고저 1~5개']           = hi in {1,2,3,4,5}
    results['F05 끝수합 14~35']         = 14 <= es <= 35
    # 같은 끝수: 없음/2개/2쌍2만 허용
    if max_end == 1:   end_key = 'none'
    elif max_end == 2:
        pairs = sum(1 for v in ec.values() if v==2)
        end_key = {1:'2', 2:'2pair2', 3:'2pair3'}.get(pairs, 'other')
    elif max_end == 3: end_key = '3'
    elif max_end == 4: end_key = '4'
    else:              end_key = 'other'
    results['F06 같은끝수 none/2/2쌍2'] = end_key in {'none','2','2pair2'}
    results['F07 동일구간 2~3개']       = zm in {2,3}
    # 연속: 없음 or 2연번1쌍
    if not runs:                              c_key = 'none'
    elif len(runs)==1 and runs[0]==2:         c_key = '2x1'
    elif len(runs)==2 and all(r==2 for r in runs): c_key = '2x2'
    elif len(runs)==1 and runs[0]==3:         c_key = '3x1'
    else:                                     c_key = 'other'
    results['F08 연속 없음/2x1']        = c_key in {'none','2x1'}
    results['F09 소수 0~3개']           = prime_c in {0,1,2,3}
    results['F10 제곱수 0~2개']         = sq_c in {0,1,2}
    results['F11 합성수 3~5개']         = comp_c in {3,4,5}
    results['F12 동형수 0~1개']         = double_c in {0,1}
    results['F13 3배수 1~3개']          = mul3_c in {1,2,3}
    results['F14 5배수 0~2개']          = mul5_c in {0,1,2}
    return results

# ── 점수 및 추천 풀 ───────────────────────────────────────────────
def compute_scores(data):
    total = len(data)
    latest_round = data[-1]['회차']
    all_nums = [n for d in data for n in d['번호']]
    freq = Counter(all_nums)
    avg_f = len(all_nums)/45
    std_f = (sum((c-avg_f)**2 for c in freq.values())/45)**0.5 or 1

    skips, last = defaultdict(list), {}
    for d in data:
        for n in d['번호']:
            if n in last: skips[n].append(d['회차']-last[n])
            last[n] = d['회차']

    avg_skip  = {n: sum(skips[n])/len(skips[n]) for n in range(1,46) if skips[n]}
    last_seen = {n: 0 for n in range(1,46)}
    for d in data:
        for n in d['번호']: last_seen[n] = d['회차']
    waiting = {n: latest_round-last_seen[n] for n in range(1,46)}

    # 페어 친화도 — 프로덕션(lotto.py/app.py)과 동일하게 포함해야 검증 대표성이 있다.
    # 가중치 평탄화 이후 이 항의 기여도가 2.4% -> 6.7%로 올라, 누락 시 측정 대상이
    # 실제 출하 점수함수와 달라진다.
    # 닫힌형. 유도와 근거는 lotto.compute_scores 주석 참조.
    # 독립 정보가 아니라 빈도의 선형 재표현이다(Z역 항과 Spearman -0.996).
    pair_affinity = {n: 15.0*freq[n]/total - 2.0 for n in range(1,46)}

    scores = {}
    for n in range(1,46):
        avg_sk = avg_skip.get(n, total/6)
        scores[n] = ((waiting[n]/avg_sk)*W_SKIP
                     + (-(freq[n]-avg_f)/std_f)*W_Z
                     + pair_affinity[n]*W_PAIR)
    return scores

def get_pool(data, size=POOL_SIZE):
    scores = compute_scores(data)
    ranked = sorted(scores.items(), key=lambda x:-x[1])
    zones  = [(1,9),(10,19),(20,29),(30,39),(40,45)]
    pool   = []
    for lo,hi in zones:
        best = next((n for n,_ in ranked if lo<=n<=hi and n not in pool), None)
        if best: pool.append(best)
    for n,_ in ranked:
        if len(pool) >= size: break
        if n not in pool: pool.append(n)
    pool_set = set(pool)
    zone_must = pool[:5]
    if sum(1 for n in pool if n%2==1) < 5:
        for n,_ in ranked:
            if n%2==1 and n not in pool_set:
                removable = [p for p in pool if p%2==0 and p not in zone_must]
                if removable:
                    pool.remove(min(removable, key=lambda x: scores[x]))
                    pool.append(n); pool_set = set(pool)
                    if sum(1 for x in pool if x%2==1) >= 5: break
    return set(pool)

# ════════════════════════════════════════════════════════════════
# 실행부는 반드시 __main__ 가드 안에 둔다 — accumulate.py가 get_pool/apply_filters를
# import해 매주 채점에 쓰는데, 모듈 최상단에서 실행되면 import 한 번에 전수
# 백테스팅(수 분)이 돌아버린다.
def main():
    data = load_data()
    total = len(data)

    print("=" * 60)
    print(f"  백테스팅 — 현재 필터 기준  ({total}회차 전체)")
    print("=" * 60)

    # ── 1. 필터별 커버율 ─────────────────────────────────────────
    filter_pass = defaultdict(int)
    all_pass = 0
    filter_names = None

    for d in data:
        r = apply_filters(d['번호'])
        if filter_names is None: filter_names = list(r.keys())
        for k,v in r.items():
            if v: filter_pass[k] += 1
        if all(r.values()): all_pass += 1

    print(f"\n[1] 필터별 커버율\n")
    print(f"  {'필터':<28} {'통과':>5} {'커버율':>7}  판정")
    print("  " + "-"*52)
    for fn in filter_names:
        cnt = filter_pass[fn]
        pct = cnt/total*100
        judge = "✅ 유효" if pct >= 85 else ("⚠️ 보통" if pct >= 70 else "❌ 과필터")
        print(f"  {fn:<28} {cnt:>5}회  {pct:>5.1f}%  {judge}")

    print(f"\n  ▶ 14개 필터 모두 통과: {all_pass}회 / {total}회  ({all_pass/total*100:.1f}%)")

    # ── 2. 추천 풀 포함율 (매 회차 직전 데이터로 풀 생성) ────────
    print(f"\n[2] 추천 {POOL_SIZE}개 풀 포함율 (회차별 당첨번호가 풀에 몇 개 포함?)\n")

    MIN_DATA = 50  # 최소 50회차 이후부터 분석
    hit_dist = Counter()
    match_6 = match_5 = match_4 = match_3 = 0

    for i in range(MIN_DATA, total):
        pool = get_pool(data[:i])
        actual = set(data[i]['번호'])
        hits = len(actual & pool)
        hit_dist[hits] += 1
        if hits == 6: match_6 += 1
        if hits >= 5: match_5 += 1
        if hits >= 4: match_4 += 1
        if hits >= 3: match_3 += 1

    analyzed = total - MIN_DATA
    print(f"  분석 회차: {data[MIN_DATA]['회차']}회 ~ {data[-1]['회차']}회  ({analyzed}회차)\n")
    print(f"  {'풀 포함 개수':<14} {'횟수':>6} {'비율':>7}")
    print("  " + "-"*32)
    for k in sorted(hit_dist.keys(), reverse=True):
        pct = hit_dist[k]/analyzed*100
        bar = "█" * int(pct/2)
        print(f"  {k}개 포함{'':<7} {hit_dist[k]:>5}회  {pct:>5.1f}%  {bar}")

    print(f"\n  ▶ 당첨번호 6개 전부 풀에 포함: {match_6}회 ({match_6/analyzed*100:.1f}%)")
    print(f"  ▶ 5개 이상 포함:               {match_5}회 ({match_5/analyzed*100:.1f}%)")
    print(f"  ▶ 4개 이상 포함:               {match_4}회 ({match_4/analyzed*100:.1f}%)")
    print(f"  ▶ 3개 이상 포함:               {match_3}회 ({match_3/analyzed*100:.1f}%)")

    # ── 3. 필터 + 풀 동시 통과 ──────────────────────────────────
    print(f"\n[3] 필터 통과 + 풀 4개↑ 포함 동시 만족\n")
    both = 0
    for i in range(MIN_DATA, total):
        pool = get_pool(data[:i])
        actual = data[i]['번호']
        hits = len(set(actual) & pool)
        r = apply_filters(actual)
        if all(r.values()) and hits >= 4:
            both += 1

    print(f"  필터 전체 통과 & 풀 4개↑ 포함: {both}회 / {analyzed}회 ({both/analyzed*100:.1f}%)")
    print(f"\n분석 완료.")


if __name__ == '__main__':
    main()
