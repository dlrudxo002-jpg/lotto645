# -*- coding: utf-8 -*-
"""
회차 누적 반영 파이프라인 - 새 당첨번호가 확인되면 통계·검증을 함께 갱신한다.

이 파일이 해결하는 문제:
  기존 `lotto.py fetch`는 구 엔드포인트(common.do)를 호출한다. 동행복권 사이트가
  개편되며 이 주소는 302로 죽었고(2026-09-13 확인), lotto.py:274의 광범위한
  except가 실패를 "아직 추첨 전"과 구분 없이 삼켜 누적이 조용히 멈춰 있었다.

누적되는 것은 세 가지다. 숫자만 쌓는 것은 누적이 아니다.
  (1) 당첨번호      -> lotto645_전체.csv
  (2) 회차별 당첨금 -> lotto645_당첨금.csv   (등수별 인원·금액·판매액, EV 분석용)
  (3) 성적표        -> scoreboard.json
      (3)이 핵심이다. 회차를 CSV에 넣기 "전"에, 그 회차 직전 데이터만으로 풀을
      생성해 실제 당첨번호와 대조하고 결과를 누적한다(look-ahead 없음).
      이렇게 해야 "우리 방식이 여전히 우연과 구분되지 않는가"를 매주 자동으로
      재검정할 수 있다. 빈도표만 갱신하면 틀린 방법도 영원히 살아남는다.

사용법:
    python -X utf8 accumulate.py              # 새 회차만 수집·채점·반영 (평상시)
    python -X utf8 accumulate.py --prizes     # 당첨금 이력 전체 백필
    python -X utf8 accumulate.py --rebuild    # 성적표를 1회차부터 전면 재계산
    python -X utf8 accumulate.py --sales      # 당첨금 CSV '총판매액' 열을 공식값으로 재기록
"""
import csv
import json
import math
import os
import sys
import time
import urllib.request
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH   = os.path.join(BASE, 'lotto645_전체.csv')
PRIZE_PATH = os.path.join(BASE, 'lotto645_당첨금.csv')
SCORE_PATH = os.path.join(BASE, 'scoreboard.json')

# 개편 후 엔드포인트. srchDir=center라 N회를 물으면 N-5~N+4회 10회차분이 역순으로
# 온다 (최신 회차 근처는 최신 10회, 1회 근처는 1~10회로 고정 — 2026-10-13 확인).
API = ('https://www.dhlottery.co.kr/lt645/selectPstLt645InfoNew.do'
       '?srchDir=center&srchLtEpsd={epsd}')
UA = 'Mozilla/5.0'
MIN_DATA = 50          # 성적표 집계 시작 회차 인덱스 (backtest_current와 동일)
POLITE_SLEEP = 0.35    # 연속 호출 간격 — 공식 사이트에 부담을 주지 않는다

CSV_HEADER = ['회차', '번호1', '번호2', '번호3', '번호4', '번호5', '번호6', '보너스']
PRIZE_HEADER = ['회차', '추첨일', '1등인원', '1등금액', '2등인원', '2등금액',
                '3등인원', '3등금액', '4등인원', '4등금액', '5등인원', '5등금액',
                '총판매액']


