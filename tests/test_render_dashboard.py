import json

import pytest

import render_dashboard


def test_escapes_script_close_tag():
    """텔레그램 본문에 `</script>` 가 있으면 스크립트 블록이 조기 종료된다.

    채널 본문은 제3자가 쓰는 내용이라 실제 주입 경로다. 실측으로 재현했던 사례.
    """
    payload = json.dumps({"organized": "본문에 </script><img src=x onerror=alert(1)> 포함"},
                         ensure_ascii=False)
    escaped = render_dashboard.escape_for_script(payload)
    assert "</script>" not in escaped
    assert "\\u003c" in escaped


def test_escaped_payload_still_parses_to_the_same_value():
    original = {"organized": "a </script> b", "title": "<b>제목</b>"}
    escaped = render_dashboard.escape_for_script(json.dumps(original, ensure_ascii=False))
    assert json.loads(escaped) == original


def test_escapes_js_line_separators():
    payload = json.dumps({"x": "a b c"}, ensure_ascii=False)
    escaped = render_dashboard.escape_for_script(payload)
    assert " " not in escaped
    assert " " not in escaped
    assert json.loads(escaped) == {"x": "a b c"}


def test_escapes_html_comment_opener():
    payload = json.dumps({"x": "<!-- 주석"}, ensure_ascii=False)
    assert "<!--" not in render_dashboard.escape_for_script(payload)


def test_render_replaces_the_placeholder():
    out = render_dashboard.render("before __PAYLOAD__ after", '{"a": 1}')
    assert out == 'before {"a": 1} after'


def test_render_rejects_template_without_placeholder():
    with pytest.raises(ValueError):
        render_dashboard.render("자리표시자 없음", '{"a": 1}')


def test_rendered_script_block_is_not_terminated_early():
    """실제 템플릿에 넣었을 때 스크립트 블록이 하나로 유지되는지."""
    from pathlib import Path
    template = (Path(render_dashboard.__file__).resolve().parent.parent
                / "templates" / "dashboard.html").read_text(encoding="utf-8")
    payload = json.dumps(
        {"date": "2026-09-06", "collected_at": "", "channel_count": 1, "message_count": 1,
         "indicators": [], "quiet_channels": [], "not_checked_channels": [],
         "excluded_channels": [],
         "topics": [{"topic_id": "t1", "title": "테스트", "primary_channel": "A",
                     "merged": [], "organized": "</script><img src=x onerror=alert(1)>",
                     "summary": "요약", "indicators": [], "draft_insight": "초안"}]},
        ensure_ascii=False)
    rendered = render_dashboard.render(template, payload)
    # 템플릿 자체의 </script> 는 딱 하나여야 한다 — payload 가 하나를 더 만들면 안 된다.
    assert rendered.count("</script>") == 1
