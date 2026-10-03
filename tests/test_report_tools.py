import json

from scripts import report_tools as rt


def sample():
    return rt.extract_data(rt.TEMPLATE.read_text(encoding="utf-8"))


def test_template_sample_is_valid():
    assert rt.validate_data(sample()) == []


def test_build_and_validate_roundtrip(tmp_path):
    data = sample()
    data["lede"] = "</script><script>alert(1)</script>"
    src, out = tmp_path / "d.json", tmp_path / "r.html"
    src.write_text(json.dumps(data, ensure_ascii=False))
    rt.cmd_build(src, out)
    html = out.read_text()
    assert "</script><script>alert" not in html
    assert rt.cmd_validate(out) == []
    assert rt.extract_data(html)["lede"] == data["lede"]


def test_validate_rejects_changes_outside_block(tmp_path):
    out = tmp_path / "r.html"
    out.write_text(rt.TEMPLATE.read_text(encoding="utf-8").replace("<h1>", "<h1 class=x>"))
    assert "report-data 以外の部分がテンプレートと違う" in rt.cmd_validate(out)


def test_validate_catches_bad_items():
    data = sample()
    data["posts"][0]["theme"] = "my_new_theme"
    data["posts"][1]["url"] = "javascript:alert(1)"
    data["picks"].append("nope")
    del data["blogs"][0]["fetch_status"]
    errors = rt.validate_data(data)
    assert any("theme" in e for e in errors)
    assert any("https" in e for e in errors)
    assert any("nope" in e for e in errors)
    assert any("fetch_status" in e for e in errors)


def test_trend_counts_missing_days_as_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "2026-10-03.json").write_text(json.dumps({"posts": [{}, {}]}))
    trend = rt.cmd_trend("2026-10-03")
    assert (
        len(trend) == 14
        and trend[-1] == {"date": "2026-10-03", "count": 2}
        and trend[0]["count"] == 0
    )


def test_build_rejects_invalid_data_without_overwriting_output(tmp_path):
    import pytest

    src, out = tmp_path / "invalid.json", tmp_path / "report.html"
    src.write_text("{}")
    out.write_text("previous report")
    with pytest.raises(SystemExit, match="検証に失敗"):
        rt.cmd_build(src, out)
    assert out.read_text() == "previous report"
