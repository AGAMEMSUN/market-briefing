#!/usr/bin/env bash
# .agents/skills/ 의 각 스킬을 .claude/skills/ 에 심볼릭 링크로 연결한다.
# Claude Code 는 슬래시 명령(/brief 등)을 .claude/skills/<이름>/SKILL.md 에서만
# 인식한다. 이 저장소는 스킬 원본을 .agents/skills/ 에 두므로 연결이 필요하다.
# .claude/* 는 .gitignore 대상이라 이 연결은 로컬 전용이다 — 클론 후 1회 실행.
#
# Windows 는 setup-skills.ps1 을 쓴다.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$root/.agents/skills"
dst="$root/.claude/skills"

mkdir -p "$dst"
for skill in "$src"/*/; do
    name="$(basename "$skill")"
    rm -rf "$dst/$name"
    ln -s "$skill" "$dst/$name"
    echo "linked  $name"
done
