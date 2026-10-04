from pathlib import Path

import pytest

from scripts import prepare_try as pt


def test_prepares_and_resets_only_owned_directory(tmp_path):
    target = tmp_path / "試験 出力"
    pt.prepare(target)
    (target / "data.json").write_text("old")
    pt.prepare(target)
    assert (target / "state").is_dir()
    assert not (target / "data.json").exists()


def test_rejects_unowned_directory_without_deleting_contents(tmp_path):
    sentinel = tmp_path / "keep"
    sentinel.write_text("keep")
    with pytest.raises(ValueError):
        pt.prepare(tmp_path)
    assert sentinel.read_text() == "keep"


def test_rejects_symlinks_repository_and_parents(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    for target in (link, pt.ROOT, pt.ROOT.parent, Path("/"), Path.home()):
        with pytest.raises(ValueError):
            pt.prepare(target)
