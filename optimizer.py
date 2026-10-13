# -*- coding: utf-8 -*-
"""
optimizer.py — 당첨 확률 최대화 엔진 (2026-09-13 신설)

━━ 무엇을 올릴 수 있고 무엇을 못 올리는가 ━━━━━━━━━━━━━━━━━━━━━━━━

  못 올림:  P(1등) = 1 / 8,145,060  — 게임 1장당. 독립시행이므로 불변.
            번호 선택 방식과 무관하다. 이 파일도 이 값을 바꾸지 않는다.

  올릴 수 있음:  P(N게임 중 최소 1게임 당첨)  — 같은 예산, 같은 장수.
            게임 1장당 확률은 고정이지만, N장을 '서로 어떻게 배치하느냐'는
            자유도다. 이 자유도가 P(최소 1개 당첨)을 실제로 바꾼다.

  왜 바뀌는가: 두 게임이 번호를 공유하면 '같이 맞고 같이 틀리는' 상관이 생긴다.
            상관이 커지면 당첨이 한 회차에 몰리고 나머지 회차는 전멸한다.
            기댓값은 그대로지만 '최소 1개'라는 사건의 확률은 내려간다.
            따라서 최적해는 게임 간 번호 중복을 최소화하는 배치다.

            휠링은 정확히 그 반대를 한다 — 15개 풀에 몰아넣어 상관을 최대화한다.
            그래서 휠링은 P(최소 1개 당첨)을 낮춘다. 이 파일이 수치로 증명한다.

━━ 계산 방식 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  근사·시뮬레이션이 아니라 **정확한 조합론적 계산**을 쓴다.
  45개 번호를 '어느 게임들에 속하는가'(시그니처)로 원자 분할한 뒤,
  6개 공을 원자에 배분하는 경로를 DP로 전수 집계한다. 정수 연산이므로
  반올림 오차가 없다. 게임 수가 커져 상태공간이 폭발하면 몬테카를로로
  자동 전환하고, 그 경우 신뢰구간을 함께 출력한다.

실행: python -X utf8 optimizer.py
"""
import functools
import math
import random
from fractions import Fraction
from itertools import combinations

N_BALL   = 45
N_DRAW   = 6
WIN_MIN  = 3          # 5등 = 3개 일치. '당첨'의 하한.
TOTAL    = math.comb(N_BALL, N_DRAW)   # 8,145,060
SEED     = 20260913
STATE_CAP = 400_000   # 정확계산 상태수 상한. 초과 시 몬테카를로로 전환.


# ═══ 1. 정확 확률 엔진 ═══════════════════════════════════════════
def p_no_win_exact(tickets):
    """
    P(N개 게임 전부 3개 미만 일치) 를 정확히 계산한다. Fraction 반환.

    원리: 45개 번호를 시그니처(= 자신을 포함하는 게임들의 집합)로 분할하면
          같은 시그니처의 번호는 완전히 교환 가능하다. 원자를 하나씩 처리하며
          '공 사용 수 x 게임별 일치수' 상태를 누적한다. 어느 게임이든 일치수가
          3에 닿는 순간 그 경로는 '당첨'이므로 버린다. 남은 경로의 가중치 합이
          곧 미당첨 경우의 수다.
    """
    T = len(tickets)
    atoms = {}
    for n in range(1, N_BALL+1):
        s = tuple(i for i, t in enumerate(tickets) if n in t)
        atoms[s] = atoms.get(s, 0) + 1

    # 큰 원자(= 많은 게임에 걸친 번호)부터 처리하면 조기 가지치기가 잘 된다.
    order = sorted(atoms.items(), key=lambda kv: -len(kv[0]))

    states = {(0, (0,)*T): 1}
    for sig, m in order:
        nxt = {}
        for (b, cnt), w in states.items():
            kmax = min(N_DRAW - b, m)
            for k in range(kmax+1):
                if sig and k:
                    c = list(cnt)
                    dead = False
                    for i in sig:
                        c[i] += k
                        if c[i] >= WIN_MIN:
                            dead = True
                            break
                    if dead:
                        continue
                    nc = tuple(c)
                else:
                    nc = cnt
                key = (b+k, nc)
                nxt[key] = nxt.get(key, 0) + w*math.comb(m, k)
        states = nxt
        if len(states) > STATE_CAP:
            raise MemoryError('state explosion')
    ways = sum(w for (b, _), w in states.items() if b == N_DRAW)
    return Fraction(ways, TOTAL)


def _make_checker(tickets):
    """draw -> 당첨여부. 번호별 역색인 + 조기종료로 게임 수에 거의 비례하지 않는다."""
    by_num = [[] for _ in range(N_BALL+1)]
    for i, t in enumerate(tickets):
        for x in t:
            by_num[x].append(i)

    def wins(draw):
        cnt = {}
        for x in draw:
            for i in by_num[x]:
                c = cnt.get(i, 0) + 1
                if c >= WIN_MIN:
                    return True
                cnt[i] = c
        return False
    return wins


