"""One phase-loaded instruction skill; no tools, model calls or state mutations."""
import hashlib
from pathlib import Path

NAME = 'client-note-update'
RELATIVE_PATH = Path('skills') / NAME / 'SKILL.md'
MAX_BYTES = 2 * 1024 * 1024


def path(directory):
    directory = Path(directory)
    candidate = directory / RELATIVE_PATH
    if any(part.is_symlink() for part in (directory, candidate.parent.parent, candidate.parent, candidate)):
        raise ValueError('스킬 원본은 심볼릭 링크일 수 없습니다.')
    if not candidate.is_file() or candidate.stat().st_size > MAX_BYTES:
        raise ValueError('노트 작성 스킬 원본 파일을 확인하세요.')
    return candidate


def _metadata(lines):
    if next(lines, '').strip() != '---':
        raise ValueError('SKILL.md는 name과 description을 가진 frontmatter로 시작해야 합니다.')
    fields = {}
    for index, line in enumerate(lines):
        if line.strip() == '---':
            break
        key, separator, value = line.rstrip('\r\n').partition(':')
        if not separator or key not in ('name', 'description') or key in fields:
            raise ValueError('스킬 frontmatter에는 name과 description을 한 줄씩 작성하세요.')
        fields[key] = value.strip()
        if index >= 15:
            raise ValueError('스킬 frontmatter가 너무 깁니다.')
    else:
        raise ValueError('스킬 frontmatter의 닫는 ---가 없습니다.')
    if fields.get('name') != NAME or not fields.get('description'):
        raise ValueError('스킬 이름은 client-note-update이며 description이 필요합니다.')
    if any(value.startswith(('"', "'", '|', '>')) for value in fields.values()):
        raise ValueError('이 스킬의 name과 description은 따옴표 없는 한 줄 텍스트로 작성하세요.')
    return fields


def validate(content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_BYTES:
        raise ValueError('스킬 원문은 2 MB 이하의 문자열이어야 합니다.')
    lines = iter(content.splitlines(keepends=True))
    metadata = _metadata(lines)
    if not ''.join(lines).strip():
        raise ValueError('스킬 수행 지침이 비어 있습니다.')
    return metadata


def describe(directory):
    """Discovery reads only the header; instructions stay out of speech input."""
    source = path(directory)
    with source.open(encoding='utf-8') as stream:
        metadata = _metadata(iter(stream))
    return {**metadata, 'path': str(source), 'stage': 'full_note_update'}


def snapshot(directory, iteration):
    source = path(directory)
    raw = source.read_bytes()
    content = raw.decode('utf-8')
    return {**validate(content), 'path': str(source),
            'sha256': hashlib.sha256(raw).hexdigest(), 'content': content,
            'stage': 'full_note_update', 'iteration': iteration}


def instructions(skill):
    lines = iter(skill['content'].splitlines(keepends=True))
    _metadata(lines)
    return '# 실행 스킬 · ' + skill['name'] + '\n\n' + ''.join(lines).lstrip('\r\n')


def catalog_text(catalog):
    return '\n\n## 사용 가능한 단계별 스킬\n' + '\n'.join(
        '- ' + item['name'] + ': ' + item['description'] +
        ' 상세 지침은 발화 확정 후 노트 갱신 단계에 런타임이 제공합니다.'
        for item in catalog)
