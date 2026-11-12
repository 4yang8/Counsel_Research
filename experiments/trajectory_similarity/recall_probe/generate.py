"""Recall probe: can the client model later recover the covert state it committed at turn k?

For each saved dialogue (both conditions) and target turns k (nearest turn to each --k with a
non-empty withheld_content, within +-2), sample:
  regen        the saved turn-k main request (stage_client_update) re-sent unchanged
               -> stochastic spread of the covert state at the same moment
  probe_k      recall question asked right after client turn k (public dialogue through client k,
               no past records)                      -> recoverable from public info at k
  probe_T_none recall question after the last client turn T, no past records
  probe_T_sel  same, with the records the memory selector actually injected at turn T
               (Q3inner_memory dialogues only)
  probe_T_withk same, with the turn-k record itself injected (sanity upper bound, 1 sample)
The recall question is out-of-band (never added to the dialogue). Inputs reuse the saved main
request of the corresponding turn (system prompt, basic_profile, selected_traits, public
dialogue), so nothing in src/ is changed or called. Results are appended to raw.jsonl and the
run can be resumed with --resume <run dir>.
"""
import argparse
import concurrent.futures as cf
import copy
from datetime import datetime
import glob
import json
import os
from pathlib import Path
import threading
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
VERSIONS = ['Q3inner_memory', 'inner_memory_no']


def encode(value):  # identical to src/client/memory_access/memory.encode
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def is_none(w):
    w = (w or '').strip().strip('.').strip()
    return not w or w.startswith('없음') or w in ('해당 없음', '불확실')


PROBE = """[실험용 회상 질문 — 상담사에게 전달되지 않으며 상담 대화의 일부가 아닙니다]
{k}턴에서 상담사는 다음과 같이 말했습니다.
"{counselor}"
당신은 이렇게 답했습니다.
"{client}"

그 답을 하던 {k}턴 당시의 마음을 recall_private_position으로 기록하세요. 지금의 마음이 아니라 {k}턴 당시의 마음을 기록합니다.
- actual_wish: 당시 실제로 원한 결과·선택·관계상의 바람
- intended_message: 당시 그 발화로 상담사에게 전달하려 한 의미
- withheld_content: 당시 알고 있었지만 그 발화에서 말하지 않기로 한 구체적인 생각·감정·이유 (없었다면 '없음')
- withholding_reason: 그 내용을 말하지 않기로 한 이유 (보류 내용이 없으면 '해당 없음')"""


def recall_tool(main_payload):
    pp = copy.deepcopy(main_payload['tools'][0]['function']['parameters']['properties']['private_position'])
    return {'type': 'function', 'function': {
        'name': 'recall_private_position',
        'description': '지정한 과거 턴 당시의 비공개 입장을 회상해 기록합니다. 상담 대화에는 공개되지 않습니다.',
        'parameters': pp}}


def main_payload(record):
    mains = [x['payload'] for x in record['model_requests'] if x['stage'] == 'main']
    if not mains or mains[0].get('tool_choice', {}).get('function', {}).get('name') != 'stage_client_update':
        raise ValueError('stage_client_update 요청 없음')
    return mains[0]


def probe_payload(base, records, k, upto, private_turns):
    """base: saved main request of turn `upto`; public dialogue extended with client turn `upto`."""
    p = copy.deepcopy(base)
    p.pop('stream_options', None)
    p['stream'] = False
    data = json.loads(p['messages'][1]['content'])
    if data['public_dialogue'][-1]['role'] != 'counselor':
        raise ValueError('기준 요청의 마지막 공개 발화가 상담사가 아님')
    data['public_dialogue'] = data['public_dialogue'] + [{'role': 'client', 'content': records[upto - 1]['utterance']}]
    data['private_turns'] = private_turns
    rk = records[k - 1]
    p['messages'] = [p['messages'][0], {'role': 'user', 'content': encode(data)},
                     {'role': 'user', 'content': PROBE.format(k=k, counselor=rk['counselor_utterance'], client=rk['utterance'])}]
    p['tools'] = [recall_tool(base)]
    p['tool_choice'] = {'type': 'function', 'function': {'name': 'recall_private_position'}}
    return p


def regen_payload(base):
    p = copy.deepcopy(base)
    p.pop('stream_options', None)
    p['stream'] = False
    return p