def p_win_mc_fixed(tickets, iters=1_500_000, seed=SEED):
    """고정된 게임 집합의 P(최소 1개 당첨). (추정값, 95% 반폭)."""
    rng = random.Random(seed)
    pool = list(range(1, N_BALL+1))
    wins = _make_checker(tickets)
    w = sum(1 for _ in range(iters) if wins(rng.sample(pool, N_DRAW)))
    p = w/iters
    return p, 1.96*math.sqrt(p*(1-p)/iters)


def p_win_mc_family(builder, n, sets=600, draws=2000, seed=SEED):
    """
    '이 방식으로 N게임을 사면' 의 기대 성능. 배치 자체가 무작위인 방식(자동구매,
    휠링)은 단일 집합이 아니라 집합들의 평균이 답이다.
    집합마다 별도 승률을 내고 집합 간 분산으로 신뢰구간을 잡는다(군집 보정).
    """
    rng = random.Random(seed)
    pool = list(range(1, N_BALL+1))
    rates = []
    for _ in range(sets):
        wins = _make_checker(builder(n, rng))
        w = sum(1 for _ in range(draws) if wins(rng.sample(pool, N_DRAW)))
        rates.append(w/draws)
    m = sum(rates)/len(rates)
    sd = (sum((x-m)**2 for x in rates)/(len(rates)-1))**0.5
    return m, 1.96*sd/math.sqrt(len(rates))


def p_win(tickets):
    """P(최소 1게임 당첨). (확률, 방식) 반환. 가능하면 정확계산."""
    try:
        return float(1 - p_no_win_exact(tickets)), '정확'
    except MemoryError:
        p, hw = p_win_mc_fixed(tickets)
        return p, f'MC±{hw*100:.3f}%p'


# ═══ 2. 단일 게임 기준값 (정확) ══════════════════════════════════
def single_ticket_rank_probs():
    """게임 1장의 등수별 정확 확률. 보너스 구분 포함."""
    c = math.comb
    p6 = Fraction(1, TOTAL)                                   # 1등
    p5b = Fraction(c(6,5)*1, TOTAL)                           # 2등 (5개+보너스)
    p5 = Fraction(c(6,5)*(N_BALL-N_DRAW-1), TOTAL)            # 3등 (5개)
    p4 = Fraction(c(6,4)*c(N_BALL-N_DRAW, 2), TOTAL)          # 4등
    p3 = Fraction(c(6,3)*c(N_BALL-N_DRAW, 3), TOTAL)          # 5등
    return {'1등': p6, '2등': p5b, '3등': p5, '4등': p4, '5등': p3}


P_SINGLE_WIN = sum(single_ticket_rank_probs().values())   # 게임 1장 당첨 확률


# ═══ 3. 배치 생성기 ══════════════════════════════════════════════
def tickets_disjoint(n, rng):
    """완전 분리 배치 — 게임 간 번호 중복 0. n <= 7 에서만 가능(6n <= 45)."""
    if 6*n > N_BALL:
        raise ValueError('분리 불가')
    pool = list(range(1, N_BALL+1))
    rng.shuffle(pool)
    return [tuple(sorted(pool[6*i:6*i+6])) for i in range(n)]


def tickets_random(n, rng):
    """무작위 배치 — 각 게임 독립 추출(자동 구매와 동일)."""
    return [tuple(sorted(rng.sample(range(1, N_BALL+1), N_DRAW))) for _ in range(n)]


def tickets_balanced(n, rng):
    """
    균형 배치 — 6n > 45 로 중복이 불가피할 때 사용.
    45개 번호를 라운드로빈으로 돌려 각 번호의 사용 횟수를 최대한 고르게 만들고,
    한 게임 안에 같은 번호가 두 번 들어가지 않게 한다.
    """
    seq = []
    while len(seq) < 6*n:
        blk = list(range(1, N_BALL+1))
        rng.shuffle(blk)
        seq += blk
    tickets, i = [], 0
    for _ in range(n):
        t = set()
        while len(t) < N_DRAW:
            if seq[i] not in t:
                t.add(seq[i])
            i += 1
            if i >= len(seq):                      # 보충
                blk = list(range(1, N_BALL+1)); rng.shuffle(blk); seq += blk
        tickets.append(tuple(sorted(t)))
    return tickets


def tickets_wheel(n, pool_size, rng):
    """휠링 배치 — pool_size개 풀 안에서만 n게임 생성 (현행 시스템 방식)."""
    pool = rng.sample(range(1, N_BALL+1), pool_size)
    allc = list(combinations(sorted(pool), N_DRAW))
    rng.shuffle(allc)
    return [tuple(c) for c in allc[:n]]