# ── 수집 ─────────────────────────────────────────────────────────
def fetch_window(epsd):
    """epsd 회차 앞뒤 10회차분(N-5~N+4)을 가져온다. 실패는 예외로 올린다 — 삼키지 않는다."""
    req = urllib.request.Request(API.format(epsd=epsd), headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode('utf-8')
    if not body.lstrip().startswith('{'):
        raise RuntimeError(f'JSON이 아닌 응답 (엔드포인트 변경 의심): {body[:120]!r}')
    payload = json.loads(body)
    out = []
    for it in (payload.get('data') or {}).get('list') or []:
        nums = [it.get(f'tm{i}WnNo') for i in range(1, 7)]
        if it.get('ltEpsd') is None or any(n in (None, 0) for n in nums):
            continue          # 미추첨/결번 행은 버린다
        out.append(it)
    return out


def fetch_rounds(start, end):
    """
    start~end 회차를 수집한다.

    창 위치를 회차 산술로 미리 정하지 않는다. "아직 못 받은 회차 중 가장 큰 m"을
    기준으로 m-4를 물어 m-9~m을 한 번에 받고, 그래도 m이 안 오면 m 자체를 묻는다.
    과거 구현은 end에서 10씩 내려가며 물었는데, center 창이 N-5~N+4라서 end가 최신
    회차가 아니면 범위 맨 아래가 조용히 빠졌다 (1228~1235 요청 -> 1228·1229 누락,
    2026-10-13 실측). 두 번 물어도 오지 않는 회차는 결번/미추첨으로 보고 넘긴다 —
    빠진 회차가 있는지는 호출부가 확인한다.

    각 m은 서로 다른 조회를 최대 2번(m-4, m)만 쓰므로 정상 응답이면 조회 수는
    2*(end-start+1)을 넘지 않고, 실제로는 10회차당 1~2번이다. 엔드포인트가 1회차씩만
    주거나 늘 최신 10회만 주는 식으로 망가지면 범위 전체를 한 회차씩 훑게 되므로
    (1~1245회 = 약 1,200번 호출) 상한을 넘으면 멈춘다.
    """
    start = max(1, start)
    cap = 2 * ((end - start) // 10 + 1) + 10
    got, asked, gone = {}, set(), set()
    while True:
        miss = [r for r in range(end, start - 1, -1) if r not in got and r not in gone]
        if not miss:
            break
        m = miss[0]
        epsd = next((e for e in (max(1, m - 4), m) if e not in asked), None)
        if epsd is None:
            gone.add(m)
            continue
        if len(asked) >= cap:
            raise RuntimeError(f'{start}~{end}회 수집에 조회 {cap}번을 넘겼다 '
                               f'(받은 회차 {len(got)}개) - 엔드포인트 응답 형식 변경 의심.')
        asked.add(epsd)
        for it in fetch_window(epsd):
            got[it['ltEpsd']] = it
        time.sleep(POLITE_SLEEP)
    return {k: v for k, v in got.items() if start <= k <= end}


def latest_round(have):
    """
    추첨이 끝난 최신 회차. 엔드포인트는 미추첨 회차를 조회하면 빈 리스트를 주고
    최신 회차를 알려주는 파라미터가 없다(2026-09-13 확인) — 위로 더듬어 찾는다.
    미수집분이 많을 수 있으니 10씩 건너뛰다가 1씩 좁힌다.

    종료 조건은 "빈 응답"이 아니라 "전진 실패"다. 미추첨 회차를 물었을 때
    빈 리스트 대신 과거분만 잘라 주거나(partial) 최신 10회로 고정 응답하는(clamp)
    식으로 엔드포인트가 바뀌면, 빈 응답만 보고 도는 루프는 영원히 멈추지 않는다.
    최댓값이 갱신되지 않으면 더 볼 것이 없다고 판단한다. MAX_PROBE는 최후 안전판.
    """
    MAX_PROBE = 40
    latest = have
    e, step, probes = have + 10, 10, 0
    while step >= 1 and probes < MAX_PROBE:
        probes += 1
        w = fetch_window(e)
        time.sleep(POLITE_SLEEP)
        top = max((it['ltEpsd'] for it in w), default=None)
        if top is not None and top > latest:
            latest = top
            e = latest + step
        elif step == 10:
            step, e = 1, latest + 1
        else:
            break
    if probes >= MAX_PROBE:
        print(f'  [경고] 최신 회차 탐색이 {MAX_PROBE}회 조회에서 중단됐다 '
              f'(마지막 확인 {latest}회) — 엔드포인트 응답 형식 변경 의심.')
    return latest


# ── 저장 ─────────────────────────────────────────────────────────
def load_csv():
    with open(CSV_PATH, encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f)]
    data = [{'회차': int(r['회차']),
             '번호': sorted(int(r[f'번호{i}']) for i in range(1, 7)),
             '보너스': int(r['보너스'])} for r in rows]
    data.sort(key=lambda x: x['회차'])
    return data


def ensure_newline(path):
    """
    마지막 줄에 개행이 없으면 덧붙이기가 직전 행과 붙어버려 회차 하나가 사라진다.
    이 CSV는 줄바꿈이 섞여 있어(과거분 LF, 최근분 CRLF) 눈으로는 잘 보이지 않는다.
    """
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, 'rb+') as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) not in (b'\n', b'\r'):
                f.write(b'\r\n')


