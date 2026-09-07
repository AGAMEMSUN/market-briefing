# .agents/skills/ 의 각 스킬을 .claude/skills/ 에 정션으로 연결한다.
# Claude Code 는 슬래시 명령(/brief 등)을 .claude/skills/<이름>/SKILL.md 에서만
# 인식한다. 이 저장소는 스킬 원본을 .agents/skills/ 에 두므로 연결이 필요하다.
# .claude/* 는 .gitignore 대상이라 이 연결은 로컬 전용이다 — 클론·재동기화 후 1회 실행.

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$src  = Join-Path $root '.agents\skills'
$dst  = Join-Path $root '.claude\skills'

New-Item -ItemType Directory -Force -Path $dst | Out-Null

Get-ChildItem -Directory $src | ForEach-Object {
    $link = Join-Path $dst $_.Name
    if (Test-Path $link) { Remove-Item $link -Recurse -Force }
    New-Item -ItemType Junction -Path $link -Target $_.FullName | Out-Null
    Write-Host "linked  $($_.Name)"
}