def call(url, payload, timeout, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url.rstrip('/') + '/chat/completions', data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'})
            t = time.time()
            resp = json.load(urllib.request.urlopen(req, timeout=timeout))
            ch = resp['choices'][0]
            tc = ch['message'].get('tool_calls') or []
            args = json.loads(tc[0]['function']['arguments'])
            pp = args['private_position'] if 'private_position' in args else args
            for f in ('actual_wish', 'intended_message', 'withheld_content', 'withholding_reason'):
                if not isinstance(pp.get(f), str):
                    raise ValueError(f'필드 없음: {f}')
            return {'private_position': pp, 'raw_arguments': args, 'reasoning': ch['message'].get('reasoning'),
                    'finish_reason': ch['finish_reason'], 'usage': resp.get('usage'), 'seconds': round(time.time() - t, 2),
                    'attempts': attempt + 1}
        except Exception as e:  # noqa: BLE001
            last = f'{type(e).__name__}: {e}'
    return {'error': last, 'attempts': retries + 1}


def load_dialogues(batch, turns):
    job_dir = (PROJECT / batch).resolve()
    job = json.loads((job_dir / 'job.json').read_text(encoding='utf-8'))
    out = []
    for r in job['rows']:
        sid = r['session_id']
        session = json.loads((PROJECT / 'runs/paired' / sid / 'session.json').read_text(encoding='utf-8'))
        current = Path(session['client_storage_dir']) / r['version'] / sid / 'memory_state.json' if session.get('client_storage_dir') else None
        states = [str(current)] if current and current.is_file() else glob.glob(str(PROJECT / 'runs/paired' / sid / 'client_attempts' / '*' / r['version'] / '*' / 'memory_state.json'))
        state = max(states, key=os.path.getmtime)
        recs = json.loads(Path(state).read_text(encoding='utf-8'))['records'][:turns]
        if len(recs) < turns:
            raise ValueError(f'턴 부족: {sid}')
        out.append({'job': job['id'], 'version': r['version'], 'profile': r['profile_id'], 'session_id': sid,
                    'state': str(Path(state).relative_to(PROJECT)), 'records': recs})
    return out


def pick_k(records, target):
    for d in (0, 1, -1, 2, -2):
        k = target + d
        if 2 <= k < len(records) and not is_none(records[k - 1]['inner']['private_position']['withheld_content']):
            return k
    return None


def tasks_for(d, ks, n):
    recs, T = d['records'], len(d['records'])
    base_T = main_payload(recs[T - 1])
    sel_T = json.loads(base_T['messages'][1]['content'])['private_turns']
    out = []
    for target in ks:
        k = pick_k(recs, target)
        if k is None:
            continue
        rk = recs[k - 1]
        chunk_k = {'inner': rk['inner'], 'turn': k, 'utterance': rk['utterance']}
        settings = [('regen', regen_payload(main_payload(rk)), n),
                    ('probe_k', probe_payload(main_payload(rk), recs, k, k, []), n),
                    ('probe_T_none', probe_payload(base_T, recs, k, T, []), n),
                    ('probe_T_withk', probe_payload(base_T, recs, k, T, [chunk_k]), 1)]
        if d['version'] == 'Q3inner_memory':
            settings.append(('probe_T_sel', probe_payload(base_T, recs, k, T, sel_T), n))
        for name, payload, reps in settings:
            for i in range(reps):
                out.append({'key': f"{d['session_id']}|{k}|{name}|{i}", 'job': d['job'], 'version': d['version'],
                            'profile': d['profile'], 'session_id': d['session_id'], 'k': k, 'target_k': target,
                            'T': T, 'setting': name, 'sample': i,
                            'sel_T_turns': [c['turn'] for c in sel_T] if name == 'probe_T_sel' else None,
                            'payload': payload})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch', default='runs/profile_batches/batch_08f865c61ebd4284a5ae')
    ap.add_argument('--turns', type=int, default=20)
    ap.add_argument('--k', type=int, nargs='+', default=[5, 10])
    ap.add_argument('--samples', type=int, default=3)
    ap.add_argument('--vllm-url', default='http://192.168.0.93:8002/v1')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--timeout', type=int, default=300)
    ap.add_argument('--resume')
    args = ap.parse_args()
    run = Path(args.resume) if args.resume else HERE / 'results' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    run.mkdir(parents=True, exist_ok=True)
    dialogues = load_dialogues(args.batch, args.turns)
    tasks = [t for d in dialogues for t in tasks_for(d, args.k, args.samples)]
    raw = run / 'raw.jsonl'
    done = set()
    if raw.exists():
        for line in raw.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if 'error' not in row['result']:
                done.add(row['key'])
    todo = [t for t in tasks if t['key'] not in done]
    (run / 'args.json').write_text(json.dumps({**vars(args), 'probe_template': PROBE, 'n_tasks': len(tasks),
                                               'dialogues': [{k: d[k] for k in ('job', 'version', 'profile', 'session_id', 'state')}
                                                             for d in dialogues]}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{run} | 전체 {len(tasks)} / 남은 {len(todo)}', flush=True)
    lock = threading.Lock()
    finished = [0]

    def work(t):
        res = call(args.vllm_url, t['payload'], args.timeout)
        row = {k: v for k, v in t.items() if k != 'payload'}
        row['request_messages_tail'] = t['payload']['messages'][2:]  # probe text (full request reconstructable from state)
        row['private_turns_given'] = [c['turn'] for c in json.loads(t['payload']['messages'][1]['content']).get('private_turns', [])]
        row['result'] = res
        with lock:
            with raw.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
            finished[0] += 1
            if finished[0] % 25 == 0 or 'error' in res:
                print(f"{finished[0]}/{len(todo)} {'오류 ' + res['error'][:120] if 'error' in res else ''}", flush=True)

    with cf.ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(work, todo))
    print('완료', run, flush=True)


if __name__ == '__main__':
    main()
