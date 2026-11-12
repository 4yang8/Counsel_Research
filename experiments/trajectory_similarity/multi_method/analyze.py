"""Exploratory trajectory comparisons; fixed metrics, paired profile permutations.

Primary representation is verbatim client utterance embeddings. No model generation,
dimensionality reduction, profile/counselor embedding, or favorable-case selection.
"""
import argparse
import csv
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import sys

import numpy as np

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plot_similarity import embed, PROJECT


def norm(x):
    return x / np.linalg.norm(x, axis=-1, keepdims=True).clip(1e-12)


def dtw_similarity(cost, band):
    """Symmetric weighted DTW: diagonal weight 2, axial weight 1.

    Fixed endpoints and monotone path. Total weight is 2*T, independent of path;
    this minimizes the reported weighted mean cosine distance, not path length.
    """
    t = len(cost)
    dp = np.full((t+1, t+1), np.inf)
    dp[0, 0] = 0
    for i in range(1, t+1):
        for j in range(max(1, i-band), min(t, i+band)+1):
            c = cost[i-1, j-1]
            dp[i, j] = min(dp[i-1, j-1]+2*c, dp[i-1, j]+c, dp[i, j-1]+c)
    return 1-float(dp[t, t])/(2*t)


def metrics(x):
    """All pair scores, including cross-condition pairs needed for permutations."""
    size, turns, dim = x.shape
    assert turns == 20, 'This pilot fixes early/late windows at 1–5 and 16–20.'
    same = np.einsum('itd,jtd->ijt', x, x)
    pooled = {}
    cross = {}
    for start in (0, 5, 10, 15):
        avg = x[:, start:start+5].mean(axis=1)
        pooled[start] = norm(avg) @ norm(avg).T
        cross[start] = avg @ avg.T
    out = {
        'same_all': same.mean(axis=-1),
        'same_late': same[:, :, 15:].mean(axis=-1),
        'same_growth': same[:, :, 15:].mean(axis=-1)-same[:, :, :5].mean(axis=-1),
        'pooled_late': pooled[15],
        'pooled_growth': pooled[15]-pooled[0],
        'cross_late': cross[15],
        'cross_growth': cross[15]-cross[0],
    }
    for key in ('dtw_band2', 'dtw_band5', 'dtw_free', 'dtw_late', 'dtw_growth'):
        out[key] = np.zeros((size, size))
    for i in range(size):
        for j in range(i+1, size):
            cost = 1-np.clip(x[i] @ x[j].T, -1, 1)
            early = dtw_similarity(cost[:10, :10], 5)
            late = dtw_similarity(cost[10:, 10:], 5)
            vals = {'dtw_band2':dtw_similarity(cost, 2),
                    'dtw_band5':dtw_similarity(cost, 5),
                    'dtw_free':dtw_similarity(cost, turns),
                    'dtw_late':late, 'dtw_growth':late-early}
            for key, value in vals.items():
                out[key][i, j] = out[key][j, i] = value
    return out, same, pooled, cross


def group_mean(matrix, ids):
    return float(matrix[np.ix_(ids, ids)][np.triu_indices(len(ids), 1)].mean())


def summarize(matrices, n):
    # Whole trajectories swap within each profile. Pair similarities are dependent.
    flips = np.array(list(itertools.product((0, 1), repeat=n)))
    base = np.arange(n)
    result = []
    for key, mat in matrices.items():
        av, bv = group_mean(mat, base), group_mean(mat, base+n)
        delta = bv-av
        null = []
        for flip in flips:
            null.append(group_mean(mat, base+(1-flip)*n)-group_mean(mat, base+flip*n))
        p = float(np.mean(np.abs(null) >= abs(delta)-1e-12))
        leave_one_out = []
        for omit in range(n):
            keep = base[base != omit]
            leave_one_out.append(group_mean(mat, keep+n)-group_mean(mat, keep))
        result.append(dict(metric=key, memory=av, no_memory=bv, delta_no_minus_memory=delta,
                           p_exact_two_sided=p, leave_one_out_min=min(leave_one_out),
                           leave_one_out_max=max(leave_one_out)))
    running = 0
    for rank, row in enumerate(sorted(result, key=lambda r:r['p_exact_two_sided'])):
        running = max(running, min(1, (len(result)-rank)*row['p_exact_two_sided']))
        row['p_holm'] = running
    return result


