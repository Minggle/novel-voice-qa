#!/usr/bin/env python3
"""导入小说:自动识别编码(UTF-8/UTF-16/GBK/BIG5),统一转成 UTF-8 存入 books/<书名>/原文.txt。

用法:
  python3 import_book.py <小说文件>          # 书名取文件名
  python3 import_book.py <小说文件> 三体     # 指定书名
  python3 import_book.py <小说文件> 三体 --force   # 覆盖已存在的原文
"""
import argparse
import codecs
import os
import re
import sys

HAN = re.compile(r"[\u4e00-\u9fff]")
NUL = b"\x00"


def han_ratio(s: str) -> float:
    if not s:
        return 0.0
    return sum(1 for c in s if HAN.match(c)) / len(s)


def windows(text: str, n: int = 1000):
    """头/中/尾三个窗口,避免英文序章或标点开头导致误判。"""
    if len(text) <= n:
        return [text]
    return [text[:n], text[len(text) // 2 - n // 2: len(text) // 2 + n // 2], text[-n:]]


# 常见字与常见标点,用于区分真实中文与乱码伪汉字
COMMON_CHARS = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就会可也你对能后自学年而得之多样想说种回第"
    "這國時為會後學說樣滿紙荒唐言一把辛酸淚甄士隱賈雨村風塵夢幻通靈"
    "，。！？、“”《》【】…—"
)


def common_score(s: str) -> float:
    if not s:
        return -1.0
    # 乱码转 gb18030 常产生大量 Unicode 私用区(PUA \ue000-\uf8ff)
    pua = sum(1 for c in s if 0xe000 <= ord(c) <= 0xf8ff)
    if pua > max(1, len(s) * 0.03):
        return -1.0
    return sum(1 for c in s if c in COMMON_CHARS) / len(s)


def detect_and_read(path):
    data = open(path, "rb").read()

    # 1) 带 BOM 的优先
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass

    # 2) utf-16 无 BOM:NUL 密度探测
    sample = data[:4000]
    if sample.count(NUL) > len(sample) * 0.02:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                text = data.decode(enc)
            except UnicodeDecodeError:
                continue
            if han_ratio(text[:2000]) > 0.15:
                return text, enc

    # 3) 多窗口投票 + 常用字打分 (区分 utf-8 / big5 / gb18030)
    candidates = []
    for enc in ("utf-8", "big5", "gb18030"):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        wins = sum(1 for w in windows(text) if han_ratio(w) > 0.15)
        if wins >= min(2, len(windows(text))):
            score = common_score(text[:4000])
            candidates.append((score, enc, text))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_enc, best_text = candidates[0]
        if best_score >= 0:
            return best_text, best_enc

    sys.exit("无法识别文件编码,请先手动转为 UTF-8")


def main():
    ap = argparse.ArgumentParser(description="导入小说并转码为 UTF-8")
    ap.add_argument("src", help="小说文件路径(txt;docx/epub 由调用方先转 txt)")
    ap.add_argument("title", nargs="?", help="书名,默认取文件名")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的原文.txt")
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        sys.exit(f"文件不存在: {args.src}")
    title = args.title or os.path.splitext(os.path.basename(args.src))[0]

    dst_dir = os.path.join("books", title)
    dst = os.path.join(dst_dir, "原文.txt")
    if os.path.exists(dst) and not args.force:
        print(f"已存在: {dst}(跳过,覆盖请加 --force)")
        return

    text, enc = detect_and_read(args.src)
    os.makedirs(dst_dir, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    chapters = len(re.findall(
        r"^\s*第[0-9零一二三四五六七八九十百千万两]+[章节回卷部篇]", text, re.M))
    print(f"书名: {title}")
    print(f"来源编码: {enc}")
    print(f"字数: {len(text):,}")
    print(f"识别到章节标记: {chapters} 个")
    print(f"已保存: {dst}")


if __name__ == "__main__":
    main()