# ═══ 4. 국소 탐색 최적화 ═════════════════════════════════════════
def optimize(n, iters=600, seed=SEED, verbose=False):
    """
    P(최소 1개 당첨)을 목적함수로 하는 국소 탐색.
    한 게임의 번호 1개를 다른 번호로 바꿔보고 개선되면 채택한다.
    목적함수는 정확계산(가능하면) — 시뮬레이션 잡음에 최적화가 끌려가지 않는다.
    """
    rng = random.Random(seed)
    cur = tickets_disjoint(n, rng) if 6*n <= N_BALL else tickets_balanced(n, rng)
    best, bp = cur, p_win(cur)[0]
    for it in range(iters):
        i = rng.randrange(n)
        t = list(best[i])
        j = rng.randrange(N_DRAW)
        new = rng.randint(1, N_BALL)
        if new in t:
            continue
        t[j] = new
        cand = list(best)
        cand[i] = tuple(sorted(t))
        try:
            p = p_win(cand)[0]
        except Exception:
            continue
        if p > bp:
            best, bp = cand, p
            if verbose:
                print(f'    it{it:4d} 개선 -> {bp*100:.4f}%')
    return best, bp


# ═══ 4-2. 실제 구매 번호 생성 ════════════════════════════════════
# 핵심 성질: 게임들이 서로 번호를 공유하지 않으면(분리 배치), P(최소 1개 당첨)은
#   '어떤 번호를 골랐는가'와 무관하고 오직 '게임 수'에만 의존한다.
#   45개를 n개 그룹(각 6개) + 나머지로 쪼개는 방식은 P에 전혀 영향을 주지 않는다.
#   -> 확률을 1비트도 손해보지 않으면서 14개 필터 준수를 공짜로 얻을 수 있다.
#   이 성질이 없으면 '필터냐 확률이냐'의 트레이드오프가 생긴다. 실제로는 없다.

def recommend(n, seed=SEED, iters=40_000):
    """
    예산 n게임에 대한 구매 번호를 생성한다.
      - n <= 7 : 완전 분리(번호 중복 0) — 확률 최적이 증명된 구성
      - n >  7 : 균형 배치(번호 사용 횟수 평준화) — 중복 불가피 구간의 최선
    게임 간 번호 교환 / 미사용 번호와의 교체로 14개 필터 통과 게임 수를 최대화한다.
    n <= 7 이면 분리가 유지되므로 확률은 탐색 내내 불변이다. n > 7 의 게임 간 교환은
    번호가 다른 게임으로 옮겨가 공유 구조가 바뀌므로 P가 조금 움직일 수 있다.
    (실제 추천은 4-3절 spread_tickets 를 쓴다 — 구조를 고정한 채 번호 이름만 바꾼다.)
    """
    from research_battery import apply_filters
    rng = random.Random(seed)

    if 6*n <= N_BALL:
        pool = list(range(1, N_BALL+1))
        rng.shuffle(pool)
        tickets = [pool[6*i:6*i+6] for i in range(n)]
        unused = pool[6*n:]
    else:
        tickets = [list(t) for t in tickets_balanced(n, rng)]
        unused = []

    def score(ts):
        return sum(1 for t in ts if all(apply_filters(t).values()))

    cur = score(tickets)
    for _ in range(iters):
        if cur == n:
            break
        if unused and rng.random() < 0.5:
            i, a = rng.randrange(n), rng.randrange(N_DRAW)
            b = rng.randrange(len(unused))
            tickets[i][a], unused[b] = unused[b], tickets[i][a]
            if score(tickets) >= cur:
                cur = score(tickets)
            else:
                tickets[i][a], unused[b] = unused[b], tickets[i][a]
        else:
            if n < 2:
                continue
            i, j = rng.sample(range(n), 2)
            a, b = rng.randrange(N_DRAW), rng.randrange(N_DRAW)
            if tickets[i][a] in tickets[j] or tickets[j][b] in tickets[i]:
                continue
            tickets[i][a], tickets[j][b] = tickets[j][b], tickets[i][a]
            if score(tickets) >= cur:
                cur = score(tickets)
            else:
                tickets[i][a], tickets[j][b] = tickets[j][b], tickets[i][a]

    out = [tuple(sorted(t)) for t in tickets]
    dup = 6*n - len({x for t in out for x in t})
    return out, cur, dup


