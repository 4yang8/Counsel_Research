#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$PROJECT_ROOT"

if [ ! -e "$PROJECT_ROOT/.git" ]; then
    echo "이 프로젝트의 Git 저장소가 아직 초기화되지 않았습니다." >&2
    echo "프로젝트 루트에서 Git 초기화와 origin 연결을 먼저 진행하세요." >&2
    exit 1
fi

if ! branch="$(git symbolic-ref --quiet --short HEAD)"; then
    echo "현재 브랜치가 없습니다. 브랜치를 선택한 뒤 다시 실행하세요." >&2
    exit 1
fi

echo "=== 현재 Git 상태 ==="
git status
echo

if [ -n "$(git status --porcelain)" ]; then
    read -r -p "커밋 메시지를 입력하세요: " commit_message
    if [ -z "$commit_message" ]; then
        echo "커밋 메시지가 비어 있습니다. 종료합니다."
        exit 1
    fi

    echo
    echo "=== 변경사항 스테이징 ==="
    git add -A
    echo
    echo "=== 커밋 예정 내용 ==="
    git status
    echo
    echo "=== 커밋 생성 ==="
    git commit -m "$commit_message"
else
    echo "새로 커밋할 변경사항이 없습니다."
    echo "기존에 push되지 않은 커밋이 있다면 push합니다."
fi

echo
echo "=== GitHub에 push ==="
git push --set-upstream origin "$branch"
echo
echo "=== 완료 ==="
git status
