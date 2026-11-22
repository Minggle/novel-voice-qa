"""split_book.py:按章节切块与无章节回退。"""
import os

from conftest import run_script


def _split(src, outdir, extra=None):
    return run_script("split_book.py", [str(src), str(outdir), *(extra or [])])


def test_chapter_split(chapter_file, tmp_path):
    out = tmp_path / "out_chapters"
    r = _split(chapter_file, out)
    assert r.returncode == 0, r.stderr
    files = sorted(os.listdir(out))
    txts = [f for f in files if f.endswith(".txt")]
    assert len(txts) >= 2, files
    assert any("MANIFEST" in f for f in files)


def test_plain_fallback_split(plain_file, tmp_path):
    out = tmp_path / "out_plain"
    r = _split(plain_file, out)
    assert r.returncode == 0, r.stderr
    txts = [f for f in os.listdir(out) if f.endswith(".txt")]
    assert len(txts) >= 1
    total = sum((out / f).stat().st_size for f in txts)
    assert total >= plain_file.stat().st_size * 0.95


def test_help_ok(tmp_path):
    r = run_script("split_book.py", ["--help"])
    assert r.returncode == 0
    assert "usage" in (r.stdout + r.stderr).lower()
