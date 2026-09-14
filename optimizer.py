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
    분리 구조를 깨지 않는 이동(게임 간 번호 교환 / 미사용 번호와의 교체)만 사용해
    14개 필터 통과 게임 수를 최대화한다. 확률은 탐색 내내 불변이다.
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