# ═══ 4-3. 적중 우선 배치 (2026-10-13) ═══════════════════════════
# 추천기(lotto.py · app.py)가 쓰는 배치. 목적은 P(n게임 중 최소 1게임 5등 이상).
#
#   1) 구조: 45개 번호 자리(슬롯)에 사용 횟수를 고르게 나누고 게임 간 공유를 평탄화한다.
#      P는 구조만으로 정해진다 — 어느 자리에 어떤 번호 이름을 붙이든 같다.
#   2) 이름 붙이기: 사용 횟수가 많은 자리에 풀(점수 상위) 번호를 붙인다.
#      번호 이름 맞바꾸기는 P를 바꾸지 않으므로, 필터 통과는 이 맞바꾸기로 찾는다.
#   3) 패치: 맞바꾸기로 못 푼 게임만 번호 1~3개를 갈아 끼운다(다른 게임과의 공유가
#      가장 적은 후보). 구조가 그 게임만큼 달라지므로 P가 조금 내려갈 수 있다.
#
# 1246회 풀 기준 측정 (scratchpad 실험, 10시드, MC 30만 공통난수):
#   30게임 패치 0건  P 59.08%  (고정 구조 58.99±0.18)
#   40게임 패치 0~2건 P 71.64%  (고정 구조 71.68±0.16)
#   50게임 패치 2~7건 P 80.63%  (고정 구조 81.10±0.14)
# 같은 게임 수의 자동구매(독립) 30/40/50게임 = 51.50 / 61.90 / 70.07%.
# 웹(app.py)은 요청 시간 때문에 필터 평가를 60,000회로 끊는다(max_evals). 같은 풀, seed 1246 1회:
#   40게임 상한 없음 71.80%(패치 0) / 6만 71.64%(패치 1)
#   50게임 상한 없음 80.68%(패치 2) / 6만 80.16%(패치 8)   — 30게임 이하는 차이 없음.
# 기대 당첨 게임 수(= n x 2.3834%)와 1등 확률은 어떤 배치에서도 그대로다.

@functools.lru_cache(maxsize=64)
def spread_structure(n, seed=SEED, sweeps=6000):
    """
    n게임의 공유 구조. 번호 대신 슬롯 0..44 를 쓴다.
    반환: (게임별 슬롯 튜플들, 슬롯별 사용 횟수). 앞 슬롯일수록 많이 쓰인다.

      6n <= 45 : 완전 분리. 슬롯 0..6n-1 을 한 번씩, 나머지는 미사용(0회).
      6n >  45 : 사용 횟수를 floor/ceil(6n/45)로 고르게 나누고, 두 게임이
                 공유하는 슬롯 수의 제곱합을 최소화한다(공유를 0/1로 평탄화).
    결정적이다(같은 n, seed면 같은 구조). 캐시하므로 호출자는 결과를 고치지 말 것.
    """
    if N_DRAW * n <= N_BALL:
        uses = [1] * (N_DRAW * n) + [0] * (N_BALL - N_DRAW * n)
        return tuple(tuple(range(N_DRAW * i, N_DRAW * i + N_DRAW)) for i in range(n)), tuple(uses)
    rng = random.Random(seed * 1000 + n)
    q, r = divmod(N_DRAW * n, N_BALL)
    uses = [q + 1] * r + [q] * (N_BALL - r)
    T = [set() for _ in range(n)]
    sh = [[0] * n for _ in range(n)]
    for s in range(N_BALL):
        chosen = []
        for _ in range(uses[s]):
            i = min((i for i in range(n) if len(T[i]) < N_DRAW and i not in chosen),
                    key=lambda i: (sum(sh[i][j] for j in chosen), len(T[i]), rng.random()))
            chosen.append(i)
        for i in chosen:
            T[i].add(s)
        for a, b in combinations(chosen, 2):
            sh[a][b] += 1
            sh[b][a] += 1
    # 국소 개선: 두 게임이 슬롯 하나씩을 맞바꾼다 (슬롯별 사용 횟수 보존)
    for _ in range(sweeps):
        i, j = rng.sample(range(n), 2)
        only_i, only_j = tuple(T[i] - T[j]), tuple(T[j] - T[i])
        if not only_i or not only_j:
            continue
        s, t = rng.choice(only_i), rng.choice(only_j)       # s: i->j, t: j->i
        changes, delta = [], 0
        for k in range(n):
            if k == i or k == j:
                continue
            di = (t in T[k]) - (s in T[k])
            if di:
                for a, d in ((i, di), (j, -di)):
                    v = sh[a][k]
                    delta += (v + d) ** 2 - v * v
                    changes.append((a, k, d))
        if delta <= 0:
            T[i].remove(s); T[i].add(t); T[j].remove(t); T[j].add(s)
            for a, k, d in changes:
                sh[a][k] += d
                sh[k][a] += d
    return tuple(tuple(sorted(x)) for x in T), tuple(uses)


