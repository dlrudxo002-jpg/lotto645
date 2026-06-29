# -*- coding: utf-8 -*-
"""
로또645 통합 분석 시스템
사용법:
  python lotto.py fetch             — 최신 당첨번호 자동 수집 + 추천 생성
  python lotto.py recommend         — 다음 회차 추천 생성
  python lotto.py analyze           — 전체 데이터 분석
  python lotto.py history           — 추천 이력 및 성과 조회
  python lotto.py status            — 현황 요약
  python lotto.py update <회차> <번1~6> <보너스>  — 수동 입력
"""
import csv, sys, json, os, random, urllib.request, urllib.error
from datetime import datetime
from collections import Counter, defaultdict

# ── 경로 ─────────────────────────────────────────────────────────
BASE    = os.path.dirname(os.path.abspath(__file__))
CSV     = os.path.join(BASE, 'lotto645_전체.csv')
HIST    = os.path.join(BASE, 'history.json')
REPORTS = os.path.join(BASE, 'reports')
os.makedirs(REPORTS, exist_ok=True)

# ── 상수 ─────────────────────────────────────────────────────────
PRIMES  = {2,3,5,7,11,13,17,19,23,29,31,37,41,43}
SQUARES = {1,4,9,16,25,36}
DOUBLES = {11,22,33,44}
CORNERS = {1,2,8,9, 6,7,13,14, 29,30,36,37, 34,35,41,42}
TRIANGLES = [
    {1,2,3,8,9,10}, {4,5,6,7,11,12,13},
    {29,30,31,36,37,38}, {33,34,35,40,41,42},
]
FROG_ZONES = [
    {1,8,15,22,29,36,43}, {2,9,16,23,30,37,44},
    {3,10,17,24,31,38,45}, {4,11,18,25,32,39},
    {5,12,19,26,33,40},   {6,13,20,27,34,41},
    {7,14,21,28,35,42},
]
API_URL = 'https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={}'

