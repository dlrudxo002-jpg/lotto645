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
  python lotto.py regrade           — 저장된 채점결과를 같은 회차 키로 재채점
                                      (채점키 off-by-one 수정 배포 후 1회 실행)
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
# 수집 엔드포인트는 accumulate.py가 단독 관리한다 (accumulate.API).
# 구 주소 'common.do?method=getLottoNumber'는 사이트 개편으로 302 — 2026-09-13 제거.

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

# 번호 점수 가중치 (2026-09-13 재검증)
# 구 설정: 스킵비율 5.0 / Z점수 1.5 / 페어친화도 1.0 — 스킵비율이 "핵심"으로 문서화돼 있었으나
# backtest_current.py 워크포워드 검증(1180회, look-ahead 없음)에서 예측력이 확인되지 않았다.
#   워크포워드 51~1241회(1191회차), 풀 15개·페어항 포함 = 프로덕션 동일 조건.
#   구 가중치 실측    : >=3 29.72% / >=4 8.14% / >=5 1.09% / 6/6 0회
#   신 가중치 실측    : >=3 30.56% / >=4 8.98% / >=5 1.09% / 6/6 1회
#   초기하분포 기대치 : >=3 31.14% / >=4 8.46% / >=5 1.17% / 6/6 0.73회
# 기대치를 하회하므로 5.0 가중은 근거가 없다. 세 항을 동등 가중으로 평탄화하고,
# 점수는 "예측 지표"가 아니라 풀 구성 시 순위를 정하는 타이브레이크로만 사용한다.
W_SKIP = 1.0   # 스킵비율 (예측력 없음 — 타이브레이크)
W_Z    = 1.0   # 장기 저빈도 Z점수 역방향
W_PAIR = 1.0   # 페어 친화도

def compute_scores(data):
    """스킵비율 + Z점수 + 페어친화도 (동등 가중 타이브레이크 — 예측 지표 아님)"""
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

    # 페어 친화도는 독립 정보가 아니다. 이중루프를 전개하면 닫힌형이 나온다:
    #   sum_{m!=n} pf[(n,m)] = 5*freq[n]  (n이 든 회차마다 n을 포함한 페어가 5개)
    #   ep = total*15/990 = total/66
    #   => (5*freq[n]/44)/(total/66) - 1) * 2 = 15*freq[n]/total - 2
    # 즉 **빈도의 선형 재표현**이며 Z역 항(-(freq-avg)/std)과 정확히 반대 방향이다
    # (Spearman -0.996, 페어 항이 Z역 항을 12.6% 상쇄한다 — CLAUDE.md [D] 참조).
    # 항목이 3개로 보이지만 독립 신호는 2개(스킵비율, 빈도)뿐이라는 사실을
    # 코드에서 바로 보이게 한다. W_Z만 조정하면 빈도 신호가 조용히 감쇄된다.
    # 값은 동일하다: 이중루프 대비 최대 오차 4.44e-16, 51~1241회 전수에서
    # 추천 풀 15개가 1건도 달라지지 않음(검증 2026-09-14).
    pair_affinity = {n: 15.0*freq[n]/total - 2.0 for n in range(1,46)}

    scores = {}
    for n in range(1,46):
        avg_sk = avg_skip.get(n, total/6)
        scores[n] = ((waiting[n]/avg_sk)*W_SKIP
                     + (-(freq[n]-avg_f)/std_f)*W_Z
                     + pair_affinity[n]*W_PAIR)
    return scores, freq, waiting, avg_skip

# 조합 점수의 페어/트리플 항 가중치.
# base(번호 점수 합)는 W_SKIP/W_Z/W_PAIR 스케일을 따라가지만 페어·트리플 항은
# 그렇지 않다. 따라서 번호 가중치를 건드리면 조합 랭킹이 소리 없이 뒤집힌다.
# 실측 sd(필터 통과 조합 3000개 기준): 구 가중 base 9.806 -> 신 가중 base 2.807 (÷3.494).
# 같은 비율로 페어·트리플을 줄여 원래 의도한 균형(pair/base 0.107, tri/base 0.131)을 복원한다.
# ※ 번호 가중치를 다시 바꾸면 이 두 상수도 반드시 재보정할 것.
W_COMBO_PAIR = 0.286
W_COMBO_TRI  = 0.086   # = 0.3(기존 트리플 계수) x 0.286

def score_combo(combo, scores, pf, tf, ep, et):
    from itertools import combinations as icombs
    n = sorted(combo)
    base   = sum(scores[x] for x in n)
    pair_b = sum(pf.get(p,0)/ep - 1.0 for p in icombs(n,2)) * W_COMBO_PAIR
    tri_b  = sum(tf.get(t,0)/et - 1.0 for t in icombs(n,3)) * W_COMBO_TRI
    return base + pair_b + tri_b

