"""import_book.py:编码识别与覆盖保护。"""
from conftest import CN_TEXT, CN_TRAD_TEXT, run_script


def _import(src, name, extra=None, cwd=None):
    workdir = cwd or (src.parent if hasattr(src, "parent") else None)
    kw = {"cwd": str(workdir)} if workdir else {}
    return run_script("import_book.py", [str(src), name, *(extra or [])], **kw)


def test_gbk_roundtrip(gbk_file, tmp_path):
    r = _import(gbk_file, "测试gbk")
    assert r.returncode == 0, r.stderr
    out = tmp_path / "books" / "测试gbk" / "原文.txt"
    assert out.exists()
    assert CN_TEXT.strip() in out.read_text(encoding="utf-8")


def test_utf16_no_bom(utf16_nobom_file, tmp_path):
    r = _import(utf16_nobom_file, "测试utf16")
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "books" / "测试utf16" / "原文.txt").read_text(encoding="utf-8")
    assert CN_TEXT.strip() in text


def test_big5_roundtrip(big5_file, tmp_path):
    r = _import(big5_file, "测试big5")
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "books" / "测试big5" / "原文.txt").read_text(encoding="utf-8")
    assert CN_TRAD_TEXT.strip() in text


def test_english_preface_not_misdetected(mixed_file, tmp_path):
    r = _import(mixed_file, "测试mixed")
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "books" / "测试mixed" / "原文.txt").read_text(encoding="utf-8")
    assert "PREFACE" in text
    assert "桃李春风" in text


def test_reimport_without_force_kept(gbk_file, tmp_path):
    assert _import(gbk_file, "测试重复").returncode == 0
    out = tmp_path / "books" / "测试重复" / "原文.txt"
    out.write_text("已被修改", encoding="utf-8")
    r = _import(gbk_file, "测试重复")
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8") == "已被修改"
    r2 = _import(gbk_file, "测试重复", ["--force"])
    assert r2.returncode == 0, r2.stderr
    assert CN_TEXT.strip() in out.read_text(encoding="utf-8")


def test_help_not_treated_as_file(tmp_path):
    r = run_script("import_book.py", ["--help"], cwd=str(tmp_path))
    assert r.returncode == 0
    assert "usage" in (r.stdout + r.stderr).lower()
    assert not (tmp_path / "books" / "--help").exists()
