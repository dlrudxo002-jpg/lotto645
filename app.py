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

    # 페어 친화도: 각 번호가 참여하는 페어의 평균 출현비율
    pair_freq, _, exp_pair, _ = compute_pair_triple(data)
    pair_affinity = {}
    for n in range(1,46):
        ratios = [pair_freq[p]/exp_pair
                  for m in range(1,46) if m != n
                  for p in [(min(n,m), max(n,m))]]
        pair_affinity[n] = (sum(ratios)/len(ratios) - 1.0) * 2.0  # 기대치 초과분

    scores = {}
    for n in range(1,46):
        avg_sk = avg_skip.get(n, total/6)
        skip_s  = (waiting[n]/avg_sk) * 5.0          # 스킵비율 (핵심)
        z_s     = (-(freq[n]-avg_f)/std_f) * 1.5     # 장기 저빈도
        pair_s  = pair_affinity[n] * 1.0              # 페어 친화도
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

def score_combo(combo, scores, pair_freq, triple_freq, exp_pair, exp_triple):
    n = sorted(combo)
    base   = sum(scores[x] for x in n)
    pair_b = sum(pair_freq.get(p,0)/exp_pair - 1.0
                 for p in icombs(n,2))                # 핫페어 보너스
    tri_b  = sum(triple_freq.get(t,0)/exp_triple - 1.0
                 for t in icombs(n,3)) * 0.3          # 핫트리플 보너스 (가중치 낮춤)
    return base + pair_b + tri_b

def get_recommended_pool(data, scores, freq, waiting, avg_skip, size=13):
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
    """당첨번호를 CSV와 history.json에 저장."""
    with open(CSV_PATH, 'a', encoding='utf-8', newline='') as f:
        csv.writer(f).writerow([rnd] + nums + [bonus])
    hist = load_history()
    key  = str(rnd)
    if key not in hist: hist[key] = {}
    hist[key]['actual'] = {'번호': nums, '보너스': bonus, '날짜': date}
    prev_key = str(rnd - 1)
    if prev_key in hist and 'recommend' in hist[prev_key]:
        try:
            from lotto import grade_match
            hist[prev_key]['result'] = grade_match(hist[prev_key]['recommend'],
                                                    {'번호': nums, '보너스': bonus})
        except: pass
    save_history(hist)

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
    save_draw(rnd, nums, bonus, date)
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

@app.route('/api/generate', methods=['POST'])
def api_generate():
    body    = request.json
    pool    = body.get('pool', [])
    filters = body.get('filters', {})
    count   = body.get('count', 10)   # 5/10/20/30/40/50/'all'
    mode    = body.get('mode', 'random')  # random / all

    if not pool or len(pool) < 6:
        return jsonify({'error': '번호를 6개 이상 선택하세요.'}), 400

    # 페어/트리플 빈도 및 점수 준비
    data = load_data()
    sc, _, _, _ = compute_scores(data)
    pf, tf, ep, et = compute_pair_triple(data)

    # 필터 통과 조합 생성
    results = []
    for combo in icombs(pool, 6):
        if apply_filters(combo, filters):
            results.append(combo)

    total_pass = len(results)

    if mode == 'all':
        games_sorted = [list(c) for c in results]
        return jsonify({'games': games_sorted, 'total': total_pass, 'mode': 'all'})

    # 점수 상위 정렬 후 상위 N개 반환
    scored = sorted(results, key=lambda c: score_combo(c, sc, pf, tf, ep, et), reverse=True)

    if count == 'all':
        games = [list(c) for c in scored]
    else:
        count = int(count)
        games = [list(c) for c in scored[:count]]

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
        })

    return jsonify({
        'games':      game_info,
        'total_pass': total_pass,
        'requested':  len(games),
        'mode':       'scored',
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
            res = entry.get('result')
            if not res:
                actual_set = set(act['번호'])
                res = []
                for g in recs:
                    m = len(set(g) & actual_set)
                    b = act['보너스'] in g
                    gr = '1등' if m==6 else '2등' if m==5 and b else '3등' if m==5 else '4등' if m==4 else '5등' if m==3 else '낙첨'
                    res.append({'game': g, 'match': m, 'grade': gr})
            grades = res

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

    if rnd <= 0 or len(nums) != 6 or not bonus:
        return jsonify({'error': '입력값 오류'}), 400
    if any(n < 1 or n > 45 for n in nums + [bonus]):
        return jsonify({'error': '번호는 1~45 범위여야 합니다'}), 400

    # 중복 확인
    data = load_data()
    existing = {d['회차'] for d in data}
    if rnd in existing:
        return jsonify({'error': f'{rnd}회차는 이미 등록되어 있습니다'}), 400

    # CSV 추가
    with open(CSV_PATH, 'a', encoding='utf-8', newline='') as f:
        csv.writer(f).writerow([rnd] + nums + [bonus])

    # history 업데이트
    hist = load_history()
    key  = str(rnd)
    if key not in hist: hist[key] = {}
    hist[key]['actual'] = {'번호': nums, '보너스': bonus}
    prev_key = str(rnd - 1)
    if prev_key in hist and 'recommend' in hist[prev_key]:
        try:
            from lotto import grade_match
            hist[prev_key]['result'] = grade_match(hist[prev_key]['recommend'],
                                                    {'번호': nums, '보너스': bonus})
        except: pass
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
