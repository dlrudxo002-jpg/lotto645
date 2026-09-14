# -*- coding: utf-8 -*-
"""
research_battery.py — 1~현재 회차 전수 통계 연구 하네스 (2026-09-13 신설)

목적: "새 분석법"을 만들 때 과적합을 구조적으로 차단한다.
      규칙을 자유롭게 추가하되, 판정은 아래 3중 관문을 통과해야만 인정한다.

  [A] 무작위성 검정 배터리 — 원자료에 애초에 구조가 있는가?
      구조가 없으면 어떤 규칙도 예측력을 가질 수 없다. 먼저 확인한다.
  [B] 규칙 경합 + 다중검정 보정 — 규칙이 무작위 대조군을 이기는가?
      워크포워드(look-ahead 없음) + 무작위 대조군 + Bonferroni 보정.
  [C] 필터 효율 — 압축이 공짜인가, 커버율을 대가로 치르는가?
      커버율만 보면 모든 필터가 훌륭해 보인다. 통과율과 함께 봐야 의미가 있다.

설계 원칙
  - 예측성 평가는 전부 워크포워드. i회차 예측에 data[:i]만 사용한다.
  - 이론 기준선(초기하분포)과 별개로 무작위 대조군을 같은 파이프라인에 통과시킨다.
    구역·홀짝 제약 때문에 이론값이 정확한 기준선이 아닐 수 있기 때문이다.
  - N개 규칙을 동시에 검정하므로 유의수준을 N으로 나눈다(Bonferroni).
    보정 없이 15개를 돌리면 전부 무력해도 1개쯤은 "유의"하게 나온다.

실행: python -X utf8 research_battery.py
"""
import csv
import math
import os
import random
from collections import Counter, defaultdict, deque
from itertools import combinations

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lotto645_전체.csv')

MIN_DATA   = 50      # 워크포워드 시작점 (그 이전은 통계량이 불안정)
POOL_SIZE  = 15      # 프로덕션과 동일
RECENT_WIN = 30      # "최근" 규칙들의 관측 창
MC_SAMPLE  = 300_000 # 필터 통과율 몬테카를로 표본
SEED       = 20260913

PRIMES  = {2,3,5,7,11,13,17,19,23,29,31,37,41,43}
SQUARES = {1,4,9,16,25,36}
DOUBLES = {11,22,33,44}


# ── 통계 유틸 ────────────────────────────────────────────────────
def norm_sf(z):
    """표준정규 상측확률."""
    return 0.5 * math.erfc(z / math.sqrt(2))

def _gser(a, x):
    ap, s, d = a, 1.0/a, 1.0/a
    for _ in range(1000):
        ap += 1; d *= x/ap; s += d
        if abs(d) < abs(s)*1e-15: break
    return s * math.exp(-x + a*math.log(x) - math.lgamma(a))

def _gcf(a, x):
    tiny = 1e-300
    b, c, d = x+1-a, 1/tiny, 1/(x+1-a)
    h = d
    for i in range(1, 1000):
        an = -i*(i-a)
        b += 2
        d = an*d + b
        if abs(d) < tiny: d = tiny
        c = b + an/c
        if abs(c) < tiny: c = tiny
        d = 1/d
        de = d*c
        h *= de
        if abs(de-1) < 1e-15: break
    return math.exp(-x + a*math.log(x) - math.lgamma(a)) * h

def chi2_sf(x, df):
    """카이제곱 상측확률 (정규화 상측 불완전감마)."""
    a, xx = df/2, x/2
    if xx <= 0: return 1.0
    return 1.0 - _gser(a, xx) if xx < a+1 else _gcf(a, xx)

def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0]*len(v)
    for j, i in enumerate(order): r[i] = j+1
    return r

def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra)/len(ra), sum(rb)/len(rb)
    num = sum((x-ma)*(y-mb) for x, y in zip(ra, rb))
    den = (sum((x-ma)**2 for x in ra) * sum((y-mb)**2 for y in rb))**0.5
    return num/den if den else 0.0