# ══════════════════════════════════════════════════════════════════
# 공통 함수
# ══════════════════════════════════════════════════════════════════
def load_data():
    data = []
    with open(CSV, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            nums = sorted([int(row[f'번호{i}']) for i in range(1,7)])
            data.append({'회차': int(row['회차']), '번호': nums, '보너스': int(row['보너스'])})
    data.sort(key=lambda x: x['회차'])
    return data

def load_history():
    if not os.path.exists(HIST): return {}
    with open(HIST, encoding='utf-8') as f: return json.load(f)

def save_history(h):
    with open(HIST, 'w', encoding='utf-8') as f:
        json.dump(h, f, ensure_ascii=False, indent=2)

def ac(n):
    d = set()
    for i in range(len(n)):
        for j in range(i+1,len(n)): d.add(n[j]-n[i])
    return len(d)-(len(n)-1)

def zone_max(n):
    return max(sum(1 for x in n if lo<=x<=hi)
               for lo,hi in [(1,9),(10,19),(20,29),(30,39),(40,45)])

def consec_ok(n):
    runs, run = [], 1
    for i in range(1,len(n)):
        if n[i]-n[i-1]==1: run+=1
        else:
            if run>=2: runs.append(run)
            run=1
    if run>=2: runs.append(run)
    return len(runs)==0 or (len(runs)==1 and runs[0]==2)

def passes(n):
    """14개 필터 — 현재 웹앱 기준 (backtest 36.8% 커버)"""
    s    = sum(n)
    odd  = sum(1 for x in n if x%2==1)
    hi   = sum(1 for x in n if x>=23)
    ends = [x%10 for x in n]
    ec   = Counter(ends)
    es   = sum(ends)
    mx_e = max(ec.values())

    if not (80<=s<=190):                                          return False
    if not (6<=ac(n)<=10):                                        return False
    if odd not in {1,2,3,4,5}:                                    return False
    if hi  not in {1,2,3,4,5}:                                    return False
    if not (14<=es<=35):                                           return False
    # 같은 끝수: none/2개/2pair2만 허용
    if mx_e == 1:   ek = 'none'
    elif mx_e == 2: ek = {1:'2',2:'2pair2',3:'2pair3'}.get(sum(1 for v in ec.values() if v==2),'other')
    else:           ek = 'other'
    if ek not in {'none','2','2pair2'}:                           return False
    if zone_max(n) not in {2,3}:                                  return False
    if not consec_ok(n):                                          return False
    if sum(1 for x in n if x in PRIMES) not in {0,1,2,3}:       return False
    if sum(1 for x in n if x in SQUARES) not in {0,1,2}:        return False
    if sum(1 for x in n if x>1 and x not in PRIMES) not in {3,4,5}: return False
    if sum(1 for x in n if x in DOUBLES) not in {0,1}:          return False
    if sum(1 for x in n if x%3==0) not in {1,2,3}:              return False
    if sum(1 for x in n if x%5==0) not in {0,1,2}:              return False
    return True

def compute_pair_triple(data):
    """페어/트리플 출현 빈도"""
    from itertools import combinations as icombs
    total = len(data)
    pf, tf = Counter(), Counter()
    for d in data:
        for p in icombs(d['번호'], 2): pf[p] += 1
        for t in icombs(d['번호'], 3): tf[t] += 1
    ep = total * 15 / (45*44/2)
    et = total * 20 / (45*44*43/6)
    return pf, tf, ep, et

def compute_scores(data):
    """스킵비율 × 5.0 + Z점수 × 1.5 + 페어친화도 × 1.0"""
    from itertools import combinations as icombs
    total        = len(data)
    latest_round = data[-1]['회차']
    all_nums     = [n for d in data for n in d['번호']]
    freq         = Counter(all_nums)
    avg_f        = len(all_nums)/45
    std_f        = (sum((c-avg_f)**2 for c in freq.values())/45)**0.5 or 1

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

    pf, _, ep, _ = compute_pair_triple(data)
    pair_affinity = {}
    for n in range(1,46):
        ratios = [pf[(min(n,m),max(n,m))]/ep for m in range(1,46) if m!=n]
        pair_affinity[n] = (sum(ratios)/len(ratios) - 1.0) * 2.0

    scores = {}
    for n in range(1,46):
        avg_sk = avg_skip.get(n, total/6)
        scores[n] = ((waiting[n]/avg_sk)*5.0
                     + (-(freq[n]-avg_f)/std_f)*1.5
                     + pair_affinity[n]*1.0)
    return scores, freq, waiting, avg_skip

def score_combo(combo, scores, pf, tf, ep, et):
    from itertools import combinations as icombs
    n = sorted(combo)
    base   = sum(scores[x] for x in n)
    pair_b = sum(pf.get(p,0)/ep - 1.0 for p in icombs(n,2))
    tri_b  = sum(tf.get(t,0)/et - 1.0 for t in icombs(n,3)) * 0.3
    return base + pair_b + tri_b

def select_candidates(data, scores, freq, waiting, avg_skip, target=15):
    """
    12~15개 유력번호 추리기
    기준 ①: 스킵비율 상위
    기준 ②: 구간(5구역) 균형 — 각 구역 최소 1개
    기준 ③: 홀짝 균형 — 풀 내 홀수 5~8개 (게임 생성 시 다양성 확보)
    """
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    zones  = [(1,9),(10,19),(20,29),(30,39),(40,45)]

    # 1단계: 각 구역에서 최고점 번호 1개씩 확보 (5개)
    pool = []
    covered = set()
    for lo, hi in zones:
        best = next((n for n,_ in ranked if lo<=n<=hi and n not in pool), None)
        if best:
            pool.append(best)
            covered.add(best)

    # 2단계: 점수 순으로 target개까지 채우기
    for n, _ in ranked:
        if len(pool) >= target: break
        if n not in pool:
            pool.append(n)

    # 3단계: 홀짝 균형 조정 — 홀수가 너무 적으면 교체
    odds_in_pool = sum(1 for n in pool if n%2==1)
    evens_in_pool = len(pool)-odds_in_pool
    # 홀수 5개 미만이면 낮은점수 짝수 → 높은점수 홀수로 교체
    if odds_in_pool < 5:
        pool_set = set(pool)
        for n, _ in ranked:
            if n%2==1 and n not in pool_set:
                # 가장 낮은 짝수(비구역필수) 제거
                removable = [p for p in pool if p%2==0 and p not in list(covered)[:5]]
                if removable:
                    worst = min(removable, key=lambda x: scores[x])
                    pool.remove(worst)
                    pool.append(n)
                    pool_set = set(pool)
                    odds_in_pool = sum(1 for x in pool if x%2==1)
                    if odds_in_pool >= 5: break

    pool.sort()
    return pool

def compute_wheel(pool, k=3):
    """최소 커버링 휠링 — k=3: 5등 보장, k=4: 4등 보장"""
    from itertools import combinations as ic
    pool = sorted(pool)
    n = len(pool)
    all_6  = list(ic(range(n), 6))
    cov    = [frozenset(ic(s, k)) for s in all_6]
    uncov  = set(ic(range(n), k))
    result = []
    while uncov:
        best_i = max(range(len(all_6)), key=lambda i: len(cov[i] & uncov))
        if not (cov[best_i] & uncov): break
        result.append(best_i)
        uncov -= cov[best_i]
    return [[pool[i] for i in all_6[wi]] for wi in result]

def grade_match(games, actual):
    actual_set = set(actual['번호'])
    bonus      = actual['보너스']
    results    = []
    for g in games:
        match   = len(set(g) & actual_set)
        b_match = bonus in g
        if   match==6:              grade="1등"
        elif match==5 and b_match:  grade="2등"
        elif match==5:              grade="3등"
        elif match==4:              grade="4등"
        elif match==3:              grade="5등"
        else:                       grade="낙첨"
        results.append({'game': g, 'match': match, 'grade': grade})
    return results

# ══════════════════════════════════════════════════════════════════
# 커맨드: fetch  — 자동 수집
# ══════════════════════════════════════════════════════════════════
def cmd_fetch(args):
    """동행복권 API로 최신 당첨번호 자동 수집"""
    data     = load_data()
    last_rnd = data[-1]['회차']

    print(f"\n  현재 DB 최신: {last_rnd}회차")
    print(f"  API 조회 시작...\n")

    added = 0
    rnd   = last_rnd + 1
    while True:
        url = API_URL.format(rnd)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as res:
                result = json.loads(res.read().decode('utf-8'))
        except Exception as e:
            print(f"  {rnd}회차 — 아직 발표 안됨 또는 오류 ({e})")
            break

        if result.get('returnValue') != 'success':
            print(f"  {rnd}회차 — 데이터 없음 (returnValue={result.get('returnValue')})")
            break

        nums  = sorted([result[f'drwtNo{i}'] for i in range(1,7)])
        bonus = result['bnusNo']
        date  = result.get('drwNoDate', '')

        # CSV 추가
        with open(CSV, 'a', encoding='utf-8', newline='') as f:
            csv.writer(f).writerow([rnd] + nums + [bonus])

        # 히스토리에 실제 결과 기록
        hist = load_history()
        key  = str(rnd)
        if key not in hist: hist[key] = {}
        hist[key]['actual']   = {'번호': nums, '보너스': bonus, '날짜': date}
        # 이전 회차 추천 채점
        prev_key = str(rnd-1)
        if prev_key in hist and 'recommend' in hist[prev_key]:
            hist[prev_key]['result'] = grade_match(hist[prev_key]['recommend'],
                                                   {'번호': nums, '보너스': bonus})
        save_history(hist)

        print(f"  ✓ {rnd}회차 ({date}): {nums}  보너스:{bonus}")
        added += 1
        rnd   += 1

    if added == 0:
        print(f"  → 새로운 회차 없음. DB가 최신 상태입니다.")
    else:
        print(f"\n  → {added}회차 추가 완료. 다음 회차 추천 생성 중...")
        data = load_data()
        cmd_recommend([], data=data, save=True)

# ══════════════════════════════════════════════════════════════════
# 커맨드: recommend
# ══════════════════════════════════════════════════════════════════
def cmd_recommend(args, data=None, save=False):
    if data is None: data = load_data()
    latest     = data[-1]
    prev_nums  = latest['번호']
    target_rnd = latest['회차'] + 1
    total      = len(data)

    scores, freq, waiting, avg_skip = compute_scores(data)

    # ── 12~15개 유력번호 추리기 ──────────────────────────────────
    pool = select_candidates(data, scores, freq, waiting, avg_skip, target=15)

    # 이전회차 이월수 중 점수 상위 1개 강제 포함
    best_carry = max(prev_nums, key=lambda n: scores[n])
    if best_carry not in pool:
        # 풀에서 가장 낮은 점수 번호와 교체
        worst = min(pool, key=lambda n: scores[n])
        pool.remove(worst)
        pool.append(best_carry)
        pool.sort()

    # ── 게임 생성 — 필터 통과 후 점수 상위 10개 ──────────────────
    from itertools import combinations as icombs
    pf, tf, ep, et = compute_pair_triple(data)
    candidates = [list(c) for c in icombs(pool, 6) if passes(list(c))]
    candidates.sort(key=lambda c: score_combo(c, scores, pf, tf, ep, et), reverse=True)
    games = candidates[:10]

    # ── 출력 ─────────────────────────────────────────────────────
    print(f"\n{'='*62}")
    print(f"  {target_rnd}회차 추천번호  (기준:{latest['회차']}회차까지 {total}회 데이터)")
    print(f"{'='*62}")
    print(f"  이전회차: {prev_nums}  보너스:{latest['보너스']}")

    print(f"\n  [유력번호 풀 {len(pool)}개] — 스킵비율·Z점수 기반 선별")
    print(f"  {pool}")

    print(f"\n  {'번호':>4}  {'skip비율':>8}  {'대기':>6}  {'전체출현':>8}  {'선정이유'}")
    print(f"  {'─'*55}")
    for n in pool:
        avg_sk = avg_skip.get(n, total/6)
        ratio  = waiting[n]/avg_sk
        z      = (freq[n]-len([x for d in data for x in d['번호']])/45) / \
                  (sum((c-len([x for d in data for x in d['번호']])/45)**2 for c in freq.values())/45)**0.5
        reasons = []
        if ratio >= 2.0:   reasons.append(f"초과대기{ratio:.1f}x")
        elif ratio >= 1.0: reasons.append(f"대기중{ratio:.1f}x")
        if n in prev_nums: reasons.append("이월수")
        if z < -1.5:       reasons.append("저빈도")
        zone_str = next(f"{lo}~{hi}구역" for lo,hi in [(1,9),(10,19),(20,29),(30,39),(40,45)] if lo<=n<=hi)
        reasons.append(zone_str)
        print(f"  {n:>4}번  {ratio:>7.2f}x  {waiting[n]:>5}회  {freq[n]:>7}회  {', '.join(reasons)}")

    print(f"\n  {'#':>2}  {'번호':<36}  {'합':>4}  {'홀:짝':>5}  {'AC':>3}  {'이월':>4}")
    print(f"  {'─'*60}")
    for i, g in enumerate(games, 1):
        carry = sum(1 for x in g if x in prev_nums)
        print(f"  {i:>2}  {str(g):<36}  {sum(g):>4}  "
              f"{sum(1 for x in g if x%2==1)}:{sum(1 for x in g if x%2==0)}  "
              f"{ac(g):>3}  {'있음' if carry else '─':>4}")

    if not games:
        print("  ⚠ 필터를 통과한 조합이 부족합니다. 후보풀을 확인하세요.")
        return []

    # ── 휠링 (5등 보장) ───────────────────────────────────────────
    wheel = compute_wheel(pool, k=3)
    w_pass = [g for g in wheel if passes(g)]
    print(f"\n  {'─'*60}")
    print(f"  [🛡 5등 보장 휠링]  총 {len(wheel)}장  (필터 통과: {len(w_pass)}장)")
    print(f"  풀 내 3개↑ 당첨번호 포함 시 → 5등(3개 일치) 이상 1장 확정")
    print(f"  {'─'*60}")
    print(f"  {'#':>2}  {'번호':<36}  {'합':>4}  {'홀:짝':>5}  {'AC':>3}  {'필터'}")
    print(f"  {'─'*60}")
    for i, g in enumerate(wheel[:20], 1):   # 상위 20장만 출력
        fmark = '✅' if passes(g) else '  '
        print(f"  {i:>2}  {str(sorted(g)):<36}  {sum(g):>4}  "
              f"{sum(1 for x in g if x%2==1)}:{sum(1 for x in g if x%2==0)}  "
              f"{ac(g):>3}  {fmark}")
    if len(wheel) > 20:
        print(f"  ... (이하 {len(wheel)-20}장 생략, CSV 저장 시 전체 포함)")

    # ── 저장 ─────────────────────────────────────────────────────
    if save:
        hist = load_history()
        key  = str(target_rnd)
        if key not in hist: hist[key] = {}
        hist[key].update({'recommend': games,
                          'wheel':     wheel,
                          'pool':      pool,
                          'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
                          'prev_round': latest['회차']})
        save_history(hist)

        path = os.path.join(REPORTS, f'recommend_{target_rnd}.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"{target_rnd}회차 추천번호\n생성:{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"유력풀({len(pool)}개): {pool}\n\n")
            for i,g in enumerate(games,1):
                f.write(f"게임{i:02d}: {g}  합:{sum(g)}  AC:{ac(g)}\n")
            f.write(f"\n[5등 보장 휠링 {len(wheel)}장]\n")
            for i,g in enumerate(wheel,1):
                fmark = '✅' if passes(g) else '  '
                f.write(f"휠링{i:03d}: {sorted(g)}  합:{sum(g)}  {fmark}\n")
        print(f"\n  → history.json / {path} 저장 완료")

    return games

# ══════════════════════════════════════════════════════════════════
# 커맨드: update  — 수동 입력
# ══════════════════════════════════════════════════════════════════
def cmd_update(args):
    if len(args) < 8:
        print("  사용법: python lotto.py update <회차> <번1> <번2> <번3> <번4> <번5> <번6> <보너스>")
        return
    rnd   = int(args[0])
    nums  = sorted([int(x) for x in args[1:7]])
    bonus = int(args[7])

    data = load_data()
    if rnd in {d['회차'] for d in data}:
        print(f"  ⚠ {rnd}회차 이미 존재.")
    else:
        with open(CSV, 'a', encoding='utf-8', newline='') as f:
            csv.writer(f).writerow([rnd]+nums+[bonus])
        print(f"  ✓ {rnd}회차 추가: {nums}  보너스:{bonus}")
        data = load_data()

    hist    = load_history()
    key     = str(rnd)
    if key not in hist: hist[key] = {}
    hist[key]['actual'] = {'번호': nums, '보너스': bonus, '날짜': datetime.now().strftime('%Y-%m-%d')}
    prev_key = str(rnd-1)
    if prev_key in hist and 'recommend' in hist[prev_key]:
        results = grade_match(hist[prev_key]['recommend'], {'번호': nums, '보너스': bonus})
        hist[prev_key]['result'] = results
        grades = [r['grade'] for r in results]
        best   = next((g for g in ['1등','2등','3등','4등','5등'] if g in grades), '낙첨')
        print(f"\n  [{rnd-1}회차 추천 채점결과]  최고등수: {best}")
        for i,r in enumerate(results,1):
            mark = " ★" if r['grade'] != '낙첨' else ""
            print(f"    게임{i:02d}: {r['game']}  {r['match']}개일치  {r['grade']}{mark}")
    save_history(hist)

    print(f"\n  → {rnd+1}회차 추천 생성 중...")
    cmd_recommend([], data=data, save=True)

# ══════════════════════════════════════════════════════════════════
# 커맨드: history
# ══════════════════════════════════════════════════════════════════
def cmd_history(args):
    hist = load_history()
    data = load_data()
    act_map = {d['회차']: d for d in data}
    if not hist:
        print("  기록 없음."); return

    print(f"\n{'='*65}")
    print(f"  추천 이력 및 성과")
    print(f"{'='*65}")

    total_games, grade_tally = 0, Counter()
    for key in sorted(hist.keys(), key=int, reverse=True)[:10]:
        rnd   = int(key)
        entry = hist[key]
        recs  = entry.get('recommend')
        act   = entry.get('actual')
        if not act and rnd in act_map:
            act = {'번호': act_map[rnd]['번호'], '보너스': act_map[rnd]['보너스']}
        pool  = entry.get('pool', [])

        print(f"\n  [{rnd}회차]  생성:{entry.get('generated','?')}")
        if pool: print(f"    유력풀: {pool}")
        if recs:
            if act:
                results = entry.get('result') or grade_match(recs, act)
                print(f"    실제당첨: {act['번호']}  보너스:{act['보너스']}")
                for i,r in enumerate(results,1):
                    grade_tally[r['grade']] += 1; total_games += 1
                    mark = " ★" if r['grade'] != '낙첨' else ""
                    print(f"    게임{i:02d}: {r['game']}  {r['match']}개  {r['grade']}{mark}")
            else:
                print(f"    추천완료 (결과 미입력 — fetch 또는 update로 입력)")
        else:
            print(f"    추천 없음")

    if total_games:
        print(f"\n{'─'*65}")
        print(f"  [전체 성과]  {total_games}게임")
        for g in ['1등','2등','3등','4등','5등','낙첨']:
            cnt = grade_tally[g]
            if cnt:
                bar = "█" * int(cnt/total_games*40)
                print(f"    {g}: {cnt:>4}회  {cnt/total_games*100:>5.1f}%  {bar}")

# ══════════════════════════════════════════════════════════════════
# 커맨드: analyze
# ══════════════════════════════════════════════════════════════════
def cmd_analyze(args):
    data   = load_data()
    total  = len(data)
    latest = data[-1]
    scores, freq, waiting, avg_skip = compute_scores(data)
    ranked = sorted(scores.items(), key=lambda x:-x[1])

    print(f"\n{'='*62}")
    print(f"  로또645 분석  ({data[0]['회차']}~{latest['회차']}회, 총 {total}회)")
    print(f"{'='*62}")

    print(f"\n  [초과대기 번호 — 평균 skip 1.5배 이상]")
    print(f"  {'번호':>4}  {'전체':>6}  {'평균skip':>8}  {'대기':>6}  {'비율':>6}")
    print(f"  {'─'*45}")
    for n, sc in ranked:
        avg_sk = avg_skip.get(n, total/6)
        ratio  = waiting[n]/avg_sk
        if ratio >= 1.5:
            print(f"  {n:>4}번  {freq[n]:>5}회  {avg_sk:>7.1f}회  {waiting[n]:>5}회  {ratio:>5.1f}x")

    print(f"\n  [최근 10회 이월수]")
    for i in range(max(1,len(data)-10), len(data)):
        prev  = set(data[i-1]['번호'])
        carry = sorted(prev & set(data[i]['번호']))
        print(f"    {data[i]['회차']}회: {data[i]['번호']}  이월:{carry or '없음'}")

    print(f"\n  [현재 유력번호 풀 (15개)]")
    pool = select_candidates(data, scores, freq, waiting, avg_skip, target=15)
    print(f"  {pool}")

    sums = [sum(d['번호']) for d in data]
    acs  = [ac(d['번호']) for d in data]
    print(f"\n  [지표 현황]")
    print(f"  합계 — 평균:{sum(sums)/len(sums):.1f}  최근:{sum(latest['번호'])}")
    print(f"  AC   — 최빈:{Counter(acs).most_common(1)[0][0]}  최근:{ac(latest['번호'])}")
    print(f"  필터 커버율: {sum(1 for d in data if passes(d['번호']))/total*100:.1f}%")

    path = os.path.join(REPORTS, f"analysis_{latest['회차']}.txt")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"분석 기준: {latest['회차']}회차  총 {total}회\n")
        f.write(f"유력풀: {pool}\n")
    print(f"\n  → {path} 저장 완료")

# ══════════════════════════════════════════════════════════════════
# 커맨드: status
# ══════════════════════════════════════════════════════════════════
def cmd_status(args):
    data   = load_data()
    hist   = load_history()
    latest = data[-1]
    total  = len(data)
    scores, freq, waiting, avg_skip = compute_scores(data)
    ranked = sorted(scores.items(), key=lambda x:-x[1])
    next_rnd  = latest['회차']+1
    has_rec   = str(next_rnd) in hist and 'recommend' in hist[str(next_rnd)]

    print(f"\n{'='*55}")
    print(f"  로또645 시스템 현황")
    print(f"{'='*55}")
    print(f"  DB:        1~{latest['회차']}회  (총 {total}회)")
    print(f"  최근당첨:  {latest['번호']}  보너스:{latest['보너스']}")
    print(f"  {next_rnd}회차 추천: {'✓ 생성됨' if has_rec else '✗ 미생성'}")
    print(f"\n  [TOP5 초과대기]")
    for n,sc in ranked[:5]:
        avg_sk = avg_skip.get(n, total/6)
        ratio  = waiting[n]/avg_sk
        print(f"    {n:2d}번  {waiting[n]:3d}회 대기  {ratio:.1f}x")
    print(f"\n  [매주 사용법]")
    print(f"    python lotto.py fetch          ← 당첨번호 자동수집 + 추천 생성")
    print(f"    python lotto.py history        ← 추천 이력·성과 확인")
    print(f"    python lotto.py analyze        ← 데이터 분석")
    print(f"    python lotto.py status         ← 현황 요약")
    rpts = sorted(os.listdir(REPORTS))
    if rpts:
        print(f"\n  [최근 리포트]")
        for r in rpts[-3:]: print(f"    reports/{r}")

# ══════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════
COMMANDS = {
    'fetch':     cmd_fetch,
    'update':    cmd_update,
    'recommend': cmd_recommend,
    'analyze':   cmd_analyze,
    'history':   cmd_history,
    'status':    cmd_status,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
