from pathlib import Path

from scripts import take_screenshots as ts


def test_shots_cover_widths_and_dark_mode():
    widths = {s.width for s in ts.SHOTS}
    assert ts.MOBILE_WIDTH in widths and max(widths) >= 1280
    assert any(s.dark for s in ts.SHOTS)
    assert len({s.name for s in ts.SHOTS}) == len(ts.SHOTS)


def test_mobile_shots_are_framed(tmp_path):
    wide = ts.Shot("w", "bm", 1440, 900)
    mobile = ts.Shot("m", "bm", ts.MOBILE_WIDTH, 900)
    assert ts.target_url(wide, tmp_path).endswith("report.html#view=bm")
    url = ts.target_url(mobile, tmp_path)
    frame = (tmp_path / "m.frame.html").read_text(encoding="utf-8")
    assert url.startswith("file://") and "width:390px" in frame and "#view=bm" in frame


def test_dark_shots_force_dark_mode():
    out = Path("/tmp/x.png")
    dark = ts.chrome_args("chrome", ts.Shot("d", "", 1440, 900, dark=True), "file:///r", out)
    light = ts.chrome_args("chrome", ts.Shot("l", "", 1440, 900), "file:///r", out)
    assert "--force-dark-mode" in dark and "--force-dark-mode" not in light
    assert "--blink-settings=preferredColorScheme=0" in dark
    assert "--blink-settings=preferredColorScheme=1" in light
    assert dark[-1] == "file:///r" and f"--screenshot={out}" in dark
