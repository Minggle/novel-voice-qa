"""test_book_crypto.py: 小说原文加密工具与 web.py 内存解密集成测试。"""
import json
import os
import pytest

from scripts.book_crypto import (
    decrypt_bytes,
    decrypt_file,
    decrypt_text_in_memory,
    encrypt_bytes,
    encrypt_file,
    resolve_book_key,
)
from scripts.web import L2DeepRetriever


SAMPLE_NOVEL = (
    "第1章 初入终焉\n"
    "齐夏睁开眼时，发现自己坐在狭小的面试房间桌前，对面坐着头戴羊头面具的人羊。\n\n"
    "第2章 说谎者\n"
    "陈俊南笑着晃了晃手里的牌，十个人必须找出说谎的那个参与者才能活下去。\n"
)


def test_encrypt_decrypt_bytes_roundtrip_and_wrong_password():
    raw = SAMPLE_NOVEL.encode("utf-8")
    cipher = encrypt_bytes(raw, "MySecretPass123!")
    assert cipher != raw
    assert "齐夏".encode("utf-8") not in cipher
    assert "陈俊南".encode("utf-8") not in cipher
    assert decrypt_bytes(cipher, "MySecretPass123!") == raw

    with pytest.raises(ValueError) as exc_info:
        decrypt_bytes(cipher, "WrongPass999!")
    assert "WrongPass999!" not in str(exc_info.value)

    with pytest.raises(ValueError):
        encrypt_bytes(raw, "")
    with pytest.raises(ValueError):
        decrypt_bytes(cipher, "")


def test_encrypt_decrypt_file_and_in_memory(tmp_path):
    plain = tmp_path / "原文.txt"
    enc = tmp_path / "原文.txt.enc"
    restored = tmp_path / "restored.txt"
    plain.write_text(SAMPLE_NOVEL, encoding="utf-8")

    out_len = encrypt_file(str(plain), str(enc), "FileKey-ABC!")
    assert out_len > 0
    assert enc.is_file()

    assert decrypt_text_in_memory(str(enc), "FileKey-ABC!") == SAMPLE_NOVEL

    decrypt_file(str(enc), str(restored), "FileKey-ABC!")
    assert restored.read_text(encoding="utf-8") == SAMPLE_NOVEL


def test_l2c_in_memory_decryption(tmp_path, monkeypatch):
    book_dir = tmp_path / "test_book"
    book_dir.mkdir(parents=True)
    plain_tmp = tmp_path / "src_plain.txt"
    plain_tmp.write_text(SAMPLE_NOVEL, encoding="utf-8")
    enc_path = book_dir / "原文.txt.enc"
    encrypt_file(str(plain_tmp), str(enc_path), "NovelSecret99!")

    # 1. 未设置 NOVEL_BOOK_KEY 且隔离 NOVEL_STARTUP_CONFIG 为不存在文件时优雅降级，跳过 L2C
    monkeypatch.delenv("NOVEL_BOOK_KEY", raising=False)
    monkeypatch.setenv("NOVEL_STARTUP_CONFIG", str(tmp_path / "no_config.json"))
    r1 = L2DeepRetriever(str(book_dir), summary_sections=[])
    r1._init_l2c_lazy()
    assert r1._l2c_available is False
    assert r1.l2c_chapters == []
    assert not (book_dir / "原文.txt").exists()

    # 2. 不设环境变量，仅通过 NOVEL_STARTUP_CONFIG 指向启动配置文件即可自动内存解密且不落盘明文
    startup_cfg = tmp_path / "startup_config.json"
    startup_cfg.write_text(json.dumps({"book_key": "NovelSecret99!"}), encoding="utf-8")
    monkeypatch.setenv("NOVEL_STARTUP_CONFIG", str(startup_cfg))
    r_cfg = L2DeepRetriever(str(book_dir), summary_sections=[])
    r_cfg._init_l2c_lazy()
    assert r_cfg._l2c_available is True
    assert len(r_cfg.l2c_chapters) == 2
    assert r_cfg.l2c_chapters[0]["chap_num"] == "第1章"
    assert not (book_dir / "原文.txt").exists()

    # 3. 设置错误密码时优雅降级且不落盘、不泄露密码
    monkeypatch.setenv("NOVEL_BOOK_KEY", "BadSecret!")
    r2 = L2DeepRetriever(str(book_dir), summary_sections=[])
    r2._init_l2c_lazy()
    assert r2._l2c_available is False
    assert not (book_dir / "原文.txt").exists()

    # 4. 设置正确密码时仅在内存中解密并完成章节索引，磁盘仍无明文 原文.txt
    monkeypatch.setenv("NOVEL_BOOK_KEY", "NovelSecret99!")
    r3 = L2DeepRetriever(str(book_dir), summary_sections=[])
    r3._init_l2c_lazy()
    assert r3._l2c_available is True
    assert len(r3.l2c_chapters) == 2
    assert r3.l2c_chapters[0]["chap_num"] == "第1章"
    assert r3.l2c_chapters[1]["chap_num"] == "第2章"
    assert "面试房间" in r3.l2c_chapters[0]["content"]
    assert not (book_dir / "原文.txt").exists()


def test_repo_startup_config_decrypts_real_enc(monkeypatch):
    monkeypatch.delenv("NOVEL_BOOK_KEY", raising=False)
    monkeypatch.delenv("NOVEL_STARTUP_CONFIG", raising=False)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.join(root, "config.json")
    enc_path = os.path.join(root, "books", "十日终焉", "原文.txt.enc")
    assert os.path.isfile(cfg_path)
    assert os.path.isfile(enc_path)

    key = resolve_book_key()
    assert key != ""
    text = decrypt_text_in_memory(enc_path, key)
    assert len(text) > 3_000_000
    assert "齐夏" in text[:5000]
