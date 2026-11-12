"""Plot sliding 5-turn window-centroid similarity for one batch (two conditions).

Uses the embedding cache written by analyze_windows.py (cache_full_<job id>.npz);
no model calls. Top: per-condition curve with a profile-bootstrap 95% band.
Bottom: condition difference with the paired-permutation 95% null band.
"""
import argparse
from datetime import datetime
from pathlib import Path
import uuid

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
W = 5
LABELS = {'Q3inner_memory': '내면 기억 유지', 'inner_memory_no': '과거 내면 미제공'}
COLORS = {'Q3inner_memory': '#2a78d6', 'inner_memory_no': '#eb6834'}   # categorical slots 1, 2
INK, INK2, MUTED, GRID, AXIS, SURFACE = '#0b0b0b', '#52514e', '#898781', '#e1e0d9', '#c3c2b7', '#fcfcfb'


def curve(X, idx=None):
    """X: profiles x turns x d. idx: resampled profile indices (bootstrap) or None."""
    n, T = X.shape[:2]
    idx = np.arange(n) if idx is None else idx
    out = []
    for a in range(T - W + 1):
        C = X[idx, a:a + W].mean(1)
        C /= np.linalg.norm(C, axis=1, keepdims=True)
        S = C @ C.T
        ok = idx[:, None] != idx[None, :]          # drop pairs of the same original profile
        out.append(S[ok].mean())
    return np.array(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--job-id', default='batch_08f865c61ebd4284a5ae')
    ap.add_argument('--title-note', default='상담사 Q3inner_counsel · 선별 기억 최대 3턴')
    ap.add_argument('--bootstrap', type=int, default=2000)
    ap.add_argument('--permutations', type=int, default=2000)
    ap.add_argument('--seed', type=int, default=0)
    args = ap.parse_args()
    z = np.load(HERE / f'cache_full_{args.job_id}.npz', allow_pickle=True)
    X = z['X']                                     # [Q3inner_memory, inner_memory_no] x P x T x d
    versions = ['Q3inner_memory', 'inner_memory_no']
    P, T = X.shape[1], X.shape[2]
    rng = np.random.default_rng(args.seed)
    obs = {v: curve(X[i]) for i, v in enumerate(versions)}
    band = {}
    for i, v in enumerate(versions):
        boots = np.stack([curve(X[i], rng.integers(0, P, P)) for _ in range(args.bootstrap)])
        band[v] = np.quantile(boots, [.025, .975], axis=0)
    diff = obs[versions[0]] - obs[versions[1]]
    null = []
    for _ in range(args.permutations):
        f = rng.random(P) < .5
        A = np.where(f[:, None, None], X[1], X[0])
        B = np.where(f[:, None, None], X[0], X[1])
        null.append(curve(A) - curve(B))
    null95 = np.quantile(np.abs(np.stack(null)), .95, axis=0)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font_path = PROJECT / 'frontend/fonts/NanumGothic-Regular.ttf'
    font_manager.fontManager.addfont(str(font_path))
    plt.rcParams['font.family'] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams['axes.unicode_minus'] = False

    x = np.arange(W, T + 1)                         # window end turn
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 7.6), sharex=True,
                                  gridspec_kw={'height_ratios': [2.3, 1], 'hspace': .22})
    fig.patch.set_facecolor(SURFACE)
    for a in (ax, ax2):
        a.set_facecolor(SURFACE)
        a.grid(axis='y', color=GRID, lw=.8)
        a.set_axisbelow(True)
        for s in ('top', 'right', 'left'):
            a.spines[s].set_visible(False)
        a.spines['bottom'].set_color(AXIS)
        a.tick_params(colors=MUTED, length=0, labelsize=10)
    for v in versions:
        c = COLORS[v]
        ax.fill_between(x, band[v][0], band[v][1], color=c, alpha=.13, lw=0)
        ax.plot(x, obs[v], color=c, lw=2, marker='o', ms=4.5, mec=SURFACE, mew=1.2, label=LABELS[v])
        ax.annotate(f'{LABELS[v]}  {obs[v][-1]:.3f}', (x[-1], obs[v][-1]), xytext=(8, 0),
                    textcoords='offset points', va='center', fontsize=10, color=INK2)
    lo = min(band[v][0].min() for v in versions)
    hi = max(band[v][1].max() for v in versions)
    ax.set_ylim(np.floor(lo * 50) / 50, np.ceil(hi * 50) / 50)
    ax.set_ylabel('서로 다른 프로필 간\n구간 중심 코사인 유사도', color=INK2, fontsize=10.5, labelpad=10)
    ax.legend(loc='upper left', frameon=False, fontsize=10, labelcolor=INK2)
    ax.set_title(f'5턴 구간 중심 유사도 · 프로필 {P}개 · 조건별 20턴\n{args.title_note}',
                 loc='left', fontsize=13, color=INK, pad=12)

    ax2.fill_between(x, -null95, null95, color=MUTED, alpha=.18, lw=0, label='우연 범위 (순열 95%)')
    ax2.axhline(0, color=AXIS, lw=1)
    ax2.plot(x, diff, color=INK2, lw=2, marker='o', ms=4, mec=SURFACE, mew=1.2, label='유지 - 미제공')
    m = float(np.abs(np.r_[diff, null95]).max())
    ax2.set_ylim(-m * 1.15, m * 1.15)
    ax2.set_ylabel('조건 차이', color=INK2, fontsize=10.5, labelpad=10)
    ax2.legend(loc='lower left', bbox_to_anchor=(0, 1.0), frameon=False, fontsize=9.5, labelcolor=INK2, ncol=2, borderaxespad=0)
    ax2.set_xticks(x)
    ax2.set_xticklabels([f'{e - W + 1}–{e}' for e in x], rotation=45, ha='right')
    ax2.set_xlabel('내담자 발화 턴 구간 (5턴 슬라이딩)', color=INK2, fontsize=10.5)
    fig.text(.01, .005, '위: 음영은 프로필 부트스트랩 95% 구간.  아래: 음영 밖으로 나가야 우연 범위를 벗어난 차이.  '
             '공개 발화만 임베딩(Qwen3-Embedding-4B) · 인접 구간은 4턴이 겹쳐 독립이 아님.',
             fontsize=8.5, color=MUTED)
    fig.subplots_adjust(left=.1, right=.83, top=.89, bottom=.16)
    out = HERE / 'results' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_sliding_' + uuid.uuid4().hex[:6])
    out.mkdir(parents=True, exist_ok=False)
    fig.savefig(out / f'sliding_window_{args.job_id}.png', dpi=180, facecolor=SURFACE)
    rows = ['window,' + ','.join(f'{v},{v}_lo,{v}_hi' for v in versions) + ',diff,null95']
    for k, e in enumerate(x):
        rows.append(f'{e - W + 1}-{e},' + ','.join(f'{obs[v][k]:.4f},{band[v][0][k]:.4f},{band[v][1][k]:.4f}' for v in versions)
                    + f',{diff[k]:.4f},{null95[k]:.4f}')
    (out / 'sliding_window.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8-sig')
    print(out / f'sliding_window_{args.job_id}.png')


if __name__ == '__main__':
    main()