class _Evals:
    """
    필터 평가기. 같은 조합은 한 번만 평가하고(캐시) 실제 평가 횟수를 센다.
    limit 은 탐색을 멈추는 기준일 뿐 호출 자체를 막지 않는다 — 호출자가 left()를 본다.
    시간이 아니라 횟수로 끊으므로 같은 입력이면 서버 속도와 무관하게 같은 결과가 나온다.
    """
    def __init__(self, ok, limit=None):
        self.ok, self.limit, self.used, self.cache = ok, limit, 0, {}

    def left(self, reserve=0):
        return self.limit is None or self.used < self.limit - reserve

    def __call__(self, key):            # key: 오름차순 튜플 (필터 함수들이 오름차순을 가정)
        v = self.cache.get(key)
        if v is None:
            self.used += 1
            v = self.cache[key] = bool(self.ok(list(key)))
        return v


def _patch(tickets, check, rng, pin=frozenset(), tries=1500):
    """
    필터 미통과(또는 중복) 게임만 번호 1~3개를 갈아 끼운다. 통과 후보 중 다른 게임들과의
    공유 제곱합이 가장 작은 것을 고른다(= 구조를 가장 덜 흔든다). 같으면 풀 번호(pin)를
    덜 빼는 쪽. 1개 교체는 전수, 2~3개는 표본 추출, 그래도 없으면 6개 전부 새로 뽑는다.
    check(_Evals)의 평가 상한에 닿으면 남은 게임은 고치지 못한 것으로 센다.
    반환: (게임 목록, 고친 게임 수, 끝내 못 고친 게임 수)
    """
    T = [set(t) for t in tickets]
    seen, todo = set(), []
    for g, t in enumerate(T):
        key = tuple(sorted(t))
        if key in seen or not check(key):
            todo.append(g)
        else:
            seen.add(key)
    fixed = failed = 0
    for g in todo:
        cur = T[g]
        cl = sorted(cur)
        others = [T[k] for k in range(len(T)) if k != g]
        outside = [x for x in range(1, N_BALL + 1) if x not in cur]
        best = None
        for r in (1, 2, 3, N_DRAW):
            if r == 1:
                cands = [(cur - {a}) | {b} for a in cl for b in outside]
            elif r < N_DRAW:
                cands = [(cur - set(rng.sample(cl, r))) | set(rng.sample(outside, r))
                         for _ in range(tries)]
            else:
                cands = [set(rng.sample(range(1, N_BALL + 1), N_DRAW)) for _ in range(tries)]
            for sc in cands:
                if not check.left():
                    break
                key = tuple(sorted(sc))
                if key in seen or not check(key):
                    continue
                rank = (sum(len(sc & o) ** 2 for o in others),
                        len((cur - sc) & pin) - len((sc - cur) & pin))
                if best is None or rank < best[0]:
                    best = (rank, key)
            if best or not check.left():
                break
        if best is None:                 # 필터가 너무 좁거나 평가 상한 — 원래 게임을 그대로 둔다
            failed += 1
            seen.add(tuple(cl))
            continue
        fixed += 1
        seen.add(best[1])
        T[g] = set(best[1])
    return [tuple(sorted(t)) for t in T], fixed, failed


LAMBDA_PIN = 0.5    # 제약 해제 뒤 풀 번호 강등 1건의 벌점 (필터 미통과 게임 1개 = 1)