def select_candidates(data, scores, freq, waiting, avg_skip, target=15):
    """
    12~15개 유력번호 추리기
    기준 ①: 종합 점수 상위 (예측 지표 아님 — compute_scores 주석 참조)
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

def record_actual(hist, rnd, nums, bonus, date=None):
    """
    확정된 회차 당첨번호를 이력에 기록하고 **같은 회차 키**의 추천을 채점한다.

    키 규약: `hist[str(N)]`은 N회차를 위한 추천(`recommend`)과 N회차의 실제
    결과(`actual`)를 함께 담는다. `cmd_recommend`가 `str(target_rnd)`에 저장하고
    `cmd_history`·`app.py /api/history`·`/api/status`의 `has_recommend`도 모두
    같은 키끼리 대조한다. 따라서 채점도 같은 키여야 한다.

    과거에는 이 자리에서 `str(rnd-1)`의 추천을 채점해 넣었다. 그 결과
    N-1회차 추천이 N회차 당첨번호로 채점된 채 저장됐고, 표시 경로는 저장된
    `result`를 재계산보다 우선하므로 화면에도 그 값이 그대로 나왔다.
    쓰는 곳이 4벌(`cmd_fetch`·`cmd_update`·`save_draw`·`/api/manual_add`)로
    복사돼 있어 네 곳이 똑같이 틀렸다 — 그래서 함수 하나로 합친다.

    반환: 채점 결과 리스트. 해당 회차 추천이 없으면 None.
    """
    entry  = hist.setdefault(str(rnd), {})
    actual = {'번호': nums, '보너스': bonus}
    if date is not None:
        actual['날짜'] = date
    entry['actual'] = actual
    if 'recommend' not in entry:
        return None
    entry['result'] = grade_match(entry['recommend'], {'번호': nums, '보너스': bonus})
    return entry['result']

# ══════════════════════════════════════════════════════════════════
# 커맨드: fetch  — 자동 수집
# ══════════════════════════════════════════════════════════════════
def cmd_fetch(args):
    """
    최신 당첨번호 자동 수집.

    수집 자체는 accumulate.sync()에 위임한다 — CSV뿐 아니라 당첨금 이력과
    성적표(누적 채점)까지 한 번에 갱신되며, 수집 코드가 이 프로젝트에 하나만
    존재하게 하기 위해서다. 여기서는 추천 이력(history.json) 채점만 맡는다.

    구 엔드포인트(common.do)는 사이트 개편으로 사망했고(2026-09-13 확인),
    과거 이 자리의 광범위한 except가 그 실패를 "아직 추첨 전"과 구분 없이
    삼켜 수집이 조용히 멈춰 있었다. 지금은 네트워크·형식 오류를 명시적으로
    구분해 출력한다.
    """
    import accumulate

    data     = load_data()
    last_rnd = data[-1]['회차']
    print(f"\n  현재 DB 최신: {last_rnd}회차")
    print(f"  공식 API 조회 시작...\n")

    try:
        added = accumulate.sync(verbose=True)
    except Exception as e:
        # 수집 실패는 "새 회차 없음"과 전혀 다른 사건이다. 절대 조용히 넘기지 않는다.
        # 종료 코드도 0이 아니어야 한다 — 스케줄러가 초록불만 보고 멈춘 수집을
        # 몇 주씩 못 알아채는 것이 이번 사고의 원인이었다.
        print(f"\n  [수집 실패] {type(e).__name__}: {e}")
        if isinstance(e, (urllib.error.URLError, TimeoutError, OSError)):
            print("  → 네트워크·타임아웃 문제로 보인다. 잠시 후 다시 실행할 것.")
        elif isinstance(e, (ValueError, KeyError, TypeError)):
            print("  → 응답 형식이 바뀌었을 수 있다. accumulate.py의 API 상수와 필드명을 확인할 것.")
        sys.exit(1)

    if not added:
        print(f"\n  → 새로운 회차 없음. DB가 최신 상태입니다.")
        return

    # 히스토리에 실제 결과 기록 + **같은 회차** 추천 채점.
    # hist[str(N)]에는 N회차용 추천과 N회차 실제 결과가 함께 들어간다
    # (cmd_recommend가 str(target_rnd)에 쓴다). "직전 회차를 채점한다"고
    # 읽지 말 것 — 이 버그가 4벌로 복사돼 있었던 원인이 그 오독이었다.
    hist = load_history()
    for a in added:
        rnd, nums, bonus, date = a['회차'], a['번호'], a['보너스'], a['날짜']
        record_actual(hist, rnd, nums, bonus, date)
        print(f"  ✓ {rnd}회차 ({date}): {nums}  보너스:{bonus}")
    save_history(hist)

    print(f"\n  → {len(added)}회차 추가 완료. 다음 회차 추천 생성 중...")
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

    print(f"\n  [유력번호 풀 {len(pool)}개] — 구간·홀짝 균형 + 종합점수 순 선별")
    print(f"  {pool}")

    print(f"\n  {'번호':>4}  {'skip비율':>8}  {'대기':>6}  {'전체출현':>8}  {'선정이유'}")
    print(f"  {'─'*55}")
    # 표시용 Z는 루프 밖에서 한 번만 구한다. 과거에는 이 두 값을 번호마다
    # 다시 계산해 전체 데이터를 45번 훑었고, std_f의 `or 1` 안전판도 빠져 있어
    # 모든 번호 빈도가 같은 극소 데이터에서 ZeroDivisionError가 났다
    # (compute_scores:143과 같은 식이어야 한다).
    avg_f = len([x for d in data for x in d['번호']]) / 45
    std_f = (sum((c-avg_f)**2 for c in freq.values())/45)**0.5 or 1
    for n in pool:
        avg_sk = avg_skip.get(n, total/6)
        ratio  = waiting[n]/avg_sk
        z      = (freq[n]-avg_f) / std_f
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
    results = record_actual(hist, rnd, nums, bonus,
                            datetime.now().strftime('%Y-%m-%d'))
    if results:
        grades = [r['grade'] for r in results]
        best   = next((g for g in ['1등','2등','3등','4등','5등'] if g in grades), '낙첨')
        print(f"\n  [{rnd}회차 추천 채점결과]  최고등수: {best}")
        for i,r in enumerate(results,1):
            mark = " ★" if r['grade'] != '낙첨' else ""
            print(f"    게임{i:02d}: {r['game']}  {r['match']}개일치  {r['grade']}{mark}")
    save_history(hist)

    print(f"\n  → {rnd+1}회차 추천 생성 중...")
    cmd_recommend([], data=data, save=True)

# ══════════════════════════════════════════════════════════════════
# 커맨드: history
# ══════════════════════════════════════════════════════════════════
def cmd_regrade(args):
    """
    history.json의 저장된 `result`를 **같은 회차 키**로 전부 다시 채점한다.

    구 코드는 N회차 당첨번호로 str(N-1)의 추천을 채점해 넣었다. 표시 경로가
    `entry.get('result') or grade_match(...)`이라 저장값이 재계산보다 우선하므로,
    그 잘못된 등수는 화면에서 구분되지 않은 채 영구히 남는다. 이 저장소의
    history.json은 비어 있지만 배포 인스턴스(PythonAnywhere/Render)는 git 밖에서
    누적되므로, 배포 후 이 명령을 한 번 실행해 과거 기록을 바로잡는다.

    actual이 없는 회차는 CSV에서 보충하고, 그래도 없으면 result를 제거한다
    (제거해도 표시 경로가 자동 재계산하므로 정보 손실은 없다).
    """
    hist = load_history()
    if not hist:
        print("  이력 없음. 할 일이 없습니다.")
        return
    act_map = {d['회차']: d for d in load_data()}

    fixed = cleared = same = 0
    for key in sorted(hist.keys(), key=int):
        entry = hist[key]
        recs  = entry.get('recommend')
        if not recs:
            continue
        act = entry.get('actual')
        if not act and int(key) in act_map:
            d   = act_map[int(key)]
            act = {'번호': d['번호'], '보너스': d['보너스']}
        if not act:
            if entry.pop('result', None) is not None:
                cleared += 1
            continue
        new = grade_match(recs, act)
        old = entry.get('result')
        entry['result'] = new
        if old is None:
            same += 1
        elif [r['grade'] for r in old] != [r['grade'] for r in new]:
            fixed += 1
            print(f"  · {key}회차 재채점: "
                  f"{[r['grade'] for r in old]} → {[r['grade'] for r in new]}")
        else:
            same += 1

    save_history(hist)
    print(f"\n  재채점 완료 — 변경 {fixed}건 / 동일 {same}건 / 결과제거 {cleared}건")


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
    # 점수순(ranked)이 아니라 실제 대기비율순으로 정렬해야 제목과 내용이 일치한다.
    # 가중치 평탄화 이후 점수-대기비율 상관이 0.843 -> 0.458로 떨어져,
    # 점수순 출력 시 "대기 0회"인 번호가 초과대기 TOP5에 올라오는 오류가 있었다.
    print(f"\n  [TOP5 초과대기]")
    by_ratio = sorted(range(1,46),
                      key=lambda n: -(waiting[n]/avg_skip.get(n, total/6)))
    for n in by_ratio[:5]:
        ratio = waiting[n]/avg_skip.get(n, total/6)
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
    'regrade':   cmd_regrade,
    'status':    cmd_status,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
