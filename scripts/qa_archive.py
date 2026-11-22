#!/usr/bin/env python3
"""问答档案库:存档每轮问答,重复问题可直接命中,答案可溯源。

数据文件: books/<书名>/问答库/档案.jsonl (每行一条 JSON)
字段: id, ts, question, answer, sources, hits

命令:
  add    --book 书名 --question 问题 --answer 答案 [--sources 出处]
  find   --book 书名 --query 查询 [--top 3] [--min-score 0.5]   # 只读,不写库
  update --book 书名 --id N [--question Q] [--answer A] [--sources S]
  import-log --book 书名        # 从 对话记录.md 回填(幂等,按问题哈希去重)
  list   --book 书名            # 列出全部条目摘要
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime

BASE = "books"

# 问句模板字(低区分度),实体重叠度计算时剔除
TEMPLATE_BIGRAMS = {
    "什么", "为什", "怎么", "么", "是谁", "的谁", "吗", "呢", "吧", "啊",
    "了么", "了", "多少", "哪些", "没", "没有", "为", "什", "谁", "几",
    "岁", "是不是", "是否", "能", "可以", "怎样", "如何", "哪", "个",
}


def book_dir(book):
    if os.path.isabs(book):
        return book
    if book.startswith(BASE + os.sep) or book == BASE:
        return book
    return os.path.join(BASE, book)


def path_of(book):
    return os.path.join(book_dir(book), "问答库", "档案.jsonl")


def require_book(book):
    d = book_dir(book)
    if not os.path.isdir(d):
        sys.exit(f"书目不存在: {d}(可用书目见 {BASE}/ 下目录)")
    return d


def load(book):
    p = path_of(book)
    if not os.path.isfile(p):
        return []
    entries = []
    with open(p, encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception as e:
                print(f"[qa_archive] 跳过损坏的 JSON 行 #{idx}: {e}", file=sys.stderr)
    return entries


def save(book, entries):
    """原子写:临时文件 + os.replace,避免并发写半途文件。"""
    p = path_of(book)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def norm(s):
    """只保留字母数字与汉字,全角/半角标点一律剥离(保证问号变体同哈希)。"""
    return re.sub(r"[^0-9a-zA-Z一-鿿]+", "", s).lower()


def qhash(q):
    return hashlib.md5(norm(q).encode()).hexdigest()[:12]


def bigrams(s):
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def content_bigrams(s):
    return bigrams(norm(s)) - TEMPLATE_BIGRAMS


def score(query, question, idf=None):
    """字符 IDF 重合度 × 实体重叠约束。

    - 字符分: 库内越常见的字权重越低(的/是/什么),专属字权重高。
    - 实体约束: 剔除问句模板后的 bigram 重叠率,防止"齐夏的真实身份"
      错中"许流年的真实身份"(模板字撑起的高分)。
    - 短查询(<=3 字)封顶 0.7,避免"齐夏"两字任意命中。
    """
    qn, pn = norm(query), norm(question)
    qc = set(qn)
    if not qc:
        return 0.0
    idf = idf or {}

    def w(ch):
        df = idf.get(ch, 1)
        return 1.0 if df <= 1 else 1.0 / df

    num = sum(w(c) for c in qc & set(pn))
    den = sum(w(c) for c in qc)
    char_s = num / den if den else 0.0

    qb, qcb = bigrams(qn), content_bigrams(qn)
    if qcb:
        shared = qcb & content_bigrams(pn)
        entity = len(shared) / len(qcb)
    else:
        entity = 1.0  # 查询无内容 bigram(如纯模板),不惩罚
    jac = len(qb & bigrams(pn)) / len(qb | bigrams(pn)) if qb else 0.0

    s = 0.75 * char_s * (0.5 + 0.5 * entity) + 0.25 * jac
    if len(qn) <= 3:
        s = min(s, 0.7)
    return s


def build_idf(entries):
    n = max(1, len(entries))
    df = {}
    for e in entries:
        for c in set(norm(e["question"])):
            df[c] = df.get(c, 0) + 1
    return {c: (3 if k * 2 >= n else (2 if k else 1)) for c, k in df.items()}


def cmd_add(a):
    require_book(a.book)
    if "本块人物索引" in (a.answer or "") or (a.sources and "本块人物索引" in a.sources):
        print(f"拒绝归档包含本块人物索引的低质回答: {a.question}")
        return
    entries = load(a.book)
    h = qhash(a.question)
    if any(e["qhash"] == h for e in entries):
        print(f"已存在同题条目,跳过(查 find 可见): {a.question}")
        return
    entry = {
        "id": len(entries) + 1,
        "qhash": h,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "question": a.question,
        "answer": a.answer,
        "sources": a.sources or "",
        "hits": 0,
    }
    entries.append(entry)
    save(a.book, entries)
    print(f"已存档 #{entry['id']} {entry['ts']}")


def cmd_find(a):
    """只读检索:不写档案、不累加 hits。"""
    require_book(a.book)
    entries = load(a.book)
    idf = build_idf(entries)
    ranked = sorted(
        ((score(a.query, e["question"], idf), e) for e in entries),
        key=lambda t: t[0], reverse=True,
    )
    best = [(e, s) for s, e in ranked[: a.top] if s >= a.min_score]
    if not best:
        print("NO_MATCH")
        return
    for e, s in best:
        ans = e["answer"]
        if len(ans) > 300:
            ans = ans[:300] + "…"
        print(f"=== #{e['id']} 相似度{s:.2f} {e['ts']} 命中{e['hits']}次 ===")
        print("问:", e["question"])
        print("答:", ans)
        print("出处:", e["sources"])


def cmd_update(a):
    require_book(a.book)
    entries = load(a.book)
    hit = [e for e in entries if e["id"] == a.id]
    if not hit:
        sys.exit(f"找不到条目 #{a.id}")
    e = hit[0]
    if a.question:
        e["question"] = a.question
        e["qhash"] = qhash(a.question)
    if a.answer:
        e["answer"] = a.answer
    if a.sources:
        e["sources"] = a.sources
    save(a.book, entries)
    print(f"已更新 #{e['id']}")


def cmd_import_log(a):
    require_book(a.book)
    log = os.path.join(book_dir(a.book), "对话记录.md")
    if not os.path.isfile(log):
        sys.exit(f"找不到 {log}")
    entries = load(a.book)
    known = {e["qhash"] for e in entries}
    text = open(log, encoding="utf-8").read()
    added = 0
    cur_ts = ""
    q, ans = None, []

    def flush():
        nonlocal added, q, ans
        if q and qhash(q) not in known:
            entries.append({
                "id": 0, "qhash": qhash(q), "ts": cur_ts,
                "question": q, "answer": " ".join(x for x in ans if x),
                "sources": "", "hits": 0,
            })
            known.add(qhash(q))
            added += 1
        q, ans = None, []

    for line in text.splitlines():
        m = re.match(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", line)
        if m:
            flush()
            cur_ts = m.group(1)
            continue
        if re.match(r"^问[^:：]*[:：]", line):
            flush()
            q = re.sub(r"^问[^:：]*[:：]\s*", "", line)
            continue
        if re.match(r"^答[^:：]*[:：]", line) and q:
            ans.append(re.sub(r"^答[^:：]*[:：]\s*", "", line))
    flush()
    for i, e in enumerate(entries, 1):
        e["id"] = i
    save(a.book, entries)
    print(f"回填 {added} 条,库内共 {len(entries)} 条")


def cmd_list(a):
    require_book(a.book)
    for e in load(a.book):
        print(f"#{e['id']} {e['ts']} 命中{e['hits']} | {e['question']}")


def main():
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument("--book", default=argparse.SUPPRESS, help="书名或目录")

    ap = argparse.ArgumentParser(parents=[base_parser])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", parents=[base_parser])
    p.add_argument("--question", required=True)
    p.add_argument("--answer", required=True)
    p.add_argument("--sources", default="")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("find", parents=[base_parser])
    p.add_argument("--query", required=True)
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--min-score", type=float, default=0.5)
    p.set_defaults(fn=cmd_find)
    p = sub.add_parser("update", parents=[base_parser])
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--question")
    p.add_argument("--answer")
    p.add_argument("--sources")
    p.set_defaults(fn=cmd_update)
    p = sub.add_parser("import-log", parents=[base_parser])
    p.set_defaults(fn=cmd_import_log)
    p = sub.add_parser("list", parents=[base_parser])
    p.set_defaults(fn=cmd_list)
    a = ap.parse_args()
    book_val = getattr(a, "book", None)
    if not book_val:
        sys.exit("请通过 --book 指定书目")
    a.book = book_val
    a.fn(a)


if __name__ == "__main__":
    main()
