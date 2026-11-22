"""qa_archive.py:add/find/update/去重/退出码。"""
from conftest import read_jsonl, run_script


def test_add_then_find_exact(book_dir, tmp_path):
    arch = book_dir / "问答库" / "档案.jsonl"
    r = run_script("qa_archive.py", [
        "add", "--book", str(book_dir),
        "--question", "齐夏的真实身份是什么",
        "--answer", "齐夏是回响者,第1333章揭晓。", "--sources", "第1333章",
    ])
    assert r.returncode == 0, r.stderr
    assert arch.exists()
    entries = read_jsonl(arch)
    assert len(entries) == 1
    assert entries[0]["question"] == "齐夏的真实身份是什么"
    assert entries[0]["sources"] == "第1333章"

    f = run_script("qa_archive.py", ["find", "--book", str(book_dir), "--query", "齐夏的真实身份是什么"])
    assert f.returncode == 0, f.stderr
    assert "回响者" in f.stdout


def test_add_dedup_same_question(book_dir):
    args = ["add", "--book", str(book_dir), "--question", "甜甜的回响是什么",
            "--answer", "巧物。", "--sources", "第1292章"]
    assert run_script("qa_archive.py", args).returncode == 0
    assert run_script("qa_archive.py", args).returncode == 0
    entries = read_jsonl(book_dir / "问答库" / "档案.jsonl")
    assert len(entries) == 1


def test_find_is_read_only(book_dir):
    """find 不得改写档案、不得累加 hits。"""
    arch = book_dir / "问答库" / "档案.jsonl"
    run_script("qa_archive.py", ["add", "--book", str(book_dir), "--question", "人鼠今年多大了",
                                 "--answer", "十岁。", "--sources", "第273章"])
    before = arch.read_bytes()
    run_script("qa_archive.py", ["find", "--book", str(book_dir), "--query", "人鼠今年多大了"])
    assert arch.read_bytes() == before
    entries = read_jsonl(arch)
    assert entries[0].get("hits", 0) == 0


def test_find_missing_book_nonzero_exit(tmp_path):
    r = run_script("qa_archive.py", ["find", "--book", str(tmp_path / "没有这本书"), "--query", " test "])
    assert r.returncode != 0
    assert r.stderr.strip()


def test_find_min_score_threshold(book_dir):
    run_script("qa_archive.py", ["add", "--book", str(book_dir), "--question", "乔家劲的回响是什么",
                                 "--answer", "破万法。", "--sources", "第591章"])
    hi = run_script("qa_archive.py", ["find", "--book", str(book_dir), "--query", "乔家劲的回响是什么",
                                      "--min-score", "0.9"])
    assert hi.returncode == 0 and "破万法" in hi.stdout
    lo = run_script("qa_archive.py", ["find", "--book", str(book_dir), "--query", "番茄炒蛋怎么做",
                                      "--min-score", "0.9"])
    assert "破万法" not in lo.stdout


def test_update_entry(book_dir):
    run_script("qa_archive.py", ["add", "--book", str(book_dir), "--question", "楚天秋的结局",
                                 "--answer", "成神失败。", "--sources", "第1189章"])
    entries = read_jsonl(book_dir / "问答库" / "档案.jsonl")
    r = run_script("qa_archive.py", ["update", "--book", str(book_dir), "--id", str(entries[0]["id"]),
                                     "--answer", "砸金救文巧云后灰飞烟灭。", "--sources", "第1358章"])
    assert r.returncode == 0, r.stderr
    entries = read_jsonl(book_dir / "问答库" / "档案.jsonl")
    assert "灰飞烟灭" in entries[0]["answer"]
    assert entries[0]["sources"] == "第1358章"


def test_fullwidth_punct_same_hash(book_dir):
    """全角/半角问号视为同一题。"""
    base = ["add", "--book", str(book_dir), "--answer", "答案。", "--sources", "第1章"]
    run_script("qa_archive.py", base + ["--question", "齐夏是谁?"])
    run_script("qa_archive.py", base + ["--question", "齐夏是谁？"])
    entries = read_jsonl(book_dir / "问答库" / "档案.jsonl")
    assert len(entries) == 1


def test_import_log_regex_parsing_and_dedup(book_dir):
    """测试从 对话记录.md 回填问答归档, 验证时间戳和问答正则转义正确。"""
    log_file = book_dir / "对话记录.md"
    log_content = """# 对话记录

## 2026-08-30 23:35
问: 齐夏今年几岁了?
答: 表面答案是二十六岁。

## 2026-08-31 00:14(自测轮)
问(自测,接续白羊时间线): 天龙当年为什么要洗去齐夏的记忆?
答: 动机有三层。
"""
    log_file.write_text(log_content, encoding="utf-8")

    r = run_script("qa_archive.py", ["import-log", "--book", str(book_dir)])
    assert r.returncode == 0, r.stderr
    assert "回填 2 条" in r.stdout

    entries = read_jsonl(book_dir / "问答库" / "档案.jsonl")
    assert len(entries) == 2
    assert entries[0]["ts"] == "2026-08-30 23:35"
    assert entries[0]["question"] == "齐夏今年几岁了?"
    assert entries[0]["answer"] == "表面答案是二十六岁。"
    assert entries[1]["ts"] == "2026-08-31 00:14"
    assert entries[1]["question"] == "天龙当年为什么要洗去齐夏的记忆?"
    assert entries[1]["answer"] == "动机有三层。"

    # 幂等性测试: 再次执行回填不应产生重复项
    r2 = run_script("qa_archive.py", ["import-log", "--book", str(book_dir)])
    assert r2.returncode == 0
    assert "回填 0 条" in r2.stdout
    entries2 = read_jsonl(book_dir / "问答库" / "档案.jsonl")
    assert len(entries2) == 2

