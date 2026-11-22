#!/usr/bin/env python3
"""小说原文加密与内存解密工具 (gzip + OpenSSL AES-256-CBC PBKDF2-SHA256)。

用法:
  python3 scripts/book_crypto.py encrypt books/十日终焉/原文.txt
  python3 scripts/book_crypto.py decrypt books/十日终焉/原文.txt.enc
  python3 scripts/book_crypto.py verify  books/十日终焉/原文.txt.enc
"""
import argparse
import getpass
import gzip
import os
import re
import subprocess
import sys
import tempfile
from typing import Optional

_PASS_ENV_VAR = "_NOVEL_BOOK_PASS"
_OPENSSL_CIPHER_ARGS = [
    "-aes-256-cbc",
    "-pbkdf2",
    "-iter",
    "100000",
    "-md",
    "sha256",
]


def _run_openssl(args: list, data: bytes, password: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env[_PASS_ENV_VAR] = password
    try:
        return subprocess.run(
            ["openssl", *args],
            input=data,
            capture_output=True,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("系统未安装 openssl 命令，无法执行加解密") from exc


def encrypt_bytes(data: bytes, password: str) -> bytes:
    """使用 gzip 压缩后通过 AES-256-CBC + PBKDF2 加密字节数据。"""
    if not password:
        raise ValueError("密码不能为空")
    compressed = gzip.compress(data, compresslevel=6)
    proc = _run_openssl(
        ["enc", *_OPENSSL_CIPHER_ARGS, "-salt", "-pass", f"env:{_PASS_ENV_VAR}"],
        compressed,
        password,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").replace(password, "***").strip()
        raise ValueError(f"加密失败: {err or f'exit {proc.returncode}'}")
    return proc.stdout


def decrypt_bytes(cipher: bytes, password: str) -> bytes:
    """解密 AES-256-CBC + PBKDF2 密文并在内存中解压。"""
    if not password:
        raise ValueError("密码不能为空")
    if not cipher:
        raise ValueError("密文内容为空")
    proc = _run_openssl(
        ["enc", "-d", *_OPENSSL_CIPHER_ARGS, "-pass", f"env:{_PASS_ENV_VAR}"],
        cipher,
        password,
    )
    if proc.returncode != 0:
        raise ValueError("解密失败：密码错误或密文已损坏")
    try:
        return gzip.decompress(proc.stdout)
    except Exception as exc:
        raise ValueError("解密失败：密码错误或压缩数据损坏") from exc


def _atomic_write_bytes(dst_path: str, payload: bytes) -> int:
    abs_dst = os.path.abspath(dst_path)
    parent = os.path.dirname(abs_dst)
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".book_crypto_", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp_path, abs_dst)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
    return len(payload)


def encrypt_file(src_path: str, dst_path: str, password: str) -> int:
    """将明文文件压缩加密并原子写入目标路径。"""
    with open(src_path, "rb") as f:
        raw = f.read()
    cipher = encrypt_bytes(raw, password)
    return _atomic_write_bytes(dst_path, cipher)


def decrypt_file(src_path: str, dst_path: str, password: str) -> int:
    """将加密文件解密解压并原子写入目标路径。"""
    with open(src_path, "rb") as f:
        cipher = f.read()
    raw = decrypt_bytes(cipher, password)
    return _atomic_write_bytes(dst_path, raw)


def decrypt_text_in_memory(enc_path: str, password: str) -> str:
    """仅在内存中解密并返回 UTF-8 文本，不在磁盘生成任何明文文件。"""
    with open(enc_path, "rb") as f:
        cipher = f.read()
    raw = decrypt_bytes(cipher, password)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("解密成功但 UTF-8 解码失败") from exc


def _prompt_password(confirm: bool = False) -> str:
    env_pw = os.environ.get("NOVEL_BOOK_KEY", "").strip()
    if env_pw:
        return env_pw
    pw = getpass.getpass("请输入原文加密密码: ").strip()
    if not pw:
        raise ValueError("密码不能为空")
    if confirm:
        pw2 = getpass.getpass("请再次输入确认密码: ").strip()
        if pw != pw2:
            raise ValueError("两次输入的密码不一致")
    return pw


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="小说原文压缩加密与内存校验工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enc = sub.add_parser("encrypt", help="加密明文文件生成 .enc")
    p_enc.add_argument("src", help="明文文件路径 (例如 books/十日终焉/原文.txt)")
    p_enc.add_argument("--out", help="输出加密文件路径 (默认 <src>.enc)")

    p_dec = sub.add_parser("decrypt", help="解密 .enc 还原明文文件")
    p_dec.add_argument("src", help="加密文件路径 (例如 books/十日终焉/原文.txt.enc)")
    p_dec.add_argument("--out", help="输出明文文件路径")

    p_ver = sub.add_parser("verify", help="仅在内存中解密校验 .enc 文件完整性")
    p_ver.add_argument("src", help="加密文件路径")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "encrypt":
            out = args.out or f"{args.src}.enc"
            pw = _prompt_password(confirm=True)
            src_size = os.path.getsize(args.src)
            out_size = encrypt_file(args.src, out, pw)
            print(f"加密完成: {out} ({src_size} B -> {out_size} B)")
            return 0
        if args.cmd == "decrypt":
            default_out = args.src[:-4] if args.src.endswith(".enc") else f"{args.src}.dec"
            out = args.out or default_out
            pw = _prompt_password(confirm=False)
            out_size = decrypt_file(args.src, out, pw)
            print(f"解密完成: {out} ({out_size} B)")
            return 0
        if args.cmd == "verify":
            pw = _prompt_password(confirm=False)
            text = decrypt_text_in_memory(args.src, pw)
            chaps = len(re.findall(r"(?:^|\n)第[0-9一二三四五六七八九十百千万]+章\s+", text))
            print(f"校验通过: {args.src} (共 {len(text)} 字符, 约 {chaps} 章)")
            return 0
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
