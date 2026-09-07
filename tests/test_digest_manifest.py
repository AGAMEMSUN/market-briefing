import digest_manifest


def test_manifest_path_uses_date(tmp_path):
    assert digest_manifest.manifest_path(tmp_path, "2026-09-06") == tmp_path / "2026-09-06.manifest.json"


def test_write_then_read_roundtrip(tmp_path):
    path = digest_manifest.manifest_path(tmp_path, "2026-09-06")
    digest_manifest.write_manifest(path, collected=["A", "B"], excluded=["C"], not_checked=["D"])
    data = digest_manifest.read_manifest(path)
    assert data["collected"] == ["A", "B"]
    assert data["excluded"] == ["C"]
    assert data["not_checked"] == ["D"]


def test_read_missing_file_returns_empty_lists(tmp_path):
    assert digest_manifest.read_manifest(tmp_path / "nope.json") == {
        "collected": [], "excluded": [], "not_checked": []}


def test_read_missing_file_returns_fresh_lists_each_call(tmp_path):
    first = digest_manifest.read_manifest(tmp_path / "nope.json")
    first["collected"].append("오염")
    assert digest_manifest.read_manifest(tmp_path / "nope.json")["collected"] == []


def test_read_old_manifest_without_new_keys(tmp_path):
    path = tmp_path / "old.manifest.json"
    path.write_text('{"matched": ["A"], "missing": ["B"]}', encoding="utf-8")
    data = digest_manifest.read_manifest(path)
    assert data == {"collected": [], "excluded": [], "not_checked": []}


def test_not_checked_defaults_to_empty(tmp_path):
    path = digest_manifest.manifest_path(tmp_path, "2026-09-06")
    digest_manifest.write_manifest(path, collected=["A"], excluded=[])
    assert digest_manifest.read_manifest(path)["not_checked"] == []


def test_prune_legacy_title_keys():
    """체크포인트는 채널 ID 로 키잉한다. 제목 기반 옛 키는 저장 시 걷어낸다."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "telegram_digest.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("def _prune_legacy_keys")
    end = source.index("def _load_last_ids")
    namespace: dict = {}
    exec(source[start:end], namespace)
    prune = namespace["_prune_legacy_keys"]

    got = prune({"미국 주식 인사이더": 62987, "1234567890": 42, "-1001234567890": 7})
    assert got == {"1234567890": 42, "-1001234567890": 7}
