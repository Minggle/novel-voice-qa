#!/usr/bin/env python3
"""按章节边界把原文切块,供并行子代理精读。输出 chunks/NNN.txt + MANIFEST.txt。

Usage / 用法:
  python split_book.py books/<书名>/原文.txt books/<书名>/chunks [每块目标字数,默认80000]
"""
import os
import re
import sys

CH_RE = re.compile(
    r"^[ \t]*第[0-9零一二三四五六七八九十百千万两]+[章节回卷部篇][^\n]*$", re.M)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__.strip())
        return
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, outdir = sys.argv[1], sys.argv[2]
    target = int(sys.argv[3]) if len(sys.argv) > 3 else 80000
    text = open(src, encoding="utf-8").read()
    os.makedirs(outdir, exist_ok=True)

    starts = [m.start() for m in CH_RE.finditer(text)]
    if not starts:
        sys.exit("未识别到章节标记,请改用按字数切块")

    bounds = [0]
    last = 0
    for s in starts[1:]:
        if s - last >= target:
            bounds.append(s)
            last = s
    bounds.append(len(text))

    manifest = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        part = text[a:b]
        fn = os.path.join(outdir, f"{i + 1:03d}.txt")
        with open(fn, "w", encoding="utf-8", newline="") as f:
            f.write(part)
        first = CH_RE.search(part)
        n_ch = len(CH_RE.findall(part))
        manifest.append(
            f"{i + 1:03d}.txt | {len(part):,}字 | 约{n_ch}章 | 起始: "
            f"{first.group().strip() if first else '开篇'}")

    with open(os.path.join(outdir, "MANIFEST.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(manifest) + "\n")
    print(f"共 {len(bounds) - 1} 块 -> {outdir}/  清单: MANIFEST.txt")


if __name__ == "__main__":
    main()
