# -*- coding: utf-8 -*-
from flask import Flask, render_template, jsonify, request, Response
import csv, json, os, random, urllib.request, io
from collections import Counter, defaultdict
from itertools import combinations as icombs

app  = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, 'lotto645_전체.csv')
HIST     = os.path.join(BASE, 'history.json')

PRIMES  = {2,3,5,7,11,13,17,19,23,29,31,37,41,43}
SQUARES = {1,4,9,16,25,36}
DOUBLES = {11,22,33,44}
CORNERS = {1,2,8,9,6,7,13,14,29,30,36,37,34,35,41,42}

SAVE_TOKEN   = os.environ.get('SAVE_TOKEN', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO  = 'ktlee-beep/lotto'
WORKFLOW_FILE = 'lotto-fetch.yml'

# ── 데이터 로드 ───────────────────────────────────────────────────
def load_data():
    data = []
    with open(CSV_PATH, encoding='utf-8') as f:
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

# ── 분석 함수 ─────────────────────────────────────────────────────
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

def compute_pair_triple(data):
    total = len(data)
    pair_freq   = Counter()
    triple_freq = Counter()
    for d in data:
        for p in icombs(d['번호'], 2): pair_freq[p]   += 1
        for t in icombs(d['번호'], 3): triple_freq[t] += 1
    exp_pair   = total * 15 / (45*44/2)      # ≈ 18.5
    exp_triple = total * 20 / (45*44*43/6)   # ≈ 1.72
    return pair_freq, triple_freq, exp_pair, exp_triple

# 번호 점수 가중치 (2026-09-13 재검증) — 근거는 lotto.py 동일 상수 주석 참조.
# 스킵비율 5.0 가중은 워크포워드 백테스팅에서 초기하분포 기대치를 하회해 폐기.
# 세 항 동등 가중, 점수는 예측 지표가 아니라 풀 구성용 타이브레이크.
W_SKIP = 1.0
W_Z    = 1.0
W_PAIR = 1.0

def compute_scores(data):
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

    # 페어 친화도 — 닫힌형. 유도와 근거는 lotto.compute_scores 주석 참조.
    # 독립 정보가 아니라 빈도의 선형 재표현이다(Z역 항과 Spearman -0.996).
    pair_affinity = {n: 15.0*freq[n]/total - 2.0 for n in range(1,46)}

    scores = {}
    for n in range(1,46):
        avg_sk = avg_skip.get(n, total/6)
        skip_s  = (waiting[n]/avg_sk) * W_SKIP       # 스킵비율 (예측력 없음 — 타이브레이크)
        z_s     = (-(freq[n]-avg_f)/std_f) * W_Z     # 장기 저빈도
        pair_s  = pair_affinity[n] * W_PAIR          # 페어 친화도
        scores[n] = skip_s + z_s + pair_s
    return scores, freq, waiting, avg_skip

def compute_wheel(pool, k=3):
    """
    최소 커버링 휠링: pool 내 k개 이상이 당첨번호에 포함되면 k등 이상 1장 보장
    k=3 → 5등 보장 / k=4 → 4등 보장
    """
    pool = sorted(pool)
    n = len(pool)
    all_6   = list(icombs(range(n), 6))
    cov     = [frozenset(icombs(s, k)) for s in all_6]
    uncov   = set(icombs(range(n), k))
    result  = []
    while uncov:
        best_i = max(range(len(all_6)), key=lambda i: len(cov[i] & uncov))
        if not (cov[best_i] & uncov): break
        result.append(best_i)
        uncov -= cov[best_i]
    return [[pool[i] for i in all_6[wi]] for wi in result]

# 조합 점수의 페어/트리플 가중치 — 근거·재보정 주의사항은 lotto.py 동일 상수 주석 참조.
W_COMBO_PAIR = 0.286
W_COMBO_TRI  = 0.086   # = 0.3(기존 트리플 계수) x 0.286

def score_combo(combo, scores, pair_freq, triple_freq, exp_pair, exp_triple):
    n = sorted(combo)
    base   = sum(scores[x] for x in n)
    pair_b = sum(pair_freq.get(p,0)/exp_pair - 1.0
                 for p in icombs(n,2)) * W_COMBO_PAIR      # 핫페어 보너스
    tri_b  = sum(triple_freq.get(t,0)/exp_triple - 1.0
                 for t in icombs(n,3)) * W_COMBO_TRI       # 핫트리플 보너스
    return base + pair_b + tri_b

def get_recommended_pool(data, scores, freq, waiting, avg_skip, size=15):
    ranked = sorted(scores.items(), key=lambda x:-x[1])
    zones  = [(1,9),(10,19),(20,29),(30,39),(40,45)]
    pool   = []
    for lo, hi in zones:
        best = next((n for n,_ in ranked if lo<=n<=hi and n not in pool), None)
        if best: pool.append(best)
    for n,_ in ranked:
        if len(pool) >= size: break
        if n not in pool: pool.append(n)
    odds = sum(1 for n in pool if n%2==1)
    if odds < 5:
        pool_set = set(pool)
        zone_must = pool[:5]
        for n,_ in ranked:
            if n%2==1 and n not in pool_set:
                removable = [p for p in pool if p%2==0 and p not in zone_must]
                if removable:
                    pool.remove(min(removable, key=lambda x: scores[x]))
                    pool.append(n); pool_set = set(pool)
                    if sum(1 for x in pool if x%2==1) >= 5: break
    return sorted(pool)

# ── 필터 적용 ─────────────────────────────────────────────────────
def apply_filters(combo, f):
    n   = sorted(combo)
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

    # 홀짝
    if f.get('odd_even'):
        allowed = set(f['odd_even'])
        if odd not in allowed: return False
    # 고저
    if f.get('high_low'):
        allowed = set(f['high_low'])
        if hi not in allowed: return False
    # 번호 총합
    if f.get('sum_range'):
        lo, hi2 = f['sum_range']
        if not (lo <= s <= hi2): return False
    # 끝수 합
    if f.get('end_sum_range'):
        lo, hi2 = f['end_sum_range']
        if not (lo <= es <= hi2): return False
    # 같은 끝수
    if f.get('same_end') is not None:
        allowed = set(f['same_end'])
        # 'none'=끝수 모두 다름(max_end==1), '2쌍2'=max2개짜리2쌍, '2쌍3'=2개짜리3쌍
        result_val = None
        if max_end == 1: result_val = 'none'
        elif max_end == 2:
            pairs = sum(1 for v in ec.values() if v==2)
            if pairs == 1:   result_val = '2'
            elif pairs == 2: result_val = '2pair2'
            elif pairs == 3: result_val = '2pair3'
        elif max_end == 3: result_val = '3'
        elif max_end == 4: result_val = '4'
        if result_val not in allowed: return False
    # 동일구간
    if f.get('zone_max') is not None:
        allowed = set(f['zone_max'])
        if zm not in allowed: return False
    # 연속번호
    if f.get('consec') is not None:
        allowed = set(f['consec'])
        if not runs:      key = 'none'
        elif len(runs)==1 and runs[0]==2: key = '2x1'
        elif len(runs)==2 and all(r==2 for r in runs): key = '2x2'
        elif len(runs)==1 and runs[0]==3: key = '3x1'
        else: key = 'other'
        if key not in allowed: return False
    # 소수
    if f.get('prime') is not None:
        if prime_c not in set(f['prime']): return False
    # 제곱수
    if f.get('square') is not None:
        if sq_c not in set(f['square']): return False
    # 합성수
    if f.get('composite') is not None:
        if comp_c not in set(f['composite']): return False
    # 동형수(쌍수)
    if f.get('double') is not None:
        if double_c not in set(f['double']): return False
    # AC값
    if f.get('ac_vals') is not None:
        if ac_v not in set(f['ac_vals']): return False
    # 3의 배수
    if f.get('mul3') is not None:
        if mul3_c not in set(f['mul3']): return False
    # 5의 배수
    if f.get('mul5') is not None:
        if mul5_c not in set(f['mul5']): return False
    return True

# ── API 라우트 ────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def api_status():
    data = load_data()
    scores, freq, waiting, avg_skip = compute_scores(data)
    pool = get_recommended_pool(data, scores, freq, waiting, avg_skip)
    latest = data[-1]
    next_rnd = latest['회차']+1

    # 번호별 상세 정보
    total = len(data)
    num_info = {}
    for n in range(1,46):
        avg_sk = avg_skip.get(n, total/6)
        ratio  = waiting[n]/avg_sk
        num_info[n] = {
            'score': round(scores[n],2),
            'freq':  freq[n],
            'wait':  waiting[n],
            'ratio': round(ratio,2),
            'recommended': n in pool
        }

    hist = load_history()
    has_rec = str(next_rnd) in hist and 'recommend' in hist[str(next_rnd)]

    # 최신회차 날짜 (history에서)
    latest_date = ''
    key = str(latest['회차'])
    if key in hist and 'actual' in hist[key]:
        latest_date = hist[key]['actual'].get('날짜', '')

    return jsonify({
        'latest_round': latest['회차'],
        'latest_nums':  latest['번호'],
        'latest_bonus': latest['보너스'],
        'latest_date':  latest_date,
        'next_round':   next_rnd,
        'has_recommend': has_rec,
        'recommended_pool': pool,
        'num_info': num_info,
        'total_rounds': total
    })

def save_draw(rnd, nums, bonus, date):
    """
    당첨번호를 CSV·성적표·history.json에 저장.
    CSV 쓰기는 accumulate.record_manual에 위임한다 — 직접 쓰면 성적표가
    그 회차를 영영 건너뛴다(중복 확인·채점 순서도 거기서 함께 보장된다).
    반환: False = 이미 있는 회차라 아무것도 쓰지 않음.
    """
    # 채점을 CSV 기록보다 **먼저** 한다. 손상된 recommend 때문에 채점이 터져도
    # CSV·성적표에만 남고 history.json에는 없는 반쪽 상태가 생기지 않는다
    # (hist는 메모리에만 있고 save_history까지 가야 디스크에 반영된다).
    from lotto import record_actual
    hist = load_history()
    record_actual(hist, rnd, nums, bonus, date)

    import accumulate
    if accumulate.record_manual(rnd, nums, bonus) == 'duplicate':
        return False   # hist 변경분은 저장하지 않고 버린다
    save_history(hist)
    return True

@app.route('/api/save_draw', methods=['POST'])
def api_save_draw():
    """GitHub Actions에서 수집한 당첨번호를 저장하는 엔드포인트."""
    if request.headers.get('X-Save-Token') != SAVE_TOKEN:
        return jsonify({'error': 'unauthorized'}), 401
    body  = request.json
    rnd   = int(body['rnd'])
    nums  = sorted([int(x) for x in body['nums']])
    bonus = int(body['bonus'])
    date  = body.get('date', '')
    data  = load_data()
    if rnd <= data[-1]['회차']:
        return jsonify({'status': 'already exists', 'rnd': rnd})
    if not save_draw(rnd, nums, bonus, date):
        return jsonify({'status': 'already exists', 'rnd': rnd})
    return jsonify({'status': 'saved', 'rnd': rnd, 'nums': nums, 'bonus': bonus})

@app.route('/api/fetch', methods=['POST'])
def api_fetch():
    """GitHub Actions 워크플로우를 트리거하여 최신 회차를 수집 요청."""
    try:
        req = urllib.request.Request(
            f'https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches',
            data=json.dumps({'ref': 'main'}).encode(),
            headers={
                'Authorization': f'Bearer {GITHUB_TOKEN}',
                'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json',
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            res.read()
        return jsonify({'status': 'triggered', 'message': '수집 요청됨. 약 1분 후 새로고침하세요.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

MAX_SPREAD_GAMES = 50     # 게임 수 상한 (측정·검증 범위). 전체추출('all')은 해당 없음
# 45개 분산 배치의 필터 평가 횟수 상한 — 무료 서버 요청 시간 초과(30초) 방지.
# 시간이 아니라 횟수로 끊으므로 같은 입력이면 서버 속도와 무관하게 같은 게임이 나온다.
SPREAD_MAX_EVALS = 60_000
# '선택 번호 안에서만'(pool 배치·전체추출)은 선택 번호의 6개 조합을 전부 검사한다.
# 22개 = 74,613조합, 로컬 최대 2.4초(전체추출). 24개는 4.7초로 무료 서버(로컬의 약 5~6배)에서
# 30초에 근접한다 [확인 필요: 서버 배율은 1회 실측 기반]. 번호가 더 많으면 45개 분산 배치를 쓴다.
MAX_POOL_NUMS = 22

# 켜진 필터의 화면 이름 (안내 문구용)
LIST_FILTERS = {
    'odd_even': '홀 : 짝 비율', 'high_low': '고 : 저 비율', 'same_end': '같은 끝수',
    'zone_max': '동일구간', 'consec': '연속번호', 'prime': '소수', 'square': '제곱수',
    'composite': '합성수', 'double': '동형수', 'ac_vals': 'A/C값',
    'mul3': '3의 배수', 'mul5': '5의 배수',
}
RANGE_FILTERS = {'sum_range': '번호 총합 구간', 'end_sum_range': '끝수 총합 구간'}


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check_filters(f):
    """
    필터 본문 검사. 문제가 있으면 안내 문구, 없으면 None.
    켜진 필터에 허용 값이 하나도 없으면 apply_filters 는 홀짝·고저는 '필터 꺼짐'으로,
    나머지는 '전부 탈락'으로 처리한다. 둘 다 사용자가 뜻한 동작이 아니므로 여기서 막는다.
    """
    if not isinstance(f, dict):
        return '필터 형식이 올바르지 않습니다.'
    for k, name in LIST_FILTERS.items():
        v = f.get(k)
        if v is None:
            continue
        if not isinstance(v, list):
            return f"'{name}' 필터 값 형식이 올바르지 않습니다."
        if not v:
            return f"'{name}' 필터가 켜져 있는데 허용 값이 하나도 선택되지 않았습니다. 값을 고르거나 필터를 끄세요."
        if not all(_is_num(x) or isinstance(x, str) for x in v):
            return f"'{name}' 필터 값 형식이 올바르지 않습니다."
    for k, name in RANGE_FILTERS.items():
        v = f.get(k)
        if v is None:
            continue
        if not isinstance(v, list) or len(v) != 2 or not all(_is_num(x) for x in v):
            return f"'{name}' 필터에 최솟값과 최댓값을 숫자로 넣으세요."
        if v[0] > v[1]:
            return f"'{name}' 필터의 최솟값이 최댓값보다 큽니다."
    return None


def any_combo_passes(ok, tries=4000):
    """무작위 조합 tries개 안에 필터를 통과하는 것이 하나라도 있는가 (찾는 즉시 멈춘다)."""
    rng = random.Random(0)
    return any(ok(sorted(rng.sample(range(1, 46), 6))) for _ in range(tries))

@app.route('/api/generate', methods=['POST'])
def api_generate():
    body    = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': '요청 형식이 올바르지 않습니다.'}), 400
    filters = body.get('filters')
    if filters is None:
        filters = {}
    msg = check_filters(filters)
    if msg:
        return jsonify({'error': msg}), 400
    count   = body.get('count', 10)   # 5/10/20/30/40/50/'all'
    mode    = body.get('mode', 'random')  # random / all
    # 배치 방식 (2026-10-13)
    #   spread45 : 45개 번호 전체에 고르게 나눠 쓰고 선택 번호를 더 자주 쓴다 (기본, 적중 우선)
    #   pool     : 선택 번호 안에서만 고르되 서로 덜 겹치는 조합을 고른다
    # 과거 방식(선택 번호 안 조합점수 상위 N개)은 게임끼리 4~5개씩 겹쳐 같이 맞고 같이 틀렸다.
    # 1246회 풀 10게임 기준 P(최소 1게임 5등 이상): 과거 8.88% / pool 17.08% / spread45 23.01%.
    # 근거와 측정은 optimizer.py 4-3절.
    layout  = body.get('layout', 'spread45')

    raw_pool = body.get('pool', [])
    if not isinstance(raw_pool, list) or not all(
            isinstance(x, int) and not isinstance(x, bool) for x in raw_pool):
        return jsonify({'error': '번호 형식이 올바르지 않습니다.'}), 400
    pool = sorted(set(raw_pool))
    if len(pool) < 6:
        return jsonify({'error': '번호를 6개 이상 선택하세요.'}), 400
    if pool[0] < 1 or pool[-1] > 45:
        return jsonify({'error': '번호는 1~45 사이여야 합니다.'}), 400
    if layout not in ('spread45', 'pool'):
        return jsonify({'error': f'알 수 없는 배치 방식: {layout}'}), 400
    if count != 'all':
        # 화면은 게임 수를 문자열("10")로 보낸다. 참/거짓·소수는 받지 않는다.
        if isinstance(count, str) and count.strip().isdigit():
            count = int(count)
        if not isinstance(count, int) or isinstance(count, bool):
            return jsonify({'error': '게임 수 형식이 올바르지 않습니다.'}), 400
        if not 1 <= count <= MAX_SPREAD_GAMES:
            return jsonify({'error': f'게임 수는 1~{MAX_SPREAD_GAMES} 사이여야 합니다.'}), 400
    # 전체추출은 선택 번호 안의 통과 조합을 전부 내는 기능이므로 분산 배치와 함께 쓸 수 없다
    if count == 'all' or mode == 'all':
        layout = 'pool'
    if layout == 'pool' and len(pool) > MAX_POOL_NUMS:
        return jsonify({'error': f"'선택 번호 안에서만'과 전체추출은 번호 {MAX_POOL_NUMS}개까지 지원합니다. "
                                 f"지금 {len(pool)}개를 골랐습니다. 번호를 줄이거나 '45개 전체 분산'을 쓰세요."}), 400

    import optimizer as O    # 지연 import — 다른 API 응답에 import 비용을 얹지 않는다

    # 페어/트리플 빈도 및 점수 준비
    data = load_data()
    sc, _, _, _ = compute_scores(data)
    pf, tf, ep, et = compute_pair_triple(data)
    ok = lambda g: apply_filters(g, filters)

    patched = failed = 0
    total_pass = None
    if layout == 'spread45':
        # 통과 조합이 거의 없는 조건이면 탐색이 상한까지 헛돈다 — 미리 알린다
        if not any_combo_passes(ok):
            return jsonify({'error': '필터 조건이 너무 좁습니다. 무작위 조합 4,000개 중 통과하는 '
                                     '조합이 하나도 없습니다. 허용 값을 넓히거나 필터를 끄세요.'}), 400
        priority = (sorted(pool, key=lambda x: -sc[x]) +
                    sorted((x for x in range(1, 46) if x not in pool), key=lambda x: -sc[x]))
        tickets, patched, failed = O.spread_tickets(count, priority, ok, pinned=len(pool),
                                                    seed=data[-1]['회차'] + 1,
                                                    max_evals=SPREAD_MAX_EVALS)
        games = [list(t) for t in tickets]
    else:
        # 필터 통과 조합 생성
        results = [c for c in icombs(pool, 6) if ok(c)]
        total_pass = len(results)

        if mode == 'all':
            games_sorted = [list(c) for c in results]
            return jsonify({'games': games_sorted, 'total': total_pass, 'mode': 'all'})

        # 점수순 정렬 후, 서로 덜 겹치게 N개 (상위 N개를 그대로 자르면 거의 같은 조합만 남는다)
        scored = sorted(results, key=lambda c: score_combo(c, sc, pf, tf, ep, et), reverse=True)
        if count == 'all':
            games = [list(c) for c in scored]
        else:
            games = [list(c) for c in O.pick_spread(scored, count)]

    # P(최소 1게임 5등 이상) — 표시용. 전체추출처럼 게임이 많으면 계산하지 않는다.
    p_win = p_hw = p_indep = None
    if 0 < len(games) <= MAX_SPREAD_GAMES:
        p_win, p_hw = O.p_win_fast(games, mc_iters=50_000)
        p_indep = 1 - (1 - float(O.P_SINGLE_WIN)) ** len(games)   # P_SINGLE_WIN 은 Fraction
    use = Counter(x for g in games for x in g)
    pool_set = set(pool)

    # 각 게임 통계
    game_info = []
    for g in games:
        n = sorted(g)
        game_info.append({
            'nums':    n,
            'sum':     sum(n),
            'odd':     sum(1 for x in n if x%2==1),
            'ac':      ac(n),
            'zone_mx': zone_max(n),
            'end_sum': sum(x%10 for x in n),
            'combo_score': round(score_combo(n, sc, pf, tf, ep, et), 2),
            'in_pool': sum(1 for x in n if x in pool_set),
            # pool 배치는 통과 조합에서만 고르므로 다시 검사하지 않는다
            'filter_ok': True if layout == 'pool' else bool(ok(n)),
        })

    return jsonify({
        'games':      game_info,
        'total_pass': total_pass,
        'requested':  len(games),
        'mode':       'scored',
        'layout':     layout,
        'p_win':      p_win,
        'p_hw':       p_hw,
        'p_indep':    p_indep,
        'patched':    patched,
        'failed':     failed,
        'pool_uses':  sorted({use[x] for x in pool}),
        'other_uses': sorted({use[x] for x in range(1, 46) if x not in pool_set}),
    })

@app.route('/api/download', methods=['POST'])
def api_download():
    body    = request.json
    pool    = body.get('pool', [])
    filters = body.get('filters', {})

    results = []
    for combo in icombs(pool, 6):
        if apply_filters(combo, filters):
            results.append(list(combo))

    buf = io.StringIO()
    buf.write('번호1,번호2,번호3,번호4,번호5,번호6,합계,AC값\n')
    for g in results:
        n = sorted(g)
        buf.write(f"{','.join(map(str,n))},{sum(n)},{ac(n)}\n")

    return Response(
        '\ufeff' + buf.getvalue(),  # BOM for Excel
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=lotto_combinations.csv'}
    )

@app.route('/api/wheel', methods=['POST'])
def api_wheel():
    body = request.json
    pool = body.get('pool', [])
    k    = int(body.get('k', 3))   # 3=5등보장, 4=4등보장

    if len(pool) < 6:
        return jsonify({'error': '번호를 6개 이상 선택하세요.'}), 400
    if k not in (3, 4):
        return jsonify({'error': 'k는 3 또는 4만 지원합니다.'}), 400

    data = load_data()
    sc, _, _, _ = compute_scores(data)
    pf, tf, ep, et = compute_pair_triple(data)

    wheel = compute_wheel(pool, k)

    grade_label = {3: '5등(3개 일치)', 4: '4등(4개 일치)'}
    guarantee   = f'풀 내 {k}개 이상 당첨번호 포함 시 → {grade_label[k]} 이상 1장 보장'

    games = []
    for combo in wheel:
        n = sorted(combo)
        f_ok = apply_filters(n, {
            'odd_even': [1,2,3,4,5], 'high_low': [1,2,3,4,5],
            'sum_range': [80,190], 'end_sum_range': [14,35],
            'same_end': ['none','2','2pair2'], 'zone_max': [2,3],
            'consec': ['none','2x1'], 'prime': [0,1,2,3],
            'square': [0,1,2], 'composite': [3,4,5],
            'double': [0,1], 'mul3': [1,2,3], 'mul5': [0,1,2],
        })
        games.append({
            'nums':        n,
            'sum':         sum(n),
            'odd':         sum(1 for x in n if x%2==1),
            'ac':          ac(n),
            'end_sum':     sum(x%10 for x in n),
            'combo_score': round(score_combo(n, sc, pf, tf, ep, et), 2),
            'filter_ok':   f_ok,
        })

    # 점수 정렬
    games.sort(key=lambda g: g['combo_score'], reverse=True)

    filter_pass = sum(1 for g in games if g['filter_ok'])
    return jsonify({
        'wheel':       games,
        'total':       len(games),
        'filter_pass': filter_pass,
        'k':           k,
        'guarantee':   guarantee,
    })

@app.route('/api/history')
def api_history():
    hist    = load_history()
    data    = load_data()
    act_map = {d['회차']: d for d in data}
    result  = []

    for key in sorted(hist.keys(), key=int, reverse=True)[:20]:
        rnd   = int(key)
        entry = hist[key]
        recs  = entry.get('recommend', [])
        act   = entry.get('actual')
        if not act and rnd in act_map:
            act = {'번호': act_map[rnd]['번호'], '보너스': act_map[rnd]['보너스']}

        grades = []
        if recs and act:
            # 등수 판정은 lotto.grade_match 하나만 쓴다. 과거에는 이 자리에
            # 같은 판정식이 한 벌 더 인라인으로 복사돼 있었다.
            from lotto import grade_match
            grades = entry.get('result') or grade_match(recs, act)

        result.append({
            'round': rnd,
            'generated': entry.get('generated',''),
            'actual': act,
            'recommend': recs,
            'grades': grades
        })
    return jsonify(result)

@app.route('/api/manual_add', methods=['POST'])
def api_manual_add():
    body  = request.json
    rnd   = int(body.get('round', 0))
    nums  = sorted([int(n) for n in body.get('nums', [])])
    bonus = int(body.get('bonus', 0))
    # 추첨일은 선택 입력이다. 없으면 기록하지 않는다 — 오늘 날짜를 넣으면
    # 과거 회차를 수동 등록할 때 추첨일이 조작된다(/api/status의 latest_date가
    # 그 값을 그대로 보여준다). 미입력 시 날짜칸이 비는 것은 구 동작과 같다.
    date  = body.get('date') or None

    if rnd <= 0 or len(nums) != 6 or not bonus:
        return jsonify({'error': '입력값 오류'}), 400
    if any(n < 1 or n > 45 for n in nums + [bonus]):
        return jsonify({'error': '번호는 1~45 범위여야 합니다'}), 400

    # 순서·이유는 save_draw와 동일하다 (채점 먼저, CSV 다음, 저장 마지막).
    from lotto import record_actual
    hist = load_history()
    record_actual(hist, rnd, nums, bonus, date)

    # CSV 추가 — 중복 확인·채점·성적표 갱신을 accumulate가 한 곳에서 처리한다.
    # 여기서 직접 CSV에 쓰면 그 회차는 성적표에서 영구 누락된다.
    import accumulate
    if accumulate.record_manual(rnd, nums, bonus) == 'duplicate':
        return jsonify({'error': f'{rnd}회차는 이미 등록되어 있습니다'}), 400
    save_history(hist)

    return jsonify({'ok': True, 'round': rnd, 'nums': nums, 'bonus': bonus})

@app.route('/api/draws')
def api_draws():
    data = load_data()
    result = []
    for d in reversed(data):
        result.append({
            'round': d['회차'],
            'nums':  d['번호'],
            'bonus': d['보너스']
        })
    return jsonify(result)

if __name__ == '__main__':
    print("로또645 웹 서버 시작: http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)