def spread_tickets(n, priority, ok, pinned=0, seed=SEED, iters=20_000, max_evals=None):
    """
    적중 우선 배치 n게임. spread_structure 의 구조에 번호 이름을 붙인다.
      priority  : 1~45 전체를 우선순위 순으로 (앞일수록 많이 쓰이는 슬롯에 배정)
      ok        : 오름차순 게임 1장 -> 필터 통과 여부 (CLI는 lotto.passes, 웹은 사용자 필터)
      pinned    : priority 앞 몇 개를 처음 사용 횟수 등급에 묶어둘지 (= 풀 크기)
      max_evals : 필터 평가 횟수 상한(같은 조합은 1회로 센다). None이면 iters만 본다.
                  웹 요청 시간 상한용. 시간이 아니라 횟수로 끊으므로 결과는 서버 속도와
                  무관하게 결정적이다. 약 2/3을 이름 붙이기에, 나머지를 패치에 쓴다.
    반환: (게임 목록, 패치한 게임 수, 끝내 못 고친 게임 수). 마지막 값은 0이 정상이다.

    번호 이름 맞바꾸기는 구조를 바꾸지 않으므로 P는 탐색 내내 같다. 앞 절반은 풀 번호가
    사용 횟수가 낮은 자리로 밀려나지 않게 묶어 두고, 그래도 못 풀면 뒤 절반에서는 밀려남을
    벌점(LAMBDA_PIN)으로만 막는다. 끝나면 밀려난 풀 번호를 필터를 깨지 않는 한 되돌린다.
    """
    if sorted(priority) != list(range(1, N_BALL + 1)):
        raise ValueError('priority 는 1~45 를 정확히 한 번씩 담아야 한다')
    slots, uses = spread_structure(n)
    rng = random.Random(seed)
    of_slot = [[] for _ in range(N_BALL)]
    for g, ss in enumerate(slots):
        for s in ss:
            of_slot[s].append(g)
    pin = frozenset(priority[:pinned])
    hi = max(uses)

    # 초기 배정: 우선순위대로 놓고 같은 사용 횟수끼리만 섞는다
    lab = list(priority)
    for u in set(uses):
        pos = [s for s in range(N_BALL) if uses[s] == u]
        vals = [lab[s] for s in pos]
        rng.shuffle(vals)
        for s, x in zip(pos, vals):
            lab[s] = x

    check = _Evals(ok, max_evals)
    reserve = 0 if max_evals is None else max_evals // 3        # 패치 몫

    def good(g):
        return check(tuple(sorted(lab[s] for s in slots[g])))

    bad = {g for g in range(n) if not good(g)}
    dem = 0                 # 풀 번호가 낮은 등급 자리로 밀려난 수 (초기 배정은 0)
    best, best_key = list(lab), (len(bad), 0)
    strict = True
    for it in range(iters):
        if not bad or not check.left(reserve):
            break
        if it == iters // 2:
            strict = False          # 묶어 둔 채로는 못 풀었다 — 벌점으로 전환
        g = rng.choice(tuple(bad))
        s = rng.choice(slots[g])
        t = rng.randrange(N_BALL - 1)
        t += t >= s
        # 풀 번호와 비풀 번호가 사용 횟수가 다른 자리끼리 바뀌면 풀 번호의 사용 횟수가 바뀐다
        d_dem = 0
        if uses[s] != uses[t] and (lab[s] in pin) != (lab[t] in pin):
            if strict:
                continue
            d_dem = 1 if uses[s if lab[s] in pin else t] == hi else -1
        hit = set(of_slot[s]) | set(of_slot[t])
        lab[s], lab[t] = lab[t], lab[s]
        new_bad = {h for h in hit if not good(h)}
        delta = len(new_bad) - len(bad & hit) + LAMBDA_PIN * d_dem
        if delta <= 0 or rng.random() < math.exp(-4 * delta):
            bad = (bad - hit) | new_bad
            dem += d_dem
            if (len(bad), dem) < best_key:
                best, best_key = list(lab), (len(bad), dem)
        else:
            lab[s], lab[t] = lab[t], lab[s]
    lab, dem = best, best_key[1]

    # 마무리: 밀려난 풀 번호를 되돌린다 — 필터 미통과 게임이 늘지 않는 맞바꾸기만 받는다
    for s in [s for s in range(N_BALL) if uses[s] != hi and lab[s] in pin]:
        for t in range(N_BALL):
            if dem <= 0 or not check.left(reserve):
                break
            if uses[t] != hi or lab[t] in pin:
                continue
            hit = set(of_slot[s]) | set(of_slot[t])
            before = {h for h in hit if not good(h)}
            lab[s], lab[t] = lab[t], lab[s]
            if {h for h in hit if not good(h)} <= before:
                dem -= 1
                break
            lab[s], lab[t] = lab[t], lab[s]

    out = [tuple(sorted(lab[s] for s in ss)) for ss in slots]
    return _patch(out, check, random.Random(seed + 1), pin)


def pick_spread(cands, n):
    """
    풀 안에서만 고를 때의 분산 선택. 점수순으로 정렬된 후보에서 n개를 고르되, 이미 고른
    게임과 번호를 많이 공유하는 후보는 뒤로 미룬다. 공유 상한 2에서 시작해 3, 4, 5로 완화.
    (상위 n개를 그대로 자르면 서로 4~5개씩 겹치는 조합만 남아 같이 맞고 같이 틀린다.)
    후보가 n개 이하면 고를 것이 없으므로 그대로 돌려준다(공유 검사는 후보 수의 제곱이다).
    """
    if n >= len(cands):
        return list(cands)
    out, sets = [], []
    for cap in range(2, N_DRAW):
        for c in cands:
            if len(out) == n:
                return out
            sc = set(c)
            if sc in sets:
                continue
            if all(len(sc & g) <= cap for g in sets):
                out.append(c)
                sets.append(sc)
    return out


