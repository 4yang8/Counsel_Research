"""Read and edit the actual sources of the lab's prompt editors."""
import json
import runtime.user_templates as user_templates
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import uuid

from storage.file_manager import LIMIT, digest
from counselor.catalog import PRACTICE_VERSIONS

from project_paths import PROJECT_ROOT
HERE = PROJECT_ROOT


def source(server, role, version, kind):
    if kind == 'user' or isinstance(kind, str) and kind.endswith('_user'):
        return HERE / user_templates.descriptor(role, version, kind)['path'], None
    if role == 'client':
        if version == 'Q3sb' and kind == 'note_summarizer':
            return HERE / 'src/client/Q3sb/note_summarizer.md', None
        if version == 'Q3sk' and kind == 'skill_notes':
            from client.Q3sk.skills_runtime import path
            return path(HERE / 'src/client/Q3sk'), None
        if version in ('Q3','Q3a','Q3b','Q3sk','Q3n','Q3sb','Q3x') and kind == 'trajectory':
            return HERE / 'src/client' / version / 'trajectory.md', None
        if version == 'basic' and kind == 'system':
            return Path(server.config_path), 'system_prompt'
        if version == 'state_summary' and kind in ('system', 'summary'):
            return HERE / 'src/client/state_summary' / (kind + '.md'), None
        if version in ('Q2a','Q2a_variant_context','Q2a_trait_background','Q2a_prefill_test','Q2a_note_select','Q2a_note_retrieve','Q2a_turn_goal','Q2a_agentic_memory','Q2a_agentic_memory_F','Q3','Q3a','Q3b','Q3sk','Q3n','Q3sb','Q3x') and kind in ('system', 'trait_router'):
            return HERE / 'src/client' / version / (kind + '.md'), None
        if version == 'Q2' and kind in ('system', 'trait_router', 'state_router', 'state_updater'):
            return HERE / 'src/client/Q2' / (kind + '.md'), None
        if version == 'Q1a' and kind in ('system', 'trait_router', 'draft_reviewer'):
            return HERE / 'src/client/Q1a' / (kind + '.md'), None
        if version in ('Q1', 'Q1_s1', 'Q1_s2') and kind in ('system', 'trait_router'):
            return HERE / 'src/client' / version / (kind + '.md'), None
    elif role == 'counselor':
        if kind == 'system':
            if version == 'basic':
                return HERE / 'resources/prompts/basic_counselor.md', None
            if version in PRACTICE_VERSIONS:
                return HERE / 'src/counselor' / version / 'system.md', None
        package = PRACTICE_VERSIONS.get(version)
        descriptor = getattr(package.runtime, 'SUBAGENT_PROMPTS', {}).get(kind) if package else None
        if descriptor:
            return HERE / 'src/counselor' / version / descriptor['filename'], None
    raise ValueError('편집할 프롬프트의 역할·버전·종류를 확인하세요.')


def read(server, role, version, kind):
    path, field = source(server, role, version, kind)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError('프롬프트 원본 파일을 확인하세요.')
    raw = path.read_bytes()
    content = json.loads(raw).get(field, '') if field else raw.decode('utf-8')
    if not isinstance(content, str):
        raise ValueError('프롬프트 원문은 문자열이어야 합니다.')
    return {'role': role, 'version': version, 'kind': kind, 'path': str(path),
            'field': field, 'content': content, 'revision': digest(raw),
            'variables': user_templates.REGISTRY.get((role, version, kind), {}).get('variables', {})}


def handle_get(handler):
    parts = urlsplit(handler.path)
    if parts.path != '/api/prompts/source':
        return False
    params = parse_qs(parts.query)
    try:
        with handler.server.storage_lock:
            result = read(handler.server, *(params.get(key, [''])[0] for key in ('role', 'version', 'kind')))
        handler.json_response(result)
    except (ValueError, OSError, UnicodeError, TypeError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True


def handle_post(handler):
    if handler.path != '/api/prompts/save':
        return False
    try:
        if handler.headers.get('X-Gemma-Request') != '1':
            raise ValueError('요청 헤더가 없습니다.')
        size = int(handler.headers.get('Content-Length', 0))
        if not 0 < size <= LIMIT * 2:
            raise ValueError('요청 크기를 확인하세요.')
        body = json.loads(handler.rfile.read(size))
        content = body.get('content')
        if not isinstance(content, str) or len(content.encode('utf-8')) > LIMIT:
            raise ValueError('프롬프트는 2 MB 이하의 문자열이어야 합니다.')
        with handler.server.storage_lock:
            if handler.server.storage_jobs:
                handler.json_response({'error': '생성이 끝난 뒤 프롬프트를 저장하세요.'}, 409)
                return True
            keys = [body.get(key) for key in ('role', 'version', 'kind')]
            current = read(handler.server, *keys)
            if body.get('revision') != current['revision']:
                handler.json_response({'error': '원본이 변경되었습니다. 편집 내용을 복사해 둔 뒤 실행취소로 최신 파일을 불러오세요.'}, 409)
                return True
            if tuple(keys) in user_templates.REGISTRY:
                user_templates.validate(content, user_templates.descriptor(*keys)['variables'])
            if keys == ['client', 'Q3sk', 'skill_notes']:
                from client.Q3sk.skills_runtime import validate
                validate(content)
            path, field = source(handler.server, *keys)
            if field:
                data = json.loads(path.read_text(encoding='utf-8'))
                data[field] = content
                content = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
            temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_text(content, encoding='utf-8')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            result = {**read(handler.server, *keys), 'saved': True}
        handler.json_response(result)
    except (ValueError, OSError, UnicodeError, TypeError, AttributeError) as error:
        handler.json_response({'error': str(error)}, 400)
    return True