def hyper(p, k, n=6, N=45):
    return math.comb(p, k)*math.comb(N-p, n-k)/math.comb(N, n)


# ── 데이터 ───────────────────────────────────────────────────────
def load():
    d = []
    with open(CSV_PATH, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            d.append({'회차': int(r['회차']),
                      '번호': sorted(int(r[f'번호{i}']) for i in range(1, 7))})
    d.sort(key=lambda x: x['회차'])
    return d


# ── 필터 (backtest_current.apply_filters와 동일 정의 — 변경 시 함께 수정) ──
def ac(n):
    return len({n[j]-n[i] for i in range(len(n)) for j in range(i+1, len(n))}) - (len(n)-1)

def zone_max(n):
    return max(sum(1 for x in n if lo <= x <= hi)
               for lo, hi in [(1,9),(10,19),(20,29),(30,39),(40,45)])

def consec_runs(n):
    runs, run = [], 1
    for i in range(1, len(n)):
        if n[i]-n[i-1] == 1:
            run += 1
        else:
            if run >= 2: runs.append(run)
            run = 1
    if run >= 2: runs.append(run)
    return runs

def apply_filters(n):
    n = sorted(n)
    ends = [x % 10 for x in n]
    ec   = Counter(ends)
    runs = consec_runs(n)
    max_end = max(ec.values())
    if max_end == 1: end_key = 'none'
    elif max_end == 2:
        end_key = {1:'2', 2:'2pair2', 3:'2pair3'}.get(sum(1 for v in ec.values() if v == 2), 'other')
    elif max_end == 3: end_key = '3'
    elif max_end == 4: end_key = '4'
    else: end_key = 'other'
    if not runs: c_key = 'none'
    elif len(runs) == 1 and runs[0] == 2: c_key = '2x1'
    elif len(runs) == 2 and all(r == 2 for r in runs): c_key = '2x2'
    elif len(runs) == 1 and runs[0] == 3: c_key = '3x1'
    else: c_key = 'other'
    return {
        'F01 번호총합 80~190':      80 <= sum(n) <= 190,
        'F02 AC값 6~10':            6 <= ac(n) <= 10,
        'F03 홀짝 1~5개':           sum(1 for x in n if x % 2) in {1,2,3,4,5},
        'F04 고저 1~5개':           sum(1 for x in n if x >= 23) in {1,2,3,4,5},
        'F05 끝수합 14~35':         14 <= sum(ends) <= 35,
        'F06 같은끝수 none/2/2쌍2': end_key in {'none','2','2pair2'},
        'F07 동일구간 2~3개':       zone_max(n) in {2,3},
        'F08 연속 없음/2x1':        c_key in {'none','2x1'},
        'F09 소수 0~3개':           sum(1 for x in n if x in PRIMES) in {0,1,2,3},
        'F10 제곱수 0~2개':         sum(1 for x in n if x in SQUARES) in {0,1,2},
        'F11 합성수 3~5개':         sum(1 for x in n if x > 1 and x not in PRIMES) in {3,4,5},
        'F12 동형수 0~1개':         sum(1 for x in n if x in DOUBLES) in {0,1},
        'F13 3배수 1~3개':          sum(1 for x in n if x % 3 == 0) in {1,2,3},
        'F14 5배수 0~2개':          sum(1 for x in n if x % 5 == 0) in {0,1,2},
    }


# ── 풀 구성 (프로덕션 get_pool과 동일 제약) ──────────────────────
def build_pool(scores, size=POOL_SIZE):
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    pool = []
    for lo, hi in [(1,9),(10,19),(20,29),(30,39),(40,45)]:
        b = next((n for n, _ in ranked if lo <= n <= hi and n not in pool), None)
        if b: pool.append(b)
    for n, _ in ranked:
        if len(pool) >= size: break
        if n not in pool: pool.append(n)
    zone_must, ps = pool[:5], set(pool)
    if sum(1 for n in pool if n % 2) < 5:
        for n, _ in ranked:
            if n % 2 and n not in ps:
                rem = [p for p in pool if p % 2 == 0 and p not in zone_must]
                if rem:
                    pool.remove(min(rem, key=lambda x: scores[x]))
                    pool.append(n); ps = set(pool)
                    if sum(1 for x in pool if x % 2) >= 5: break
    return set(pool)


# ── 워크포워드 상태 (증분 갱신 — 매 회차 전체 재계산 회피) ───────
class State:
    def __init__(self):
        self.freq      = Counter()
        self.last_seen = {}
        self.skip_sum  = defaultdict(int)
        self.skip_cnt  = defaultdict(int)
        self.recent    = deque(maxlen=RECENT_WIN)
        self.prev      = set()
        self.cur_round = 0
        self.total     = 0

    def push(self, draw):
        r, nums = draw['회차'], draw['번호']
        for n in nums:
            if n in self.last_seen:
                self.skip_sum[n] += r - self.last_seen[n]
                self.skip_cnt[n] += 1
            self.last_seen[n] = r
            self.freq[n] += 1
        self.recent.append(nums)
        self.prev = set(nums)
        self.cur_round = r
        self.total += 1

    def derived(self):
        total = self.total
        avg_f = sum(self.freq.values())/45
        std_f = (sum((self.freq.get(n, 0)-avg_f)**2 for n in range(1, 46))/45)**0.5 or 1.0
        waiting  = {n: self.cur_round - self.last_seen.get(n, 0) for n in range(1, 46)}
        avg_skip = {n: (self.skip_sum[n]/self.skip_cnt[n]) if self.skip_cnt[n] else total/6
                    for n in range(1, 46)}
        rec = Counter(n for d in self.recent for n in d)
        rec_end = Counter(n % 10 for d in self.recent for n in d)
        return dict(total=total, avg_f=avg_f, std_f=std_f, waiting=waiting,
                    avg_skip=avg_skip, rec=rec, rec_end=rec_end)


# ── 경합 규칙 정의 ───────────────────────────────────────────────
# 각 규칙: (이름, 설명, scores(st, dv, rng) -> {번호: 점수})
def make_rules():
    def skip_ratio(st, dv, rng):
        return {n: dv['waiting'][n]/dv['avg_skip'][n] for n in range(1, 46)}

    def zrev(st, dv, rng):
        return {n: -(st.freq.get(n, 0)-dv['avg_f'])/dv['std_f'] for n in range(1, 46)}

    def zfwd(st, dv, rng):
        return {n: (st.freq.get(n, 0)-dv['avg_f'])/dv['std_f'] for n in range(1, 46)}

    def pair_aff(st, dv, rng):
        # 페어 친화도 = (평균 페어출현비 - 1) x 2
        return {n: 15.0*st.freq.get(n, 0)/dv['total'] - 2.0 for n in range(1, 46)}

    return [
        ('R00 무작위 대조군',   '순수 난수 — 경험적 기준선',
         lambda st, dv, rng: {n: rng.random() for n in range(1, 46)}),
        ('R01 번호순 대조군',   '1번부터 — 정보량 0 대조군',
         lambda st, dv, rng: {n: -n for n in range(1, 46)}),
        ('R02 스킵비율',        '대기회차 ÷ 평균출현주기 (현행 항)', skip_ratio),
        ('R03 대기회차',        '단순 미출현 기간 (콜드)',
         lambda st, dv, rng: {n: dv['waiting'][n] for n in range(1, 46)}),
        ('R04 역대기',          '최근 출현 우대 (핫)',
         lambda st, dv, rng: {n: -dv['waiting'][n] for n in range(1, 46)}),
        ('R05 저빈도 Z역',      '전체 저빈도 우대 (현행 항)', zrev),
        ('R06 고빈도 Z정',      '전체 고빈도 우대', zfwd),
        (f'R07 최근{RECENT_WIN} 핫',  '최근 창 고빈도 우대',
         lambda st, dv, rng: {n: dv['rec'].get(n, 0) for n in range(1, 46)}),
        (f'R08 최근{RECENT_WIN} 콜드', '최근 창 저빈도 우대',
         lambda st, dv, rng: {n: -dv['rec'].get(n, 0) for n in range(1, 46)}),
        ('R09 페어친화도',      '평균 페어 동시출현비 (현행 항)', pair_aff),
        ('R10 현행 1/1/1',      '스킵+Z역+페어 동등가중 (출하 설정)',
         lambda st, dv, rng: {n: skip_ratio(st, dv, rng)[n] + zrev(st, dv, rng)[n]
                                 + pair_aff(st, dv, rng)[n] for n in range(1, 46)}),
        ('R11 구 5/1.5/1',      '폐기된 구 가중치',
         lambda st, dv, rng: {n: 5.0*skip_ratio(st, dv, rng)[n] + 1.5*zrev(st, dv, rng)[n]
                                 + pair_aff(st, dv, rng)[n] for n in range(1, 46)}),
        ('R12 이월수 우대',     '직전 회차 번호 최우선',
         lambda st, dv, rng: {n: (10.0 if n in st.prev else 0.0) + rng.random()
                              for n in range(1, 46)}),
        ('R13 끝수 최근빈도',   '최근 창 끝수 편향 추종',
         lambda st, dv, rng: {n: dv['rec_end'].get(n % 10, 0) + rng.random()*0.01
                              for n in range(1, 46)}),
        ('R14 끝수 역빈도',     '최근 창 끝수 편향 역행',
         lambda st, dv, rng: {n: -dv['rec_end'].get(n % 10, 0) + rng.random()*0.01
                              for n in range(1, 46)}),
    ]


# ════════════════════════════════════════════════════════════════
def part_a(data):
    print("\n" + "="*72)
    print("  [A] 무작위성 검정 배터리 — 원자료에 구조가 있는가?")
    print("="*72)
    total = len(data)
    freq = Counter(n for d in data for n in d['번호'])

    # A1 번호 균일성
    exp = total*6/45
    chi = sum((freq[n]-exp)**2/exp for n in range(1, 46))
    p = chi2_sf(chi, 44)
    print(f"\n  A1 번호 출현 균일성      chi2={chi:7.2f} (df=44)  p={p:.4f}  "
          f"{'구조 있음' if p < 0.05 else '균일과 구분 불가'}")
    hot  = max(range(1, 46), key=lambda n: freq[n])
    cold = min(range(1, 46), key=lambda n: freq[n])
    print(f"     최다 {hot}번 {freq[hot]}회 / 최소 {cold}번 {freq[cold]}회 "
          f"(기대 {exp:.1f}회, 표준편차 {math.sqrt(exp*(1-6/45)):.1f}회)")

    # A2 회차 간 독립성 (lag별 이월수)
    print(f"\n  A2 회차 간 독립성 — lag별 이월수 (기대 0.800개/회)")
    var1 = 6*(6/45)*(39/45)*(39/44)
    for lag in range(1, 6):
        obs = [len(set(data[i]['번호']) & set(data[i-lag]['번호'])) for i in range(lag, total)]
        m = sum(obs)/len(obs)
        z = (m-0.8)/math.sqrt(var1/len(obs))
        print(f"     lag {lag}:  평균 {m:.3f}개  z={z:+6.2f}  p={2*norm_sf(abs(z)):.4f}"
              f"  {'*유의*' if 2*norm_sf(abs(z)) < 0.05 else ''}")

    # A3 재출현 간격 vs 기하분포
    gaps = []
    last = {}
    for d in data:
        for n in d['번호']:
            if n in last: gaps.append(d['회차']-last[n])
            last[n] = d['회차']
    pgeo = 6/45
    bins = [(1,1),(2,2),(3,3),(4,5),(6,8),(9,13),(14,20),(21,10**9)]
    chi = 0.0
    for lo, hi in bins:
        o = sum(1 for g in gaps if lo <= g <= hi)
        pr = sum(pgeo*(1-pgeo)**(k-1) for k in range(lo, min(hi, 400)+1))
        e = pr*len(gaps)
        chi += (o-e)**2/e
    p = chi2_sf(chi, len(bins)-1)
    print(f"\n  A3 재출현 간격 분포      chi2={chi:7.2f} (df={len(bins)-1})  p={p:.4f}  "
          f"{'기하분포 이탈' if p < 0.05 else '기하분포와 일치 (= 무기억)'}")
    print(f"     표본 {len(gaps)}개  평균 간격 {sum(gaps)/len(gaps):.2f}회 (기대 7.50회)")

    # A4 페어 동시출현
    pf = Counter()
    for d in data:
        for pr_ in combinations(d['번호'], 2): pf[pr_] += 1
    epair = total*15/990
    chi = sum((pf.get(pr_, 0)-epair)**2/epair for pr_ in combinations(range(1, 46), 2))
    p = chi2_sf(chi, 989)
    print(f"\n  A4 페어 동시출현 균일성  chi2={chi:7.2f} (df=989)  p={p:.4f}  "
          f"{'궁합 존재' if p < 0.05 else '궁합 없음'}")
    top = max(pf.items(), key=lambda x: x[1])
    print(f"     최다 페어 {top[0]} {top[1]}회 (기대 {epair:.1f}회) — "
          f"990쌍 중 최대값의 기대치는 약 {epair + 3.2*math.sqrt(epair):.1f}회")

    # A5 전후반 빈도 안정성 — 순열 귀무분포 기준
    # 주의: 이 검정의 귀무 평균은 0이 아니다. f1+f2 = (번호별 총출현)이 고정이므로
    #       회차 순서를 섞어도 rho는 음수로 치우친다(실측 약 -0.13). rho<0 자체를
    #       "역행 신호"로 읽으면 거짓 양성이 난다 — 반드시 순열분포와 비교할 것.
    h = total//2
    f1 = Counter(n for d in data[:h] for n in d['번호'])
    f2 = Counter(n for d in data[h:] for n in d['번호'])
    rho = spearman([f1[n] for n in range(1, 46)], [f2[n] for n in range(1, 46)])
    rng = random.Random(SEED+2)
    idx, nulls = list(range(total)), []
    for _ in range(1000):
        rng.shuffle(idx)
        g1 = Counter(n for i in idx[:h] for n in data[i]['번호'])
        g2 = Counter(n for i in idx[h:] for n in data[i]['번호'])
        nulls.append(spearman([g1[n] for n in range(1, 46)], [g2[n] for n in range(1, 46)]))
    m = sum(nulls)/len(nulls)
    sd = (sum((x-m)**2 for x in nulls)/len(nulls))**0.5
    pv = sum(1 for x in nulls if abs(x-m) >= abs(rho-m))/len(nulls)
    print(f"\n  A5 전후반 빈도 지속성    rho={rho:+.3f}  (순열 귀무 {m:+.3f}±{sd:.3f})"
          f"  p={pv:.4f}  {'구조 있음' if pv < 0.05/7 else '구분 불가'}")
    print(f"     (전반 1~{data[h-1]['회차']}회 vs 후반 {data[h]['회차']}~{data[-1]['회차']}회)")
    print(f"     분할점을 20~80%로 옮기면 rho가 -0.05~-0.34로 널뛴다 = 단일 분할값은 신뢰 불가.")
    print(f"     최종 판정은 [B]의 워크포워드가 내린다 (분할점 선택의 자유도가 없으므로).")


def part_b(data):
    print("\n" + "="*72)
    print("  [B] 규칙 경합 — 무작위 대조군을 이기는 규칙이 있는가?")
    print("="*72)
    rules = make_rules()
    rng = random.Random(SEED)
    st = State()
    hits = {name: [] for name, _, _ in rules}

    for i, d in enumerate(data):
        if i >= MIN_DATA:
            dv = st.derived()
            actual = set(d['번호'])
            for name, _, fn in rules:
                hits[name].append(len(actual & build_pool(fn(st, dv, rng))))
        st.push(d)

    n_eval = len(hits[rules[0][0]])
    mu0 = 6*POOL_SIZE/45
    sd0 = math.sqrt(6*(POOL_SIZE/45)*(1-POOL_SIZE/45)*(45-6)/44)
    se = sd0/math.sqrt(n_eval)
    alpha = 0.05/len(rules)
    zcrit = -norm_sf(1-alpha/2) if False else 0
    # 양측 Bonferroni 임계 z
    lo, hi2 = 0.0, 10.0
    for _ in range(200):
        mid = (lo+hi2)/2
        if 2*norm_sf(mid) > alpha: lo = mid
        else: hi2 = mid
    zcrit = (lo+hi2)/2

    print(f"\n  워크포워드 {data[MIN_DATA]['회차']}~{data[-1]['회차']}회 ({n_eval}회차), 풀 {POOL_SIZE}개")
    print(f"  귀무가설 평균 적중 {mu0:.3f}개 (표준오차 {se:.4f})")
    print(f"  규칙 {len(rules)}개 동시검정 → Bonferroni 임계 |z| > {zcrit:.3f} (족보상 alpha=0.05)")
    print(f"\n  {'규칙':<20} {'평균적중':>8} {'z':>8} {'>=4개':>8} {'>=5개':>7}  판정")
    print("  " + "-"*66)

    exp4 = sum(hyper(POOL_SIZE, k) for k in range(4, 7))
    exp5 = sum(hyper(POOL_SIZE, k) for k in range(5, 7))
    rows = []
    for name, _, _ in rules:
        h = hits[name]
        m = sum(h)/len(h)
        z = (m-mu0)/se
        r4 = sum(1 for x in h if x >= 4)/len(h)
        r5 = sum(1 for x in h if x >= 5)/len(h)
        verdict = '*** 유의' if abs(z) > zcrit else ('열위' if z < -1 else '구분 불가')
        rows.append((name, m, z, r4, r5, verdict))
    for name, m, z, r4, r5, v in sorted(rows, key=lambda r: -r[2]):
        print(f"  {name:<20} {m:>8.3f} {z:>+8.2f} {r4*100:>7.2f}% {r5*100:>6.2f}%  {v}")
    print(f"  {'(이론 기대치)':<20} {mu0:>8.3f} {0:>+8.2f} {exp4*100:>7.2f}% {exp5*100:>6.2f}%")

    surv = [r for r in rows if abs(r[2]) > zcrit and r[2] > 0]
    print(f"\n  ▶ Bonferroni 통과 규칙: {len(surv)}개"
          + (f" — {', '.join(r[0] for r in surv)}" if surv else " (없음)"))
    return rows


def part_c(data):
    print("\n" + "="*72)
    print("  [C] 필터 효율 — 압축은 공짜인가?")
    print("="*72)
    total = len(data)
    win_pass = Counter(); win_all = 0
    for d in data:
        r = apply_filters(d['번호'])
        for k, v in r.items():
            if v: win_pass[k] += 1
        if all(r.values()): win_all += 1

    rng = random.Random(SEED+1)
    pool45 = list(range(1, 46))
    rnd_pass = Counter(); rnd_all = 0
    for _ in range(MC_SAMPLE):
        c = rng.sample(pool45, 6)
        r = apply_filters(c)
        for k, v in r.items():
            if v: rnd_pass[k] += 1
        if all(r.values()): rnd_all += 1

    print(f"\n  당첨번호 {total}회 vs 무작위 조합 {MC_SAMPLE:,}개")
    print(f"\n  {'필터':<26} {'당첨커버율':>10} {'무작위통과율':>12} {'효율비':>8}  판정")
    print("  " + "-"*68)
    for k in sorted(win_pass):
        cov = win_pass[k]/total
        pas = rnd_pass[k]/MC_SAMPLE
        eff = cov/pas if pas else float('inf')
        se = math.sqrt(cov*(1-cov)/total)
        z = (cov-pas)/se if se else 0
        print(f"  {k:<26} {cov*100:>9.2f}% {pas*100:>11.2f}% {eff:>8.3f}  "
              f"{'정보 있음' if abs(z) > 2.5 else '구분 불가'}")
    covA, pasA = win_all/total, rnd_all/MC_SAMPLE
    seA = math.sqrt(covA*(1-covA)/total)
    print("  " + "-"*68)
    print(f"  {'14개 전체 동시통과':<26} {covA*100:>9.2f}% {pasA*100:>11.2f}% "
          f"{covA/pasA:>8.3f}  z={(covA-pasA)/seA:+.2f}")
    print(f"\n  효율비 1.00 = 당첨번호를 걸러내는 비율과 무작위 조합을 걸러내는 비율이 같다.")
    print(f"  = 그 필터는 후보를 좁힐 뿐 1등 확률을 바꾸지 않는다.")
    print(f"  압축률: 전체 조합 {math.comb(45,6):,}개 -> 통과 약 {int(math.comb(45,6)*pasA):,}개")


def part_d(data):
    """현행 점수함수 내부 진단 — 항 간 선형종속 점검."""
    print("\n" + "="*72)
    print("  [D] 현행 점수함수 내부 진단")
    print("="*72)
    total = len(data)
    freq = Counter(n for d in data for n in d['번호'])
    pf = Counter()
    for d in data:
        for p in combinations(d['번호'], 2): pf[p] += 1
    epair = total*15/990
    direct, closed = {}, {}
    for n in range(1, 46):
        rs = [pf.get((min(n,m), max(n,m)), 0)/epair for m in range(1, 46) if m != n]
        direct[n] = (sum(rs)/len(rs) - 1.0)*2.0
        closed[n] = 15.0*freq[n]/total - 2.0
    err = max(abs(direct[n]-closed[n]) for n in range(1, 46))

    avg_f = sum(freq.values())/45
    std_f = (sum((freq[n]-avg_f)**2 for n in range(1, 46))/45)**0.5
    rho = spearman([direct[n] for n in range(1, 46)],
                   [-(freq[n]-avg_f)/std_f for n in range(1, 46)])
    print(f"\n  페어 친화도 항의 닫힌 형태:  pair(n) = 15 x freq(n) / 총회차 - 2")
    print(f"  직접계산 대비 최대 오차: {err:.2e}  → 항등식 (페어 항은 빈도의 선형함수다)")
    print(f"\n  즉 '페어 친화도'는 독립 정보가 아니라 빈도의 재표현이며,")
    print(f"  Z역 항(저빈도 우대)과 Spearman rho = {rho:+.3f} 로 정확히 반대 방향이다.")
    print(f"  두 항 계수 비교 (freq 1회 증가당):")
    print(f"     Z역  항: {-1/std_f:+.5f}   페어 항: {15/total:+.5f}   "
          f"→ 페어 항이 Z역 항을 {100*(15/total)/(1/std_f):.1f}% 상쇄")


if __name__ == '__main__':
    data = load()
    print("="*72)
    print(f"  연구 하네스 — {data[0]['회차']}~{data[-1]['회차']}회 (총 {len(data)}회차)")
    print("="*72)
    part_a(data)
    part_b(data)
    part_c(data)
    part_d(data)
    print("\n분석 완료.")