def plot(same, pooled, cross, n, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font = FontProperties(fname=str(PROJECT/'frontend/fonts/NanumGothic-Regular.ttf'))
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for k, label in enumerate(('내면 유지', '과거 내면 미제공')):
        ids = np.arange(n)+k*n
        curve = [group_mean(same[:, :, t], ids) for t in range(20)]
        axes[0].plot(range(1,21), curve, marker='.', label=label)
        axes[1].plot([3,8,13,18], [group_mean(pooled[s],ids) for s in (0,5,10,15)], marker='o',label=label)
        axes[2].plot([3,8,13,18], [group_mean(cross[s],ids) for s in (0,5,10,15)], marker='o',label=label)
    for ax,title in zip(axes,('동일 턴 비교','5턴 평균 벡터 비교','5턴 내 모든 발화 교차 비교')):
        ax.set_title(title,fontproperties=font)
        ax.set_xlabel('내담자 턴 / 구간 중심',fontproperties=font)
        ax.set_ylabel('평균 코사인 유사도',fontproperties=font)
        ax.set_ylim(0,1)
        ax.grid(alpha=.25)
        ax.legend(prop=font)
    fig.tight_layout()
    fig.savefig(output/'window_comparison.png',dpi=170)
    plt.close(fig)


LABELS = {
    'same_all':'동일 턴 전체 평균', 'same_late':'동일 턴 후반 5턴',
    'same_growth':'동일 턴 증가량 (후반5−초반5)',
    'pooled_late':'5턴 평균벡터 후반 유사도',
    'pooled_growth':'5턴 평균벡터 증가량 (후반5−초반5)',
    'cross_late':'후반5턴 전체 교차 유사도',
    'cross_growth':'전체 교차 증가량 (후반5−초반5)',
    'dtw_band2':'전체 DTW ±2턴', 'dtw_band5':'전체 DTW ±5턴',
    'dtw_free':'전체 DTW 제한 없음', 'dtw_late':'후반10턴 DTW ±5턴',
    'dtw_growth':'DTW 증가량 (후반10−초반10, ±5턴)',
}


def report(rows, clean_rows, metadata, out):
    by_key = {r['metric']:r for r in rows}
    significant = [LABELS[r['metric']] for r in rows if r['p_holm'] < .05]
    lines = ['# 시간차를 고려한 궤적 임베딩 탐색 분석', '',
             f"대상: `{metadata['inputs'][0]}`. 동일 프로필 10개 × 두 조건 × 20턴. 조건별 45쌍.", '',
             '발화만 Qwen3-Embedding-4B로 임베딩하고 원 차원을 유지했다. 내면·상담사·프로필 텍스트는 입력하지 않았다.', '',
             '## 결과 해석', '',
             ('Holm 보정 후 p<0.05인 지표: '+', '.join(significant)) if significant else '12개 지표 모두 조건 간 차이가 Holm 보정 후 p<0.05에 도달하지 않았다.',
             f"±5턴 DTW 조건차는 {by_key['dtw_band5']['delta_no_minus_memory']:+.4f}, 제한 없는 DTW는 {by_key['dtw_free']['delta_no_minus_memory']:+.4f}다. 이번 표본에서는 시간차를 허용해도 큰 조건 차이가 새롭게 나타나지 않았다.",
             f"동일 턴의 후반−초반 증가량은 유지 {by_key['same_growth']['memory']:+.4f}, 미제공 {by_key['same_growth']['no_memory']:+.4f}다. 양쪽 모두 소폭 증가하지만 미제공이 더 빠르게 수렴한다는 근거는 아니다.",
             '후반 평균벡터 비교에서는 오히려 유지 쪽 유사도가 더 높다. 방법에 따라 방향이 달라지므로 한 방식의 작은 차이를 심리적 다양성 보존의 증거로 고르면 안 된다.',
             '이 결과는 동일 턴 분석의 시간차 한계를 지적한 가설이 틀렸다는 뜻이 아니다. 이번 10개 프로필·한 번의 생성에서 그 한계를 보완했어도 기대한 조건 차이는 포착되지 않았다는 뜻이다.', '',
             '## 전체 수치', '',
             '양의 조건차(미제공−유지)는 미제공 쪽의 유사도 또는 증가량이 더 크다는 뜻이다. 절대 유사도와 증가량은 다른 질문이다.', '',
             '| 분석 | 유지 | 미제공 | 조건차 | 양측 p | Holm p | 프로필 하나 제외 시 조건차 범위 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {LABELS[r['metric']]} | {r['memory']:.4f} | {r['no_memory']:.4f} | {r['delta_no_minus_memory']:+.4f} | {r['p_exact_two_sided']:.3f} | {r['p_holm']:.3f} | {r['leave_one_out_min']:+.4f} ~ {r['leave_one_out_max']:+.4f} |")
    lines += ['', '## 방법과 한계', '',
              '- 동일 턴: 같은 순번 발화의 코사인 유사도를 프로필 쌍에 걸쳐 평균.',
              '- 5턴 평균벡터: 연속 5개 단위벡터를 평균한 뒤 재정규화하여 비교. 턴 순서는 소실되며 여러 발화를 묶는 평활화 자체가 유사도를 높일 수 있다.',
              '- 전체 교차: 두 내담자의 같은 5턴 구간에 있는 5×5=25개 발화 조합의 코사인 평균. 순서는 무시하지만 가장 비슷한 조합만 고르지 않는다.',
              '- DTW: 시작·끝을 고정하고 순서를 보존하며 코사인 거리 합을 최소화. 대각선 이동 가중치 2, 수평·수직 이동 1로 총 가중치 2T가 일정하다. ±2/±5턴 및 제한 없는 정렬을 함께 공개. 정렬 자유도가 높아지면 우연한 유사성도 커진다. 시간차 정렬이 실제 상담 단계의 일치를 보장하지 않는다.',
              '- 증가량은 후반 유사도−초반 유사도. DTW는 1–10/11–20턴, 나머지는 1–5/16–20턴. 끝 구간을 실제 심리적 도달점으로 간주하지 않는다.',
              '- 프로필별 두 조건의 전체 대화를 교환하는 2^10=1024개 정확 순열검정. 45쌍/20턴을 독립 표본으로 취급하지 않는다. 조건 내 대화 교환가능성을 가정한 탐색적 검정이며 프로필당 1회 생성이므로 재생성 변동을 추정하지 못한다.',
              '- 양측 검정 12개에 Holm 보정. 사전 등록된 확증 실험이 아니다. 유의하지 않음은 효과 없음이나 두 조건의 동등성 입증이 아니다.',
              '- 프로필 하나씩 제외한 범위는 영향도 점검이며 신뢰구간이 아니다. 주제·문체의 유사성을 심리적 동일화나 프로필 충실도로 해석할 수 없다.',
              '', '## 종료 태그 제거 민감도 점검', '',
              '기본 분석은 원문 그대로다. 추가 분석에서는 XML 형태 태그만 제거하고 해당 발화만 재임베딩했다. 외국어 혼입은 번역하거나 삭제하지 않았다. 아래는 조건차의 변화이며, 유리한 결과를 골라 주 분석을 대체하지 않는다.', '',
              '| 분석 | 원문 조건차 | 태그 제거 조건차 |','|---|---:|---:|']
    for r,c in zip(rows,clean_rows):
        lines.append(f"| {LABELS[r['metric']]} | {r['delta_no_minus_memory']:+.4f} | {c['delta_no_minus_memory']:+.4f} |")
    lines += ['', '## 산출물', '',
              '- `embeddings.npz`: 원문 및 태그 제거 임베딩. 축은 조건, 프로필, 턴, 차원.',
              '- `input_snapshot.json`: 입력 발화·원본 경로·프로필 순서·임베딩 설정.',
              '- `scores.csv`, `scores_clean.csv`: 모든 지표와 검정 결과.',
              '- `pair_scores.npz`: 순열검정에 사용한 모든 대화 쌍의 지표 행렬.',
              '- `window_comparison.png`, `alignment_comparison.png`: 동일 시간/구간 및 DTW 비교.', '']
    checks_path = out/'checks.json'
    if checks_path.exists():
        checks=json.loads(checks_path.read_text())
        lines += ['## 연산 점검', '',
                  f"- 같은 순서의 상태가 서로 다른 속도로 진행하는 인공 단위벡터 대조군: 동일 턴 {checks['time_stretch_control_same']:.3f}, DTW {checks['time_stretch_control_dtw']:.3f}. 시간차를 포착하는 연산임을 확인했다. 실제 언어 임베딩의 심리적 타당성 검증은 아니다.",
                  '- 작은 행렬의 모든 정렬 경로를 열거한 독립 계산과 DTW 결과 일치. 허용 시간차 확대에 따른 최적 유사도 비감소 확인.',
                  f"- 기존 그래프와 재임베딩 동일 턴 수치의 최대 절대 차이: {checks['max_reference_same_turn_difference']:.6f}. 입력 스냅샷은 동일하며, 재호출 수치 차이가 있어 이번 분석 전체에는 동일한 저장 임베딩을 사용했다.", '']
    (out/'REPORT.md').write_text('\n'.join(lines))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--analysis',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    meta=json.loads(args.analysis.read_text())
    versions=['Q3inner_memory','inner_memory_no']
    assert meta['versions']==versions and meta['max_turns']==20 and len(meta['profile_ids'])==10
    ids=meta['profile_ids'];n=len(ids)
    texts=[s for v in versions for p in ids for s in meta['dialogues'][v][p]['utterances']]
    key=hashlib.sha256(json.dumps([texts,meta['embedding_model'],meta['embedding_url']],ensure_ascii=False).encode()).hexdigest()
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    cache=out/'embeddings.npz'
    print('단계 1/3: 400개 발화 임베딩 및 캐시 확인',flush=True)
    if cache.exists():
        cached=np.load(cache)
        assert str(cached['key'])==key, 'Cached input/model mismatch'
        raw=cached['raw'];clean=cached['clean']
    else:
        raw=embed(texts,meta['embedding_url'],meta['embedding_model'],8,180)
        clean=raw.copy()
        changed=[(i,re.sub(r'</?\w+[^>]*>','',s).strip()) for i,s in enumerate(texts) if re.search(r'</?\w+[^>]*>',s)]
        if changed:
            vectors=embed([s for i,s in changed],meta['embedding_url'],meta['embedding_model'],8,180)
            for (i,s),v in zip(changed,vectors):clean[i]=v
        raw=raw.reshape(2,n,20,-1);clean=clean.reshape(raw.shape)
        np.savez_compressed(cache,raw=raw,clean=clean,key=key)
    (out/'input_snapshot.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    print('단계 2/3: 12개 고정 지표 · 1024개 프로필 단위 순열 · 태그 민감도',flush=True)
    mat,same,pooled,cross=metrics(raw.reshape(2*n,20,-1))
    rows=summarize(mat,n)
    clean_mat,_,_,_=metrics(clean.reshape(2*n,20,-1))
    clean_rows=summarize(clean_mat,n)
    np.savez_compressed(out/'pair_scores.npz',**mat)
    for filename,data in [('scores.csv',rows),('scores_clean.csv',clean_rows)]:
        with (out/filename).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    print('단계 3/3: 그래프·보고서 저장',flush=True)
    plot(same,pooled,cross,n,out)
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font=FontProperties(fname=str(PROJECT/'frontend/fonts/NanumGothic-Regular.ttf'))
    subset=[r for r in rows if r['metric'] in ('same_all','dtw_band2','dtw_band5','dtw_free')]
    fig,ax=plt.subplots(figsize=(8,4.5));loc=np.arange(len(subset))
    ax.bar(loc-.18,[r['memory'] for r in subset],.36,label='내면 유지')
    ax.bar(loc+.18,[r['no_memory'] for r in subset],.36,label='과거 내면 미제공')
    ax.set_xticks(loc,[LABELS[r['metric']] for r in subset],fontproperties=font)
    ax.set_ylim(0,1);ax.set_ylabel('평균 유사도',fontproperties=font);ax.legend(prop=font)
    ax.set_title('동일 턴과 시간차 정렬 비교 · 정렬은 심리 단계 일치를 보장하지 않음',fontproperties=font)
    fig.tight_layout();fig.savefig(out/'alignment_comparison.png',dpi=170);plt.close(fig)
    report(rows,clean_rows,meta,out)
    print(json.dumps(rows,ensure_ascii=False,indent=2),flush=True)
    print('완료:',out,flush=True)


if __name__=='__main__':
    # Mechanism checks: identical paths, constant series, and zero-band identity.
    a=np.eye(4)
    assert abs(dtw_similarity(1-a@a.T,0)-1)<1e-12
    assert abs(dtw_similarity(np.full((4,4),.3),4)-.7)<1e-12
    main()