def append_csv(items):
    """공식 응답에서 온 값만 쓴다. 어떤 경우에도 번호를 생성하지 않는다."""
    ensure_newline(CSV_PATH)
    with open(CSV_PATH, 'a', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        for it in items:
            nums = sorted(it[f'tm{i}WnNo'] for i in range(1, 7))
            w.writerow([it['ltEpsd']] + nums + [it['bnsWnNo']])


def append_row(rnd, nums, bonus):
    """공식 응답이 아닌 외부 확인값 1줄. 호출자가 출처를 책임진다."""
    ensure_newline(CSV_PATH)
    with open(CSV_PATH, 'a', encoding='utf-8', newline='') as f:
        csv.writer(f).writerow([rnd] + sorted(nums) + [bonus])


def record_manual(rnd, nums, bonus):
    """
    이미 확인된 당첨번호 1회차를 수동 반영한다 (웹앱 수동입력·관리 엔드포인트).

    CSV에만 쓰고 끝내면 성적표가 그 회차를 영영 건너뛴다 — sync()는 CSV 최신 회차를
    기준으로 "이미 가진 것"을 판단하기 때문이다. 그래서 수동 경로도 수집 경로와
    같은 순서를 지킨다: 채점(직전 데이터만) -> CSV -> 성적표.
    당첨금은 공식 API에서만 오므로 여기서 채우지 않는다. `--prizes`가 백필한다.

    반환: 채점 결과 dict / 채점 불가 시 None / 이미 있는 회차면 'duplicate'
    """
    nums = sorted(int(n) for n in nums)
    data = load_csv()
    if any(d['회차'] == rnd for d in data):
        return 'duplicate'

    board = load_board()
    heal_board(data, board, say=lambda *a: None)

    history = [d for d in data if d['회차'] < rnd]
    s = score_round(history, {'회차': rnd, '번호': nums}) if len(history) >= MIN_DATA else None

    append_row(rnd, nums, bonus)

    # 성적표는 first_round부터 연속 구간이어야 z값이 의미를 갖는다. 중간 결번을
    # 뒤늦게 채우는 경우는 순서가 깨지므로 기록하지 않고 --rebuild로 넘긴다.
    if s and board.get('scored') and rnd == (board.get('last_round') or 0) + 1:
        record(board, s)
        save_board(board)
    elif s:
        print(f'  [주의] {rnd}회는 성적표 연속 구간 밖이다 '
              f'(성적표 최신 {board.get("last_round")}회) — --rebuild 필요.')
    return s


def load_prize_rounds():
    if not os.path.exists(PRIZE_PATH):
        return set()
    with open(PRIZE_PATH, encoding='utf-8') as f:
        return {int(r['회차']) for r in csv.DictReader(f)}


# '총판매액'은 wholEpsdSumNtslAmt다. rlvtEpsdSumNtslAmt는 261회부터 판매액이 아니라
# 1~5등 당첨금 총액(약 rnk1~5 인원 x 금액 합 - 이월 회차는 어긋남, 판매액의 약 절반)이고, 1~260회만 두 필드가
# 같다 (2026-10-13 공식 API 전 구간 대조). 공식 당첨결과 페이지 735회
# "총판매금액 : 77,488,209,000원"(web.archive.org 보관본)이 whol과 일치, rlvt는 그 절반.
# 2026-10-13 이전에는 rlvt를 기록했다 — 기존 행은 --sales로 재기록한다.
SALES_FIELD = 'wholEpsdSumNtslAmt'


def prize_row(it):
    return ([it['ltEpsd'], it.get('ltRflYmd', '')] +
            [it.get(f'rnk{r}{k}') for r in range(1, 6) for k in ('WnNope', 'WnAmt')] +
            [it.get(SALES_FIELD)])


def append_prizes(items):
    new = not os.path.exists(PRIZE_PATH)
    ensure_newline(PRIZE_PATH)
    with open(PRIZE_PATH, 'a', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(PRIZE_HEADER)
        for it in sorted(items, key=lambda x: x['ltEpsd']):
            w.writerow(prize_row(it))


# ── 채점 (look-ahead 없음) ───────────────────────────────────────
def hyper_ge(pool, k, size=6, N=45):
    """풀 pool개 중 당첨번호가 k개 이상 포함될 초기하 확률."""
    return sum(math.comb(pool, j)*math.comb(N-pool, size-j)
               for j in range(k, size+1))/math.comb(N, size)


def score_round(history, actual):
    """history(해당 회차 직전까지)로 풀을 만들어 actual과 대조한다."""
    from backtest_current import get_pool, apply_filters, POOL_SIZE
    pool = get_pool(history)
    hits = len(set(actual['번호']) & pool)
    return {'회차': actual['회차'], 'pool_size': POOL_SIZE, 'hits': hits,
            'filters_pass': all(apply_filters(actual['번호']).values())}


def blank_board():
    return {'pool_size': None, 'scored': 0, 'hits': {str(k): 0 for k in range(7)},
            'filters_pass': 0, 'first_round': None, 'last_round': None, 'log': []}


def load_board():
    if os.path.exists(SCORE_PATH):
        with open(SCORE_PATH, encoding='utf-8') as f:
            return json.load(f)
    return blank_board()


def record(board, s):
    # 풀 크기가 바뀌면 기대치(초기하)가 달라져 과거 누적과 섞을 수 없다.
    # 조용히 덮어쓰면 z값이 통째로 틀린 채 계속 출력된다.
    if board.get('pool_size') not in (None, s['pool_size']):
        print(f"  [경고] 풀 크기 변경 {board['pool_size']} -> {s['pool_size']}. "
              f"기대치 기준이 달라졌으니 --rebuild로 전면 재계산할 것.")
    board['pool_size'] = s['pool_size']
    board['scored'] += 1
    board['hits'][str(s['hits'])] += 1
    board['filters_pass'] += 1 if s['filters_pass'] else 0
    if board['first_round'] is None:
        board['first_round'] = s['회차']
    board['last_round'] = s['회차']
    board['log'].append([s['회차'], s['hits'], int(s['filters_pass'])])
    board['log'] = board['log'][-200:]     # 최근 200회만 원본 보관


def save_board(board):
    with open(SCORE_PATH, 'w', encoding='utf-8') as f:
        json.dump(board, f, ensure_ascii=False, indent=1)


def heal_board(data, board, say=print):
    """
    CSV에는 들어갔는데 성적표에는 빠진 회차를 되채운다.

    왜 필요한가: 수집 도중 중단되면 CSV 기록과 성적표 기록의 시점이 어긋난다.
    sync()는 다음 실행에서 CSV 마지막 회차를 기준으로 "이미 가진 것"을 판단하므로,
    CSV에만 들어간 회차는 두 번 다시 채점 대상이 되지 않는다. 성적표는 우리 방식이
    우연과 구분되는지 판단하는 유일한 근거이므로, 조용한 결손은 수치 자체를 무효화한다.
    data[:i]만 넘기므로 되채움 과정에서도 look-ahead는 발생하지 않는다.
    """
    last = board.get('last_round')
    if not board.get('scored') or last is None:
        return 0                      # 아직 채점 이력 없음 — --rebuild 영역
    missing = [i for i, d in enumerate(data) if d['회차'] > last]
    if not missing:
        return 0
    say(f'  [복구] 성적표 누락 {len(missing)}회차 감지 '
        f'(성적표 {last}회 / CSV {data[-1]["회차"]}회) — 되채운다.')
    for i in missing:
        s = score_round(data[:i], data[i])
        record(board, s)
        say(f'    {data[i]["회차"]}회 풀적중 {s["hits"]}개  '
            f'필터 {"통과" if s["filters_pass"] else "탈락"}')
    save_board(board)
    return len(missing)


def report_board(board):
    n = board['scored']
    if not n:
        print('  채점 이력 없음. --rebuild로 전체 재계산할 수 있다.')
        return
    ps = board['pool_size']
    print(f"  채점 구간: {board['first_round']}~{board['last_round']}회  "
          f"({n}회차, 풀 {ps}개)\n")
    print(f"  {'구간':<10} {'실측':>10} {'비율':>8} {'기대(초기하)':>12} {'z':>8}  판정")
    print('  ' + '-'*60)
    for k in (3, 4, 5, 6):
        c = sum(v for kk, v in board['hits'].items() if int(kk) >= k)
        p, e = c/n, hyper_ge(ps, k)
        z = (p-e)/math.sqrt(e*(1-e)/n)
        # 15개 룰을 동시에 보던 연구 하네스와 달리 여기는 사전 등록된 4개 구간이다.
        judge = '유의' if abs(z) > 2.5 else '구분 불가'
        print(f'  {k}개 이상{"":<3} {c:>8}회 {p*100:>7.2f}% {e*100:>11.2f}% {z:>+8.2f}  {judge}')
    fp = board['filters_pass']
    print(f"\n  당첨번호의 14개 필터 통과: {fp}회 / {n}회 ({fp/n*100:.1f}%)  "
          f"[기준 36.8%, 이탈 시 필터 재조정 신호]")


# ── 실행 ─────────────────────────────────────────────────────────
def do_rebuild():
    data = load_csv()
    print(f'성적표 전면 재계산 — {data[MIN_DATA]["회차"]}~{data[-1]["회차"]}회 '
          f'({len(data)-MIN_DATA}회차). 수 분 소요.')
    board = blank_board()
    for i in range(MIN_DATA, len(data)):
        record(board, score_round(data[:i], data[i]))
        if (i-MIN_DATA+1) % 200 == 0:
            print(f'  ... {i-MIN_DATA+1}회차 완료')
    save_board(board)
    print(f'저장: {SCORE_PATH}\n')
    report_board(board)


def do_prizes():
    data = load_csv()
    have, need = load_prize_rounds(), None
    lo, hi = data[0]['회차'], data[-1]['회차']
    need = [r for r in range(lo, hi+1) if r not in have]
    if not need:
        print(f'당첨금 이력 최신 상태 ({len(have)}회차).')
        return
    print(f'당첨금 백필: {len(need)}회차 필요 (약 {len(need)//10+1}회 호출)')
    got = fetch_rounds(min(need), max(need))
    items = [v for k, v in got.items() if k in set(need)]
    append_prizes(items)
    print(f'  {len(items)}회차 저장 -> {PRIZE_PATH}')
    lost = sorted(set(need) - {it['ltEpsd'] for it in items})
    if lost:
        print(f'  [경고] 공식 응답에 없는 회차 {len(lost)}개: {lost[:20]}')


def do_sales():
    """
    당첨금 CSV의 '총판매액' 열만 공식 API 값으로 다시 쓴다 (SALES_FIELD 주석 참조).
    다른 열은 건드리지 않는다. 다시 받은 값이 기존과 다르면 보고만 한다 — 어느 쪽이
    맞는지는 사람이 판단할 일이다. 공식 응답에 없는 회차는 기존 값을 그대로 둔다.
    전체를 메모리에서 고친 뒤 임시 파일 -> os.replace로 한 번에 바꾼다(중단해도 반쪽 없음).

    마지막 열을 덮어쓰므로 행 모양부터 확인한다. 열이 빠진 행은 5등금액이 판매액으로,
    개행이 빠져 두 행이 붙은 행(이 CSV는 LF/CRLF가 섞여 있다)은 뒤 회차 판매액이 앞
    회차 값으로 덮인다 - 둘 다 보고 없이 지나가므로 고치기 전에 멈춘다.
    조회(약 2분) 동안 sync·수동입력이 행을 추가하면 교체 시 그 행이 사라진다.
    수집과 겹치지 않을 때 돌릴 것.
    """
    if not os.path.exists(PRIZE_PATH):
        print('당첨금 CSV가 없다. --prizes로 먼저 백필할 것.')
        return
    with open(PRIZE_PATH, encoding='utf-8', newline='') as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != PRIZE_HEADER:
        raise RuntimeError(f'당첨금 CSV 헤더가 예상과 다르다: {rows[:1]}')
    body = [r for r in rows[1:] if r]
    if not body:
        raise RuntimeError('당첨금 CSV에 헤더만 있다. --prizes로 먼저 백필할 것.')
    bad = [i + 2 for i, r in enumerate(body) if len(r) != len(PRIZE_HEADER)]
    if bad:
        raise RuntimeError(f'열 개수가 {len(PRIZE_HEADER)}개가 아닌 행(파일 기준 줄): {bad[:10]}')
    rounds = [int(r[0]) for r in body]
    dup = sorted(r for r, c in Counter(rounds).items() if c > 1)
    if dup:
        raise RuntimeError(f'중복 회차: {dup[:10]}')
    print(f'총판매액 재기록: {len(body)}회차 ({min(rounds)}~{max(rounds)}회) 공식값 조회 중')
    got = fetch_rounds(min(rounds), max(rounds))
    changed, lost, other = 0, [], []
    for row in body:
        it = got.get(int(row[0]))
        if it is None or not it.get(SALES_FIELD):     # None·0 = 아직 집계 전으로 본다
            lost.append(int(row[0]))
            continue
        fresh = ['' if v is None else str(v) for v in prize_row(it)]
        if fresh[:-1] != row[:-1]:
            other.append(int(row[0]))
        if row[-1] != fresh[-1]:
            row[-1] = fresh[-1]
            changed += 1
    tmp = PRIZE_PATH + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            w.writerow(PRIZE_HEADER)
            w.writerows(body)
        os.replace(tmp, PRIZE_PATH)
    finally:
        if os.path.exists(tmp):        # 교체 실패(대상 파일이 열려 있음 등) 시 잔여물 정리
            os.remove(tmp)
    print(f'  총판매액 {changed}회차 갱신, {len(body) - changed - len(lost)}회차 기존과 동일')
    if lost:
        print(f'  [경고] 공식 응답에 없어 그대로 둔 회차 {len(lost)}개: {lost[:20]}')
    if other:
        print(f'  [주의] 총판매액 외 열이 공식값과 다른 회차 {len(other)}개(수정 안 함): '
              f'{other[:20]}')


def sync(verbose=True):
    """
    새 회차를 수집해 CSV·당첨금·성적표에 반영하고, 추가된 회차 목록을 돌려준다.
    이 프로젝트의 유일한 수집 경로다 — lotto.py·auto_fetch.py·CI 워크플로가
    모두 이 함수를 부른다. 수집 코드를 다른 곳에 다시 쓰지 말 것.
    반환: [{'회차','번호','보너스','날짜','hits','filters_pass'}, ...] (회차 오름차순)
    """
    def say(*a):
        if verbose:
            print(*a)

    data = load_csv()
    have = data[-1]['회차']
    say(f'CSV 최신: {have}회')

    # 네트워크를 건드리기 전에 지난 실행의 중단 흔적부터 정리한다.
    board = load_board()
    heal_board(data, board, say)

    newest = latest_round(have)
    if newest <= have:
        say(f'추첨 완료된 최신 회차도 {newest}회 — 새 회차 없음.')
        return []

    say(f'공식 최신: {newest}회  ->  {newest-have}개 회차 신규 수집')
    got = fetch_rounds(have+1, newest)
    # 중간 회차가 하나라도 빠지면 아무것도 쓰지 않는다. 뒤 회차를 먼저 쌓으면 CSV 최신
    # 회차가 앞질러 가서, 다음 실행부터는 빠진 회차를 다시 찾지 않는다(영구 결번).
    hole = [r for r in range(have+1, newest+1) if r not in got]
    if hole:
        raise RuntimeError(f'공식 응답에 {hole}회가 없다 (최신 {newest}회) — '
                           f'중간을 건너뛰고 쌓지 않는다. 다음 실행에서 다시 시도한다.')
    have_prize = load_prize_rounds()
    added = []
    for r in sorted(got):
        it = got[r]
        nums = sorted(it[f'tm{i}WnNo'] for i in range(1, 7))
        actual = {'회차': r, '번호': nums}
        # 반드시 CSV 반영 "전"에 채점한다. 순서가 바뀌면 look-ahead가 된다.
        s = score_round(data, actual)
        say(f'  {r}회 {nums}  풀적중 {s["hits"]}개  '
            f'필터 {"통과" if s["filters_pass"] else "탈락"}')
        # 회차 하나를 끝낼 때마다 즉시 저장한다. 루프 밖에서 한 번만 저장하면
        # 중간에 죽었을 때 이미 CSV에 들어간 회차가 성적표에서 영구 누락된다.
        # (CSV -> 성적표 -> 당첨금 순서. 앞의 둘이 어긋나면 heal_board가 복구하고,
        #  당첨금은 --prizes 백필이 복구한다.)
        append_csv([it])
        data.append(actual)
        record(board, s)
        save_board(board)
        if r not in have_prize:
            append_prizes([it])
        added.append({'회차': r, '번호': nums, '보너스': it['bnsWnNo'],
                      '날짜': str(it.get('ltRflYmd') or ''),
                      'hits': s['hits'], 'filters_pass': s['filters_pass']})
    say(f'\nCSV {len(added)}회차 추가, 당첨금·성적표 동시 갱신 완료.')
    return added


def do_update():
    added = sync(verbose=True)
    print()
    report_board(load_board())
    return added


def main():
    args = sys.argv[1:]
    modes = {'--rebuild': do_rebuild, '--prizes': do_prizes, '--sales': do_sales}
    # 모르는 인자는 수집으로 흘려보내지 않는다. 과거에는 --help 같은 인자가 그대로
    # 평상시 경로(공식 사이트 조회 + CSV 기록)를 실행했다.
    if len(args) > 1 or (args and args[0] not in modes):
        print(__doc__)
        sys.exit(0 if args and args[0] in ('-h', '--help') else 2)
    print('='*66)
    print('  회차 누적 반영 파이프라인')
    print('='*66)
    modes.get(args[0] if args else None, do_update)()


if __name__ == '__main__':
    main()
