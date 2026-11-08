"""Original-dimensional, within-turn mean cosine similarity across client profiles.

Only generated public client utterances are embedded, with identical preprocessing
in both conditions. Missing turns/profiles and duplicate profile-condition runs
are errors: the cohort cannot silently change across turns or conditions.
"""
import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen
import uuid

import numpy as np

PROJECT = Path(__file__).resolve().parents[2]


def read_json(path):
    with path.open(encoding='utf-8') as source:
        return json.load(source)


def session_paths(inputs, versions):
    found = set()
    for value in inputs:
        source = Path(value).expanduser().resolve()
        if not source.exists():
            raise ValueError(f'입력 경로가 없습니다: {source}')
        if source.is_dir() and (source / 'job.json').is_file():
            source = source / 'job.json'
        if source.is_file() and source.name == 'job.json':
            job = read_json(source)
            # job directory: <storage>/profile_batches/<id>/job.json
            storage = source.parents[2]
            for row in job['rows']:
                if row.get('version') not in versions:
                    continue
                sid = row.get('session_id')
                if not sid:
                    raise ValueError(f"아직 생성하지 않은 대화: {row.get('profile_id')} / {row.get('version')}. 생성을 완료한 뒤 실행하세요.")
                path = storage / 'paired' / sid / 'session.json'
                if not path.is_file():
                    raise ValueError(f'대화 파일이 없습니다: {path}. 이동한 대화는 session.json 경로로 직접 지정하세요.')
                found.add(path.resolve())
        elif source.is_file() and source.name == 'session.json':
            found.add(source)
        elif source.is_dir():
            # Do not recursively load old checkpoint copies or attempt snapshots.
            direct = source / 'session.json'
            candidates = [direct] if direct.is_file() else list(source.glob('*/session.json'))
            if not candidates:
                raise ValueError(f'session.json 또는 job.json이 없습니다: {source}')
            found.update(p.resolve() for p in candidates)
        else:
            raise ValueError(f'job.json / session.json / 해당 폴더를 지정하세요: {source}')
    return sorted(found)


def load_dialogues(inputs, versions, max_turns):
    groups = {v: {} for v in versions}
    ignored = 0
    for path in session_paths(inputs, versions):
        session = read_json(path)
        version = session.get('client_version')
        if version not in groups:
            ignored += 1
            continue
        profile = session['settings']['scenario']['profile']
        pid = profile.get('profile_id')
        if not isinstance(pid, str) or not pid:
            raise ValueError(f'profile_id가 없습니다: {path}')
        if pid in groups[version]:
            raise ValueError(f'중복 대화: {version} / {pid}. 반복 실행은 별도로 분석하세요.')
        client_steps = [s for s in session['steps'] if s['actor'] == 'client']
        if len(client_steps) < max_turns:
            raise ValueError(f'{version} / {pid}: 현재 {len(client_steps)}턴, 분석에 {max_turns}턴 필요. 생성을 완료하거나 MAX_TURNS를 줄이세요.')
        selected = client_steps[:max_turns]
        if any(s.get('source') == 'manual' for s in selected):
            raise ValueError(f'{version} / {pid}: 분석 범위에 수동 입력한 내담자 발화가 있습니다.')
        utterances = [s['utterance'] for s in selected]
        if any(not isinstance(t, str) or not t.strip() for t in utterances):
            raise ValueError(f'{version} / {pid}: 비어 있거나 잘못된 내담자 발화입니다.')
        profile_hash = hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        groups[version][pid] = {'path': str(path), 'session_id': session['session_id'],
                               'profile_hash': profile_hash, 'utterances': utterances}
    ids = sorted(groups[versions[0]])
    if len(ids) < 2:
        raise ValueError('각 조건에 서로 다른 프로필의 대화가 최소 2개 필요합니다.')
    for version in versions:
        if set(groups[version]) != set(ids):
            missing = sorted(set(ids) - set(groups[version]))
            extra = sorted(set(groups[version]) - set(ids))
            raise ValueError(f'{version}: 비교 프로필 집합이 다릅니다. 누락={missing}, 추가={extra}')
        for pid in ids:
            if groups[version][pid]['profile_hash'] != groups[versions[0]][pid]['profile_hash']:
                raise ValueError(f'{pid}: 조건 간 초기 프로필 내용이 다릅니다.')
    return groups, ids, ignored