def p_win_fast(tickets, mc_iters=100_000, seed=SEED):
    """
    화면 표시용 P(최소 1게임 5등 이상). (확률, 95% 반폭) — 정확계산이면 반폭 0.
    게임 10개 이하는 정확계산, 그 이상은 몬테카를로(기본 10만 회, 반폭 약 0.3%p).
    """
    if not tickets:
        return 0.0, 0.0
    if len(tickets) <= 10:
        try:
            return float(1 - p_no_win_exact(tickets)), 0.0
        except MemoryError:
            pass
    return p_win_mc_fixed(tickets, iters=mc_iters, seed=seed)


# ═══ 5. 보장 분석 (확률이 아니라 확정) ═══════════════════════════
def schonheim_C(v, k, t):
    """커버링수 C(v,k,t)의 Schonheim 하한."""
    b = 1
    for i in range(t):
        b = -(-(v-i)*b // (k-i))     # ceil division
    return b


# ═══ 6. 리포트 ═══════════════════════════════════════════════════
def fmt(p):
    return f'{p*100:8.4f}%'


def main():
    rng = random.Random(SEED)
    print('='*74)
    print('  당첨 확률 최적화 엔진 — 무엇이 고정이고 무엇이 자유도인가')
    print('='*74)

    # ── 6-1. 고정된 값 ────────────────────────────────────────────
    print('\n[1] 게임 1장 기준 — 이 값들은 어떤 방법으로도 바뀌지 않는다\n')
    rp = single_ticket_rank_probs()
    for k, v in rp.items():
        print(f'    {k}  {float(v)*100:12.8f}%   = 1 / {1/float(v):>12,.0f}')
    print(f'    {"─"*52}')
    print(f'    당첨(3개↑)  {float(P_SINGLE_WIN)*100:9.5f}%   = 1 / {1/float(P_SINGLE_WIN):>12,.2f}')
    print('\n    이 표가 이 파일의 전제다. 아래 최적화는 이 값을 단 한 자리도 바꾸지 않는다.')
    print('    바꾸는 것은 "N장을 서로 어떻게 배치하는가"뿐이다.')

    # ── 6-2. 배치별 비교 ──────────────────────────────────────────
    print('\n[2] 같은 예산, 다른 배치 — P(최소 1게임 당첨)\n')
    print(f'    {"게임수":>5} {"금액":>9} {"무작위(자동)":>18} {"최적 배치":>12} '
          f'{"휠링(풀15)":>18} {"개선":>8} {"방식":>8}')
    print('    ' + '-'*88)

    rows = []
    for n in (1, 2, 3, 5, 7, 10, 20, 45):
        # 무작위(자동구매): 배치 자체가 확률변수이므로 집합들의 평균이 답이다.
        rnd, rnd_hw = p_win_mc_family(tickets_random, n, sets=400, draws=3000)

        # 최적: 분리 가능하면 완전분리, 아니면 균형 배치 (둘 다 결정적 집합)
        opt_t = tickets_disjoint(n, rng) if 6*n <= N_BALL else tickets_balanced(n, rng)
        opt, how = p_win(opt_t)

        # 휠링: 풀 15개 안에서만 구성
        wh, wh_hw = p_win_mc_family(lambda k, r: tickets_wheel(k, 15, r), n,
                                    sets=300, draws=3000)

        gain = (opt-rnd)/rnd*100 if rnd else 0
        # n=1은 배치 자유도가 없다 — 세 열이 같은 값이어야 하며 차이는 전부 MC 오차다.
        sig = '' if n > 1 and abs(opt-rnd) > rnd_hw else '  (오차내)'
        rows.append((n, rnd, opt, wh, gain, how, rnd_hw, wh_hw))
        print(f'    {n:>5} {n*1000:>8,}원  {rnd*100:7.4f}±{rnd_hw*100:.3f}% {fmt(opt):>12} '
              f' {wh*100:7.4f}±{wh_hw*100:.3f}% {gain:>+7.2f}%{sig or " " + how:>9}')

    print('\n    "최적 배치" = 게임 간 번호 중복 최소화. n<=7이면 완전 분리(중복 0)가 가능하다.')
    print('    무작위·휠링 열은 배치 자체가 확률변수라 집합 평균(군집 보정 MC)이며 ±는 95% 신뢰구간이다.')
    print('    "(오차내)" = 개선폭이 MC 오차보다 작아 유의하지 않다는 뜻이다.')
    print('    n=1은 배치할 것이 없으므로 세 열 모두 이론값 2.3834%가 참값이다.')

    # ── 6-3. 휠링이 왜 지는가 (해석적 상한) ───────────────────────
    print('\n[3] 휠링의 구조적 상한 — 게임을 아무리 늘려도 넘지 못하는 벽\n')
    for ps in (13, 15, 20, 25):
        cap = 1 - sum(math.comb(ps, k)*math.comb(N_BALL-ps, N_DRAW-k)
                      for k in range(0, WIN_MIN))/TOTAL
        print(f'    풀 {ps:>2}개로 구성하면  P(당첨) <= {cap*100:6.2f}%   '
              f'(풀 안에 당첨번호가 3개 이상 들어올 확률)')
    print('\n    풀에 당첨번호가 2개 이하로 들어오면 그 풀에서 만든 모든 조합이 전멸한다.')
    print('    게임 수를 5,005개(= 풀15 전조합)까지 늘려도 이 상한은 그대로다.')
    print(f'    반면 무작위 45게임은 {rows[-1][1]*100:.2f}%, 최적 45게임은 {rows[-1][2]*100:.2f}%다.')

    # ── 6-4. 국소 탐색으로 최적성 확인 ────────────────────────────
    print('\n[4] 최적성 검증 — 국소 탐색이 완전분리보다 나은 배치를 찾는가\n')
    for n in (3, 5, 7):
        base = p_win(tickets_disjoint(n, random.Random(SEED)))[0]
        _, opt = optimize(n, iters=400, seed=SEED)
        verdict = '완전분리가 최적' if opt <= base + 1e-12 else f'개선 발견 {opt*100:.4f}%'
        print(f'    {n}게임: 완전분리 {base*100:.4f}%  /  탐색 {opt*100:.4f}%  -> {verdict}')

    # ── 6-5. 기댓값은 불변 ────────────────────────────────────────
    print('\n[5] 기댓값 — 배치를 바꿔도 움직이지 않는다\n')
    print(f'    E[당첨 게임 수] = N x {float(P_SINGLE_WIN):.6f}  (기댓값의 선형성)')
    for n in (5, 10, 45):
        print(f'      {n:>3}게임: 배치 무관하게 항상 {n*float(P_SINGLE_WIN):.4f}게임')
    print('\n    최적화가 바꾸는 것은 기댓값이 아니라 분산이다.')
    print('    번호를 몰면 "한 번에 여러 개 당첨 / 대부분 전멸"이 되고,')
    print('    번호를 펼치면 "꾸준히 하나씩 당첨"이 된다. 후자가 P(최소 1개)를 높인다.')

    # ── 6-6. 보장 (확률이 아닌 확정) ──────────────────────────────
    print('\n[6] 확률이 아니라 100% 보장하려면\n')
    lb = schonheim_C(N_BALL, N_DRAW, WIN_MIN)
    print(f'    5등(3개 일치) 보장에 충분한 구성 = 커버링 디자인 C(45,6,3)')
    print(f'    필요 게임 수 하한(Schonheim): {lb:,}게임 = {lb*1000:,}원')
    print(f'    회수액: 5등 5,000원 x 최소 1장 = 5,000원.  구조적으로 적자다.')
    print(f'    [확인 필요] C(45,6,3)의 정확한 최소값은 미해결 문제이며 위는 하한이다.')

    # ── 6-7. 실제 구매 번호 ───────────────────────────────────────
    print('\n[7] 실제 구매 번호 — 확률 최적 배치 + 14개 필터 동시 만족\n')
    for n in (5, 7):
        ts, passed, dup = recommend(n)
        p, how = p_win(list(ts))
        print(f'    [{n}게임 / {n*1000:,}원]  P(최소 1게임 당첨) = {p*100:.4f}% ({how})'
              f'  번호중복 {dup}개  필터통과 {passed}/{n}게임')
        for i, t in enumerate(ts, 1):
            print(f'      {i}. ' + '  '.join(f'{x:2d}' for x in t))
        print()
    print('    분리 배치에서 P는 "어떤 번호를 골랐는가"와 무관하고 게임 수에만 의존한다.')
    print('    따라서 필터 준수는 확률을 1비트도 깎지 않는다 — 트레이드오프가 없다.')

    print('\n' + '='*74)
    print('  결론')
    print('='*74)
    n5 = [r for r in rows if r[0] == 5][0]
    n45 = rows[-1]
    print(f'''
  1. P(1등)은 1/8,145,060에서 움직이지 않았다. 위 [1]이 그 값이다.
  2. P(최소 1게임 당첨)은 실제로 올랐다.
       5게임:  무작위 {n5[1]*100:.4f}% -> 최적 {n5[2]*100:.4f}%  ({n5[4]:+.2f}%)
      45게임:  무작위 {n45[1]*100:.4f}% -> 최적 {n45[2]*100:.4f}%  ({n45[4]:+.2f}%)
     같은 돈으로 당첨될 확률이 오른 것이 맞다. 다만 올라간 폭이 이 정도다.
  3. 현행 휠링 방식은 이 지표에서 구조적으로 진다. [3]의 상한이 이유다.
  4. 기댓값은 어느 배치에서도 동일하다. 최적화는 분산 조절이지 수익 개선이 아니다.
''')


if __name__ == '__main__':
    main()
