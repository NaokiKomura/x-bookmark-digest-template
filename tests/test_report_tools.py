import json

from scripts import report_tools as rt


def sample():
    return rt.extract_data(rt.TEMPLATE.read_text(encoding="utf-8"))


def test_template_sample_is_valid():
    assert rt.validate_data(sample()) == []


def test_pick_reasons_are_optional_for_existing_reports():
    data = sample()
    del data["pick_reasons"]
    assert rt.validate_data(data) == []


def test_pick_reasons_reject_unknown_ids_empty_text_and_wrong_types():
    data = sample()
    for reasons in ({"nope": "理由"}, {"s1": " "}, {"s1": 7}, ["理由"], None):
        data["pick_reasons"] = reasons
        assert any("pick_reasons" in error for error in rt.validate_data(data))


def test_pick_reasons_build_roundtrip_escapes_article_text(tmp_path):
    data = sample()
    data["pick_reasons"] = {"s1": "</script><script>alert(1)</script>"}
    src, out = tmp_path / "d.json", tmp_path / "r.html"
    src.write_text(json.dumps(data, ensure_ascii=False))
    rt.cmd_build(src, out)
    assert rt.cmd_validate(out) == []
    assert "</script><script>alert" not in out.read_text()
    assert rt.extract_data(out.read_text())["pick_reasons"] == data["pick_reasons"]


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
    data["posts"][2]["read_min"] = 54
    errors = rt.validate_data(data)
    assert any("theme" in e for e in errors)
    assert any("https" in e for e in errors)
    assert any("nope" in e for e in errors)
    assert any("fetch_status" in e for e in errors)
    assert any("read_min" in e for e in errors)


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


def test_validate_checks_visual_shapes():
    data = sample()
    types = {p["visual"]["type"] for p in data["posts"] if p.get("visual")}
    assert "matrix" in types  # サンプルに新しい種類の例がある
    post = data["posts"][0]
    post["visual"] = {
        "type": "matrix",
        "x": {"label": "x"},
        "y": {"label": "y"},
        "items": [{"name": "A", "x": 4, "y": 1}],
    }
    data["posts"][1]["visual"] = {"type": "flow", "steps": [{"detail": "label がない"}]}
    data["posts"][3]["visual"] = {"type": "options", "items": [{"name": "A", "value": "多い"}]}
    data["posts"][5]["visual"] = {
        "type": "versus",
        "a": {"label": "A"},
        "b": {"label": "B"},
        "criteria": [{"name": "速さ", "better": "c"}],
    }
    data["github"][0]["visual"] = {"type": "stat", "value": 3, "compare": {"label": "前回"}}
    errors = rt.validate_data(data)
    for word in ("matrix", "flow", "options", "versus", "stat.compare"):
        assert any(word in e for e in errors), word


def test_validate_checks_section_summaries():
    data = sample()
    assert data["section_summaries"]["blogs"]
    data["section_summaries"] = {"blogs": 1, "other": "x"}
    assert any("section_summaries" in e for e in rt.validate_data(data))


def write_report(reports, day, data):
    data = {**data, "date": day}
    src = reports / f"{day}.json"
    src.write_text(json.dumps(data, ensure_ascii=False))
    rt.cmd_build(src, reports / f"{day}.html")


def test_carryover_takes_the_three_previous_days_only(tmp_path):
    base = {k: v for k, v in sample().items() if k != "carryover"}
    for day in ("2026-10-09", "2026-10-08", "2026-10-07", "2026-10-06"):
        write_report(tmp_path, day, base)
    carry = rt.cmd_carryover("2026-10-10", tmp_path)
    assert [c["date"] for c in carry] == ["2026-10-09", "2026-10-08", "2026-10-07"]
    assert set(carry[0]) == {"date", *rt.CARRY_SECTIONS}
    assert [p["id"] for p in carry[0]["posts"]] == [p["id"] for p in base["posts"]]


def test_carryover_does_not_carry_a_reports_own_carryover(tmp_path):
    write_report(
        tmp_path, "2026-10-03", sample()
    )  # サンプルは 2026-10-02 分の繰り越し（c1 など）を持つ
    carry = rt.cmd_carryover("2026-10-04", tmp_path)
    carried_ids = {item["id"] for section in rt.CARRY_SECTIONS for item in carry[0][section]}
    assert "c1" not in carried_ids


def test_carryover_skips_missing_days_and_invalid_items(tmp_path):
    data = {k: v for k, v in sample().items() if k != "carryover"}
    write_report(tmp_path, "2026-10-08", data)
    html = tmp_path / "2026-10-08.html"
    broken = rt.extract_data(html.read_text())
    broken["posts"][0]["theme"] = "not_a_topic"
    html.write_text(
        rt.BLOCK.sub(lambda m: m.group(1) + rt.embed(broken) + m.group(3), html.read_text())
    )
    carry = rt.cmd_carryover("2026-10-10", tmp_path)
    assert [c["date"] for c in carry] == ["2026-10-08"]
    assert broken["posts"][0]["id"] not in [p["id"] for p in carry[0]["posts"]]
    assert rt.cmd_carryover("2026-10-20", tmp_path) == []


def test_validate_checks_carryover():
    data = sample()
    assert rt.validate_data({**data, "carryover": []}) == []
    future = [{"date": data["date"], "posts": []}]
    assert any("carryover.date" in e for e in rt.validate_data({**data, "carryover": future}))
    bad = [{"date": "2026-10-01", "posts": [{**data["posts"][0], "url": "javascript:alert(1)"}]}]
    assert any(
        e.startswith("carryover 2026-10-01") for e in rt.validate_data({**data, "carryover": bad})
    )
    four = [{"date": f"2026-09-2{i}"} for i in range(4)]
    assert any("3日分まで" in e for e in rt.validate_data({**data, "carryover": four}))