def embed(texts, url, model, batch_size, timeout):
    base = url.rstrip('/')
    endpoint = base if base.endswith('/embeddings') else base + '/embeddings' if base.endswith('/v1') else base + '/v1/embeddings'
    vectors = []
    dimension = None
    for offset in range(0, len(texts), batch_size):
        batch = texts[offset:offset + batch_size]
        request = Request(endpoint, data=json.dumps({'model': model, 'input': batch,
                         'encoding_format': 'float'}, ensure_ascii=False).encode(),
                         headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except Exception as error:
            raise RuntimeError(f'임베딩 서버 요청 실패 ({endpoint}): {error}') from error
        rows = sorted(payload.get('data', []), key=lambda r: r['index'])
        if [r['index'] for r in rows] != list(range(len(batch))):
            raise ValueError('임베딩 응답 개수 또는 index가 요청과 다릅니다.')
        for row in rows:
            vector = np.asarray(row['embedding'], dtype=np.float64)
            if vector.ndim != 1 or not vector.size or not np.isfinite(vector).all():
                raise ValueError('유효하지 않은 임베딩 벡터입니다.')
            dimension = dimension or vector.size
            if vector.size != dimension:
                raise ValueError('임베딩 차원이 호출마다 다릅니다.')
            norm = np.linalg.norm(vector)
            if not np.isfinite(norm) or norm <= 0:
                raise ValueError('영벡터 또는 정규화할 수 없는 임베딩입니다.')
            vectors.append(vector / norm)
        completed = len(vectors)
        print(f'임베딩 {completed}/{len(texts)} ({completed / len(texts):.0%})', flush=True)
    return np.stack(vectors)


def mean_cosine(vectors):
    """One unit vector per client; exclude self-pairs and duplicate pairs."""
    n = len(vectors)
    if n < 2:
        raise ValueError('두 개 이상의 벡터가 필요합니다.')
    similarities = vectors @ vectors.T
    return float(np.clip(similarities[np.triu_indices(n, k=1)], -1, 1).mean())


def plot(rows, versions, output, n_profiles):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font = FontProperties(fname=str(PROJECT / 'frontend/fonts/NanumGothic-Regular.ttf'))
    labels = {'Q3inner_memory': '내면 유지', 'inner_memory_no': '과거 내면 미제공'}
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for version in versions:
        selected = [r for r in rows if r['version'] == version]
        ax.plot([r['turn'] for r in selected], [r['mean_cosine_similarity'] for r in selected],
                marker='o', markersize=4, label=labels.get(version, version))
    ax.set_xlabel('내담자 발화 턴', fontproperties=font)
    ax.set_ylabel('서로 다른 내담자 간 평균 코사인 유사도', fontproperties=font)
    ax.set_title(f'턴별 발화 유사도 · 조건별 동일 프로필 {n_profiles}개', fontproperties=font)
    turns = max(r['turn'] for r in rows)
    ax.set_xticks(sorted(set(range(1, turns + 1, max(1, (turns + 14) // 15))) | {turns}))
    values = [r['mean_cosine_similarity'] for r in rows]
    ax.set_ylim(-1 if min(values) < 0 else 0, 1)
    ax.grid(alpha=.25)
    ax.legend(prop=font)
    fig.text(.5, .015, '차원 축소 없이 계산 · 값이 클수록 발화가 유사함 · 심리적 동질화를 직접 판정하는 지표는 아님',
             ha='center', fontproperties=font, fontsize=9)
    fig.tight_layout(rect=(0, .04, 1, 1))
    fig.savefig(output, dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', nargs='+', required=True)
    parser.add_argument('--versions', nargs='+', default=['Q3inner_memory', 'inner_memory_no'])
    parser.add_argument('--max-turns', type=int, default=20)
    parser.add_argument('--embedding-url', required=True)
    parser.add_argument('--embedding-model', default='Qwen/Qwen3-Embedding-4B')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if min(args.max_turns, args.batch_size, args.timeout) < 1 or len(set(args.versions)) != len(args.versions):
        raise ValueError('턴 수·배치 수·제한 시간은 양수이고 버전은 중복 없이 지정해야 합니다.')
    groups, ids, ignored = load_dialogues(args.inputs, args.versions, args.max_turns)
    pairs = len(ids) * (len(ids) - 1) // 2
    print(f'{len(ids)}개 프로필 × {len(args.versions)}개 조건 × {args.max_turns}턴 / 턴당 {pairs}쌍 비교', flush=True)
    if ignored:
        print(f'선택하지 않은 버전의 대화 {ignored}개 제외', flush=True)
    # All conditions use the same utterance-only representation; no retrieval instructions.
    texts = [text for version in args.versions for pid in ids for text in groups[version][pid]['utterances']]
    vectors = embed(texts, args.embedding_url, args.embedding_model, args.batch_size, args.timeout)
    shape = (len(args.versions), len(ids), args.max_turns, vectors.shape[1])
    vectors = vectors.reshape(shape)
    rows = [{'version': version, 'turn': turn + 1, 'n_profiles': len(ids), 'n_pairs': pairs,
             'mean_cosine_similarity': mean_cosine(vectors[vi, :, turn, :])}
            for vi, version in enumerate(args.versions) for turn in range(args.max_turns)]
    output = args.output_dir / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'mean_cosine.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    plot(rows, args.versions, output / 'mean_cosine.png', len(ids))
    metadata = {'inputs': args.inputs, 'versions': args.versions, 'max_turns': args.max_turns,
        'profile_ids': ids, 'embedding_url': args.embedding_url, 'embedding_model': args.embedding_model,
        'embedding_dimensions': vectors.shape[-1], 'representation': 'generated client utterance only; no added instruction; L2 normalized',
        'metric': 'mean of cosines for all distinct unordered profile pairs at the same turn',
        'ignored_other_versions': ignored, 'dialogues': groups}
    (output / 'analysis.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'완료: {output / "mean_cosine.png"}\n수치: {output / "mean_cosine.csv"}\n분석 입력·설정: {output / "analysis.json"}', flush=True)
    return output


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
        print('분석 중단: ' + str(error), file=sys.stderr)
        sys.exit(1)
