"""Validate a per-request vLLM endpoint without changing server defaults."""
from urllib.parse import urlsplit


def normalize_url(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('vLLM 주소를 입력하세요.')
    value = value.strip()
    if '://' not in value:
        value = 'http://' + value
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise ValueError('vLLM 주소와 포트를 확인하세요.') from error
    if (parts.scheme not in ('http', 'https') or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or any(c.isspace() for c in value)
            or parts.path.rstrip('/') not in ('', '/v1') or port == 0):
        raise ValueError('vLLM 주소는 http(s)://호스트:포트 또는 /v1 형식으로 입력하세요.')
    return f'{parts.scheme}://{parts.netloc}/v1'


def apply(config, body):
    if 'vllm_url' in body:
        config['vllm_url'] = normalize_url(body['vllm_url'])
