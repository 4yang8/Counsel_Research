"""Turn-sized memories and lazy Qwen embedding retrieval (no cross-session index)."""
import copy
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen

MODEL_PATH = '/home2/sjyang114/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-4B'
QUERY_INSTRUCTION = 'Retrieve past counseling client turns whose private thoughts and spoken utterances help interpret the current counseling exchange.'
LOCK = threading.RLock()
_MODELS = {}
_CACHE = {}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def chunk(record):
    return {key: copy.deepcopy(record[key]) for key in ('turn', 'inner', 'utterance')}


def chunk_text(record):
    return encode(chunk(record))


def model_directory(value):
    path = Path(value).expanduser()
    if (path / 'config.json').is_file():
        return path
    ref = path / 'refs/main'
    if ref.is_file():
        candidate = path / 'snapshots' / ref.read_text().strip()
        if (candidate / 'config.json').is_file():
            return candidate
    snapshots = sorted((path / 'snapshots').glob('*/config.json'))
    if len(snapshots) == 1:
        return snapshots[0].parent
    raise ValueError('임베딩 모델의 config.json 또는 하나의 확정 snapshot 경로가 필요합니다: ' + str(path))


def unit(vector):
    if not isinstance(vector, list) or not vector or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
        raise ValueError('임베딩 응답에 유효한 숫자 벡터가 없습니다.')
    norm = math.sqrt(sum(x*x for x in vector))
    if norm == 0:
        raise ValueError('영벡터 임베딩은 사용할 수 없습니다.')
    return [x/norm for x in vector]


def embed(texts, options):
    """Same explicit query prefix for local and OpenAI-compatible backends."""
    if options['embedding_backend'] == 'openai':
        from runtime.connection_settings import normalize_url
        endpoint = normalize_url(options['embedding_url']) + '/embeddings'
        req = Request(endpoint, data=encode({'model': options['embedding_model'], 'input': texts,
                                             'encoding_format': 'float'}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(req, timeout=options['embedding_timeout']) as response:
            data = json.load(response)
        rows = sorted(data.get('data', []), key=lambda row: row['index'])
        if [r.get('index') for r in rows] != list(range(len(texts))):
            raise ValueError('임베딩 서버 응답의 개수·index가 요청과 다릅니다.')
        return [unit(row['embedding']) for row in rows]
    path = model_directory(options['embedding_path'])
    key = (str(path), options['embedding_device'])
    with LOCK:
        if key not in _MODELS:
            try:
                from sentence_transformers import SentenceTransformer
                import torch
            except ImportError as error:
                raise RuntimeError('로컬 임베딩에는 sentence-transformers와 torch가 필요합니다. 또는 임베딩 서버를 선택하세요.') from error
            device = options['embedding_device']
            if device == 'auto':
                device = 'cuda' if torch.cuda.is_available() else 'cpu'
            _MODELS[key] = SentenceTransformer(str(path), device=device, local_files_only=True,
                tokenizer_kwargs={'padding_side': 'left'},
                model_kwargs={'torch_dtype': torch.float16 if device.startswith('cuda') else torch.float32})
        model = _MODELS[key]
        # Never silently truncate a turn-sized memory or search query.
        lengths = [len(model.tokenizer(t, truncation=False)['input_ids']) for t in texts]
        if any(n > model.max_seq_length for n in lengths):
            raise ValueError('임베딩 입력이 모델 길이 한도를 넘었습니다. 턴 기록을 자동 절삭하지 않았습니다.')
        return [unit(v) for v in model.encode(texts, prompt='', batch_size=2,
                    normalize_embeddings=True, show_progress_bar=False).tolist()]


def retrieve(records, query, options, cancelled=lambda: False):
    started = time.monotonic()
    if not records:
        return {'query': query, 'results': [], 'scores': [], 'status': 'no_records', 'latency_ms': 0,
                'embedded_documents': 0, 'cache_hits': 0}
    from runtime.cancellation import Cancelled
    if cancelled():
        raise Cancelled('검색 전에 중단되었습니다.')
    texts = [chunk_text(r) for r in records]
    identity = {k: options[k] for k in ('embedding_backend', 'embedding_path', 'embedding_model',
                                       'embedding_url', 'embedding_device')}
    if options['embedding_backend'] == 'local':
        identity['resolved_path'] = str(model_directory(options['embedding_path']))
    keys = [digest([identity, text]) for text in texts]
    with LOCK:
        cached = {key: _CACHE[key] for key in keys if key in _CACHE}
    missing = list(dict.fromkeys(key for key in keys if key not in cached))
    source = dict(zip(keys, texts))
    if missing:
        vectors = embed([source[key] for key in missing], options)
        cached.update(zip(missing, vectors))
        with LOCK:
            if len(_CACHE) + len(missing) > 8192:
                _CACHE.clear()
            _CACHE.update(zip(missing, vectors))
    query_text = 'Instruct: ' + QUERY_INSTRUCTION + '\nQuery: ' + query
    q = embed([query_text], options)[0]
    if cancelled():
        raise Cancelled('검색 결과 주입 전에 중단되었습니다.')
    scores = []
    for row, key in zip(records, keys):
        vector = cached[key]
        if len(vector) != len(q):
            raise ValueError('문서와 검색어의 임베딩 차원이 다릅니다.')
        scores.append((sum(a*b for a,b in zip(q, vector)), row))
    scores.sort(key=lambda item: (-item[0], item[1]['turn']))
    chosen = scores[:options['top_k']]
    return {'query': query, 'results': [chunk(r) for _,r in chosen],
            'scores': [{'turn': r['turn'], 'similarity': score} for score,r in chosen],
            'status': 'retrieved', 'latency_ms': round((time.monotonic()-started)*1000, 2),
            'embedded_documents': len(missing), 'cache_hits': len(keys)-len(missing),
            'query_instruction': QUERY_INSTRUCTION, 'embedding': identity,
            'similarity_threshold': None}
