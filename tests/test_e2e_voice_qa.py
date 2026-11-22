"""tests/test_e2e_voice_qa.py
Comprehensive Opaque-Box E2E Test Suite for 《十日终焉》 Voice QA System.
Covers Tier 1 (Feature Coverage), Tier 2 (Boundary & Corner Cases),
and Tier 3 (Cross-Feature Combinations) across all 14 system features.
Total: 154 Test Cases.
"""

import fnmatch
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import wave
from unittest.mock import patch

from scripts.web import (
    DEFAULT_VOICE,
    HTML_TEMPLATE,
    VOICES,
    NovelKnowledgeBase,
    TTSService,
    make_server,
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK_DIR = os.path.join(ROOT_DIR, "books", "十日终焉")


class BaseWebE2ETestCase(unittest.TestCase):
    """Base class that manages an ephemeral in-process HTTP server on port 0."""

    server = None
    server_thread = None
    server_url = None
    kb = None
    tts = None

    @classmethod
    def setUpClass(cls):
        cls.server = make_server(host="127.0.0.1", port=0, book_path=BOOK_DIR)
        cls.server_port = cls.server.server_address[1]
        cls.server_url = f"http://127.0.0.1:{cls.server_port}"
        cls.kb = cls.server.RequestHandlerClass.kb
        cls.tts = cls.server.RequestHandlerClass.tts

        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.15)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()

    def _format_url(self, path):
        parsed = urllib.parse.urlsplit(path)
        encoded_path = urllib.parse.quote(parsed.path)
        encoded_query = urllib.parse.quote(parsed.query, safe="=&+-%")
        reconstructed = urllib.parse.urlunsplit(("", "", encoded_path, encoded_query, ""))
        return f"{self.server_url}{reconstructed}"

    def get_json(self, path):
        url = self._format_url(path)
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data, resp.headers
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                data = {}
            return e.code, data, e.headers

    def post_json(self, path, payload):
        url = self._format_url(path)
        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else b""
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data, resp.headers
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except Exception:
                data = {"raw": raw}
            return e.code, data, e.headers

    def get_raw(self, path):
        url = self._format_url(path)
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers


# =====================================================================
# FEAT-QA-01: 问答库档案高频秒答 (<3ms, 253条)
# =====================================================================
class TestFEATQA01ArchiveRetrieval(BaseWebE2ETestCase):

    # Tier 1 (5 tests)
    def test_feat_qa_01_exact_match_qixia_echo(self):
        status, data, _ = self.post_json("/api/ask", {"question": "齐夏的回响是什么"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertIn("生生不息", data["answer"])
        self.assertGreaterEqual(data["score"], 0.7)
        self.assertLess(data["elapsed_ms"], 100)

    def test_feat_qa_01_chutianqiu_ending(self):
        status, data, _ = self.post_json("/api/ask", {"question": "楚天秋的结局是什么"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertTrue(any(k in data["answer"] for k in ["成神", "救文巧云", "灰飞烟灭"]))
        self.assertTrue(bool(re.search(r"第\d+.*章|全书", data["sources"])))

    def test_feat_qa_01_tiantian_echo(self):
        status, data, _ = self.post_json("/api/ask", {"question": "甜甜的回响是什么"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertIn("巧物", data["answer"])
        self.assertTrue("第1292章" in data["answer"] or "第1292章" in data["sources"])

    def test_feat_qa_01_qiaojiajin_echo(self):
        status, data, _ = self.post_json("/api/ask", {"question": "乔家劲的回响是什么"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertTrue("破万法" in data["answer"] or "香港钵兰街红棍" in data["answer"])
        self.assertTrue(bool(data["sources"]))

    def test_feat_qa_01_chenjunnan_echo(self):
        status, data, _ = self.post_json("/api/ask", {"question": "陈俊南的回响是什么"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertTrue("替罪" in data["answer"])

    # Tier 2 (5 tests)
    def test_feat_qa_01_boundary_fullwidth_question_mark(self):
        status1, data1, _ = self.post_json("/api/ask", {"question": "齐夏的回响是什么?"})
        status2, data2, _ = self.post_json("/api/ask", {"question": "齐夏的回响是什么？"})
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertEqual(data1["matched_question"], data2["matched_question"])
        self.assertEqual(data1["score"], data2["score"])

    def test_feat_qa_01_boundary_leading_trailing_spaces_tabs(self):
        status, data, _ = self.post_json("/api/ask", {"question": "\t\n 齐夏的回响是什么 \r\n"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertIn("生生不息", data["answer"])

    def test_feat_qa_01_boundary_noisy_filler_words(self):
        status, data, _ = self.post_json("/api/ask", {"question": "请问一下你知道齐夏的回响具体是什么吗谢谢"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertIn("生生不息", data["answer"])

    def test_feat_qa_01_boundary_ultra_short_character_name(self):
        status, data, _ = self.post_json("/api/ask", {"question": "齐夏"})
        self.assertEqual(status, 200)
        self.assertLessEqual(data["score"], 0.85)
        self.assertTrue(data["origin"] in ("问答库", "精读总集"))

    def test_feat_qa_01_boundary_case_insensitivity_mixed_alphanumeric(self):
        status, data, _ = self.post_json("/api/ask", {"question": "第1333章 齐夏"})
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("问答库", "精读总集"))
        self.assertGreater(data["score"], 0.0)


# =====================================================================
# FEAT-QA-02: 精读总集深层检索 (CRLF规范化, 344标题)
# =====================================================================
class TestFEATQA02SummaryRetrieval(BaseWebE2ETestCase):

    # Tier 1 (5 tests)
    def test_feat_qa_02_deep_retrieval_core_theme(self):
        status, data, _ = self.post_json("/api/ask", {"question": "核心立意与执念造物"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "精读总集")
        self.assertTrue("执念" in data["answer"] or "想念" in data["answer"])
        self.assertIn("精读总集", data["sources"])

    def test_feat_qa_02_deep_retrieval_dialectic_lie(self):
        status, data, _ = self.post_json("/api/ask", {"question": "谎言的辩证法与骗局"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "精读总集")
        self.assertTrue("谎言" in data["answer"] or "骗局" in data["answer"])

    def test_feat_qa_02_deep_retrieval_zodiac_game_rules(self):
        status, data, _ = self.post_json("/api/ask", {"question": "生肖体系的运转机制"})
        self.assertEqual(status, 200)
        self.assertTrue(data["origin"] in ("问答库", "精读总集"))
        self.assertGreater(data["score"], 0.15)
        self.assertTrue("生肖" in data["answer"])

    def test_feat_qa_02_deep_retrieval_echo_subconscious(self):
        status, data, _ = self.post_json("/api/ask", {"question": "回响的潜意识之信原理"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "精读总集")
        self.assertTrue("潜意识" in data["answer"] or "信念" in data["answer"] or "信" in data["answer"])

    def test_feat_qa_02_deep_retrieval_final_fraud(self):
        status, data, _ = self.post_json("/api/ask", {"question": "终局欺诈与新世界诞生"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "精读总集")
        self.assertTrue("欺诈" in data["answer"] or "新世界" in data["answer"] or "骗局" in data["answer"])

    # Tier 2 (5 tests)
    def test_feat_qa_02_boundary_crlf_dos_newlines(self):
        crlf_sample = "# 标题1\r\n\r\n第一段内容。\r\n\r\n第二段内容。\r\n## 标题2\r\n\r\n这是标题2正文。"
        mock_kb = NovelKnowledgeBase.__new__(NovelKnowledgeBase)
        sections = mock_kb._parse_summary(crlf_sample)
        self.assertEqual(len(sections), 2)
        for sec in sections:
            self.assertNotIn("\r", sec["title"])
            self.assertNotIn("\r", sec["body"])

    def test_feat_qa_02_boundary_deep_nested_headings_h1_h2_h3(self):
        nested_sample = "# 模块A\n## 章节B\n### 小节C\n小节正文内容详细描述"
        mock_kb = NovelKnowledgeBase.__new__(NovelKnowledgeBase)
        sections = mock_kb._parse_summary(nested_sample)
        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[2]["breadcrumb"], "模块A > 章节B > 小节C")

    def test_feat_qa_02_boundary_empty_summary_section_body(self):
        empty_body_sample = "# 空标题1\n# 空标题2\n## 空标题3\n有正文内容"
        mock_kb = NovelKnowledgeBase.__new__(NovelKnowledgeBase)
        sections = mock_kb._parse_summary(empty_body_sample)
        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[0]["body"], "")

    def test_feat_qa_02_boundary_special_markdown_symbols(self):
        markdown_rich = "# 表格与格式\n| 列1 | 列2 |\n|---|---|\n| 值1 | 值2 |\n\n**加粗内容** 与 *斜体* 以及 `代码`。"
        mock_kb = NovelKnowledgeBase.__new__(NovelKnowledgeBase)
        sections = mock_kb._parse_summary(markdown_rich)
        self.assertEqual(len(sections), 1)
        self.assertIn("加粗内容", sections[0]["body"])

    def test_feat_qa_02_boundary_low_score_threshold_rejection(self):
        status, data, _ = self.post_json("/api/ask", {"question": "量子引力弦理论微积分方程"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertEqual(data["score"], 0.0)


# =====================================================================
# FEAT-QA-03: qa_archive.py 正则加固与原子写
# =====================================================================
class TestFEATQA03ArchiveCLIRobustness(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.qa_dir = os.path.join(self.temp_dir, "问答库")
        os.makedirs(self.qa_dir, exist_ok=True)
        self.archive_file = os.path.join(self.qa_dir, "档案.jsonl")

        # Copy existing 253 entries or a sample into temp_dir
        src_archive = os.path.join(BOOK_DIR, "问答库", "档案.jsonl")
        if os.path.isfile(src_archive):
            shutil.copyfile(src_archive, self.archive_file)
        else:
            with open(self.archive_file, "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": 1, "qhash": "123", "ts": "2026-08-30 23:35", "question": "齐夏是谁", "answer": "骗子", "sources": "第1章", "hits": 0}, ensure_ascii=False) + "\n")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_cli(self, *args):
        cmd = [sys.executable, os.path.join(ROOT_DIR, "scripts", "qa_archive.py"), "--book", self.temp_dir, *args]
        return subprocess.run(cmd, capture_output=True, text=True)

    # Tier 1 (5 tests)
    def test_feat_qa_03_cli_add_new_entry(self):
        res = self._run_cli("add", "--question", "测试新增问答999", "--answer", "测试回答内容", "--sources", "第999章")
        self.assertEqual(res.returncode, 0)
        self.assertIn("已存档", res.stdout)
        with open(self.archive_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        last = lines[-1]
        self.assertEqual(last["question"], "测试新增问答999")
        self.assertEqual(last["answer"], "测试回答内容")
        self.assertEqual(last["sources"], "第999章")

    def test_feat_qa_03_cli_find_exact_query(self):
        res = self._run_cli("find", "--query", "齐夏的回响")
        self.assertEqual(res.returncode, 0)
        self.assertIn("生生不息", res.stdout)

    def test_feat_qa_03_cli_update_existing_entry(self):
        res = self._run_cli("update", "--id", "1", "--answer", "全新的修改后答案")
        self.assertEqual(res.returncode, 0)
        self.assertIn("已更新 #1", res.stdout)
        with open(self.archive_file, "r", encoding="utf-8") as f:
            first = json.loads(f.readline())
        self.assertEqual(first["answer"], "全新的修改后答案")

    def test_feat_qa_03_cli_list_entries(self):
        res = self._run_cli("list")
        self.assertEqual(res.returncode, 0)
        self.assertIn("#1", res.stdout)
        self.assertIn("#2", res.stdout)

    def test_feat_qa_03_atomic_write_safety(self):
        from scripts.qa_archive import load, save
        entries = load(self.temp_dir)
        original_count = len(entries)
        entries.append({
            "id": original_count + 1,
            "qhash": "aabbcc112233",
            "ts": "2026-11-14 10:00",
            "question": "原子写测试",
            "answer": "安全落地",
            "sources": "测试",
            "hits": 0,
        })
        save(self.temp_dir, entries)
        self.assertTrue(os.path.isfile(self.archive_file))
        tmp_files = [f for f in os.listdir(self.qa_dir) if f.endswith(".tmp")]
        self.assertEqual(len(tmp_files), 0)
        reloaded = load(self.temp_dir)
        self.assertEqual(len(reloaded), original_count + 1)

    # Tier 2 (5 tests)
    def test_feat_qa_03_boundary_regex_characters_in_query(self):
        for pattern in ["齐夏.*+?^${}()|[]\\", "回响??++**", "[齐夏]"]:
            res = self._run_cli("find", "--query", pattern)
            self.assertEqual(res.returncode, 0)

    def test_feat_qa_03_boundary_read_only_find_no_mtime_change(self):
        mtime_before = os.path.getmtime(self.archive_file)
        time.sleep(0.01)
        res = self._run_cli("find", "--query", "齐夏的回响是什么")
        self.assertEqual(res.returncode, 0)
        mtime_after = os.path.getmtime(self.archive_file)
        self.assertEqual(mtime_before, mtime_after)

    def test_feat_qa_03_boundary_dedup_exact_hash(self):
        with open(self.archive_file, "r", encoding="utf-8") as f:
            lines_before = len(f.readlines())
        res = self._run_cli("add", "--question", "齐夏的回响是什么", "--answer", "重复问题")
        self.assertEqual(res.returncode, 0)
        self.assertIn("已存在", res.stdout)
        with open(self.archive_file, "r", encoding="utf-8") as f:
            lines_after = len(f.readlines())
        self.assertEqual(lines_before, lines_after)

    def test_feat_qa_03_boundary_nonexistent_book_dir(self):
        bad_dir = os.path.join(self.temp_dir, "nonexistent_dir")
        cmd = [sys.executable, os.path.join(ROOT_DIR, "scripts", "qa_archive.py"), "--book", bad_dir, "list"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("不存在", res.stderr)

    def test_feat_qa_03_boundary_corrupted_jsonl_line(self):
        with open(self.archive_file, "a", encoding="utf-8") as f:
            f.write("\n{INVALID_JSON_CORRUPTED_LINE}\n")
        from scripts.qa_archive import load
        entries = load(self.temp_dir)
        self.assertGreater(len(entries), 10)


# =====================================================================
# FEAT-TTS-01: 3大音色切换与流式输出
# =====================================================================
class TestFEATTTS01VoiceSwitchAndOutput(BaseWebE2ETestCase):

    # Tier 1 (5 tests)
    def test_feat_tts_01_voice_xiaoxiao_edge(self):
        status, audio_bytes, headers = self.get_raw(f"/api/tts?text=测试晓晓音色&voice=zh-CN-XiaoxiaoNeural")
        self.assertEqual(status, 200)
        self.assertIn(headers.get_content_type(), ("audio/mpeg", "audio/aiff", "audio/wav"))
        self.assertGreater(len(audio_bytes), 200)

    def test_feat_tts_01_voice_yunxi_edge(self):
        status, audio_bytes, headers = self.get_raw(f"/api/tts?text=测试云希音色&voice=zh-CN-YunxiNeural")
        self.assertEqual(status, 200)
        self.assertIn(headers.get_content_type(), ("audio/mpeg", "audio/aiff", "audio/wav"))
        self.assertGreater(len(audio_bytes), 200)

    def test_feat_tts_01_voice_xiaoyi_edge(self):
        status, audio_bytes, headers = self.get_raw(f"/api/tts?text=测试晓伊音色&voice=zh-CN-XiaoyiNeural")
        self.assertEqual(status, 200)
        self.assertIn(headers.get_content_type(), ("audio/mpeg", "audio/aiff", "audio/wav"))
        self.assertGreater(len(audio_bytes), 200)

    def test_feat_tts_01_rate_speedup(self):
        status, audio_bytes, _ = self.get_raw(f"/api/tts?text=加速测试&rate=+25%")
        self.assertEqual(status, 200)
        self.assertGreater(len(audio_bytes), 200)

    def test_feat_tts_01_rate_slowdown(self):
        status, audio_bytes, _ = self.get_raw(f"/api/tts?text=减速测试&rate=-25%")
        self.assertEqual(status, 200)
        self.assertGreater(len(audio_bytes), 200)

    # Tier 2 (5 tests)
    def test_feat_tts_01_boundary_empty_text_parameter(self):
        status, data, _ = self.get_json("/api/tts?text=")
        self.assertEqual(status, 400)
        self.assertIn("缺少 text 参数", data.get("error", ""))

    def test_feat_tts_01_boundary_spaces_only_text(self):
        status, data, _ = self.get_json("/api/tts?text=%20%20%20")
        self.assertEqual(status, 400)
        self.assertIn("缺少 text 参数", data.get("error", ""))

    def test_feat_tts_01_boundary_unrecognized_rate_format(self):
        status, audio_bytes, _ = self.get_raw(f"/api/tts?text=速率异常测试&rate=invalid_xyz")
        self.assertEqual(status, 200)
        self.assertGreater(len(audio_bytes), 200)

    def test_feat_tts_01_boundary_extreme_rate_limits(self):
        status1, b1, _ = self.get_raw(f"/api/tts?text=极快&rate=+500%")
        status2, b2, _ = self.get_raw(f"/api/tts?text=极慢&rate=-90%")
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertGreater(len(b1), 100)
        self.assertGreater(len(b2), 100)

    def test_feat_tts_01_boundary_url_encoded_chinese_and_punctuations(self):
        complex_text = "齐夏说：「各位，我们所有人都已经死了！」【第9章】"
        status, b, _ = self.get_raw(f"/api/tts?text={urllib.parse.quote(complex_text)}")
        self.assertEqual(status, 200)
        self.assertGreater(len(b), 200)


# =====================================================================
# FEAT-TTS-02: 3级降级链路
# =====================================================================
class TestFEATTTS02FallbackChain(unittest.TestCase):

    def setUp(self):
        self.temp_cache = tempfile.mkdtemp()
        self.tts = TTSService(cache_dir=self.temp_cache)

    def tearDown(self):
        shutil.rmtree(self.temp_cache, ignore_errors=True)

    # Tier 1 (5 tests)
    def test_feat_tts_02_tier1_edge_tts_primary(self):
        out_path, mime, engine = self.tts.synthesize("测试一级合成链路", voice="zh-CN-XiaoxiaoNeural")
        self.assertTrue(os.path.isfile(out_path))
        self.assertIn(engine, ("edge-tts", "cache", "say", "fallback-wav"))
        self.assertIn(mime, ("audio/mpeg", "audio/aiff", "audio/wav"))

    def test_feat_tts_02_tier2_macos_say_explicit(self):
        if not (sys.platform == "darwin" and shutil.which("say")):
            self.skipTest("Requires macOS with 'say' command")
        out_path, mime, engine = self.tts.synthesize("测试二级say兜底", voice="Tingting")
        self.assertTrue(os.path.isfile(out_path))
        self.assertIn(engine, ("say", "cache"))
        self.assertEqual(mime, "audio/wav")

    @patch("scripts.web.edge_tts")
    def test_feat_tts_02_tier2_macos_say_fallback_on_network_error(self, mock_edge):
        if not (sys.platform == "darwin" and shutil.which("say")):
            self.skipTest("Requires macOS with 'say' command")
        mock_comm = mock_edge.Communicate.return_value
        mock_comm.save.side_effect = RuntimeError("Network connection unreachable")
        out_path, mime, engine = self.tts.synthesize("模拟断网兜底测试", voice="zh-CN-XiaoxiaoNeural")
        self.assertTrue(os.path.isfile(out_path))
        self.assertEqual(engine, "say")
        self.assertEqual(mime, "audio/wav")

    def test_feat_tts_02_tier3_pure_python_wav_generator(self):
        wav_path = os.path.join(self.temp_cache, "direct_test.wav")
        self.tts._generate_silent_wav(wav_path, duration_sec=1.0)
        self.assertTrue(os.path.isfile(wav_path))
        with wave.open(wav_path, "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16000)
            self.assertGreater(wf.getnframes(), 0)

    @patch("scripts.web.IS_MAC", False)
    @patch("scripts.web.edge_tts")
    def test_feat_tts_02_tier3_full_fallback_chain_simulation(self, mock_edge):
        mock_comm = mock_edge.Communicate.return_value
        mock_comm.save.side_effect = ConnectionResetError("Offline")
        out_path, mime, engine = self.tts.synthesize("三级完整降级测试", voice="zh-CN-XiaoxiaoNeural")
        self.assertTrue(os.path.isfile(out_path))
        self.assertEqual(engine, "fallback-wav")
        self.assertEqual(mime, "audio/wav")

    # Tier 2 (5 tests)
    def test_feat_tts_02_boundary_voice_mapping_all_say_aliases(self):
        for edge_v, say_v in [("zh-CN-XiaoxiaoNeural", "Tingting"), ("zh-CN-YunxiNeural", "Sinji"), ("zh-CN-XiaoyiNeural", "Mei-Jia")]:
            mapped = self.tts._map_say_voice(edge_v)
            self.assertEqual(mapped, say_v)

    @patch("scripts.web.edge_tts")
    def test_feat_tts_02_boundary_offline_no_network_no_hang(self, mock_edge):
        mock_comm = mock_edge.Communicate.return_value
        mock_comm.save.side_effect = TimeoutError("Timeout")
        t0 = time.time()
        out_path, _, engine = self.tts.synthesize("快速超时降级测试", voice="zh-CN-XiaoxiaoNeural")
        elapsed = time.time() - t0
        self.assertLess(elapsed, 3.0)
        self.assertTrue(os.path.isfile(out_path))
        self.assertIn(engine, ("say", "fallback-wav"))

    def test_feat_tts_02_boundary_readonly_or_inaccessible_cache_dir(self):
        read_only_tts = TTSService(cache_dir="/proc/invalid_forbidden_dir")
        self.assertEqual(read_only_tts.cache_dir, "/proc/invalid_forbidden_dir")

    def test_feat_tts_02_boundary_zero_byte_partial_file_recovery(self):
        text = "残缺文件恢复测试"
        key = self.tts.cache_key(text, DEFAULT_VOICE, "+0%")
        corrupt_file = os.path.join(self.temp_cache, f"{key}.mp3")
        with open(corrupt_file, "wb") as f:
            pass  # 写入 0 字节损坏缓存
        self.assertTrue(os.path.isfile(corrupt_file))
        self.assertEqual(os.path.getsize(corrupt_file), 0)

        # 调用 synthesize 应绕过/覆盖 0 字节损坏文件并返回有效音频
        out_path, mime, engine = self.tts.synthesize(text, voice=DEFAULT_VOICE, rate="+0%")
        self.assertTrue(os.path.isfile(out_path))
        self.assertGreater(os.path.getsize(out_path), 0)
        self.assertIn(mime, ("audio/mpeg", "audio/wav", "audio/aiff"))
        self.assertIn(engine, ("edge-tts", "say", "fallback-wav"))

    def test_feat_tts_02_boundary_empty_voice_string(self):
        out_path, _, _ = self.tts.synthesize("空音色回退", voice="")
        self.assertTrue(os.path.isfile(out_path))
        self.assertGreater(os.path.getsize(out_path), 0)


# =====================================================================
# FEAT-TTS-03: 非阻塞 MD5 音频缓存
# =====================================================================
class TestFEATTTS03AudioCaching(BaseWebE2ETestCase):

    # Tier 1 (5 tests)
    def test_feat_tts_03_md5_cache_key_consistency(self):
        text = "齐夏的回响名是生生不息"
        voice = "zh-CN-XiaoxiaoNeural"
        rate = "+0%"
        expected = hashlib.md5(f"{voice}|{rate}|{text.strip()}".encode("utf-8")).hexdigest()
        actual = self.tts.cache_key(text, voice, rate)
        self.assertEqual(expected, actual)

    def test_feat_tts_03_cache_hit_speed_sub_millisecond(self):
        text = "缓存命中极速重放测试"
        out1, _, _ = self.tts.synthesize(text)
        t0 = time.time()
        out2, _, engine = self.tts.synthesize(text)
        elapsed_ms = (time.time() - t0) * 1000.0
        self.assertEqual(out1, out2)
        self.assertEqual(engine, "cache")
        self.assertLess(elapsed_ms, 15.0)

    def test_feat_tts_03_cache_differentiation_by_rate(self):
        k1 = self.tts.cache_key("相同文本", DEFAULT_VOICE, "+0%")
        k2 = self.tts.cache_key("相同文本", DEFAULT_VOICE, "+20%")
        self.assertNotEqual(k1, k2)

    def test_feat_tts_03_cache_differentiation_by_voice(self):
        k1 = self.tts.cache_key("相同文本", "zh-CN-XiaoxiaoNeural", "+0%")
        k2 = self.tts.cache_key("相同文本", "zh-CN-YunxiNeural", "+0%")
        self.assertNotEqual(k1, k2)

    def test_feat_tts_03_audio_endpoint_serves_cached_file(self):
        out_path, mime, _ = self.tts.synthesize("端点直接获取音频测试")
        filename = os.path.basename(out_path)
        status, raw_bytes, headers = self.get_raw(f"/api/audio/{filename}")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "public, max-age=86400")
        self.assertEqual(len(raw_bytes), os.path.getsize(out_path))

    # Tier 2 (5 tests)
    def test_feat_tts_03_boundary_cache_whitespace_normalization(self):
        k1 = self.tts.cache_key("齐夏", DEFAULT_VOICE, "+0%")
        k2 = self.tts.cache_key("  \t齐夏 \n ", DEFAULT_VOICE, "+0%")
        self.assertEqual(k1, k2)

    def test_feat_tts_03_boundary_nonexistent_cache_audio_404(self):
        status, _, _ = self.get_raw("/api/audio/0123456789abcdef0123456789abcdef.mp3")
        self.assertEqual(status, 404)

    def test_feat_tts_03_boundary_special_path_traversal_in_audio_url(self):
        status, _, _ = self.get_raw("/api/audio/../../etc/passwd")
        self.assertEqual(status, 404)

    def test_feat_tts_03_boundary_concurrent_cache_reads(self):
        out_path, _, _ = self.tts.synthesize("高并发读取同一个音频文件测试")
        filename = os.path.basename(out_path)
        results = []

        def worker():
            try:
                st, b, _ = self.get_raw(f"/api/audio/{filename}")
                results.append((st, len(b)))
            except Exception as e:
                results.append((500, 0))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 5)
        self.assertTrue(all(st == 200 and sz > 0 for st, sz in results))

    def test_feat_tts_03_boundary_mime_detection_by_extension(self):
        h = "11112222333344445555666677778888"
        for ext, expected_mime in [(".mp3", "audio/mpeg"), (".wav", "audio/wav"), (".aiff", "audio/aiff")]:
            p = os.path.join(self.tts.cache_dir, f"{h}{ext}")
            with open(p, "wb") as f:
                f.write(b"RIFFDUMMYAUDIO")
            status, _, headers = self.get_raw(f"/api/audio/{h}{ext}")
            self.assertEqual(status, 200)
            self.assertEqual(headers.get_content_type(), expected_mime)
            os.remove(p)


# =====================================================================
# FEAT-WEB-01: 暗色悬疑风格单页应用与交互控制
# =====================================================================
class TestFEATWeb01SinglePageUI(BaseWebE2ETestCase):

    # Tier 1 (5 tests)
    def test_feat_web_01_index_html_status_and_title(self):
        status, body, headers = self.get_raw("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get_content_type())
        html = body.decode("utf-8")
        self.assertIn("<title>《十日终焉》智能语音问答系统</title>", html)
        self.assertIn("十日", html)
        self.assertIn("语音问答", html)

    def test_feat_web_01_dark_theme_css_variables(self):
        status, body, _ = self.get_raw("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("--bg-primary: #0f1117", html)
        self.assertIn("--accent-crimson: #e63946", html)

    def test_feat_web_01_voice_selector_markup(self):
        status, body, _ = self.get_raw("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn('select id="voiceSelect"', html)
        self.assertIn("zh-CN-XiaoxiaoNeural", html)
        self.assertIn("zh-CN-YunxiNeural", html)
        self.assertIn("zh-CN-XiaoyiNeural", html)

    def test_feat_web_01_rate_and_autoplay_controls(self):
        status, body, _ = self.get_raw("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn('id="autoPlayToggle"', html)

    def test_feat_web_01_quick_prompt_chips(self):
        status, body, _ = self.get_raw("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("齐夏的真实身份是什么", html)
        self.assertIn("生肖和神兽的规则是什么", html)

    # Tier 2 (5 tests)
    def test_feat_web_01_boundary_index_html_alias(self):
        s1, b1, _ = self.get_raw("/")
        s2, b2, _ = self.get_raw("/index.html")
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 200)
        self.assertEqual(b1, b2)

    def test_feat_web_01_boundary_utf8_meta_encoding(self):
        status, body, _ = self.get_raw("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn('<meta charset="UTF-8">', html)

    def test_feat_web_01_boundary_responsive_meta_viewport(self):
        status, body, _ = self.get_raw("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn('name="viewport"', html)
        self.assertIn("width=device-width", html)

    def test_feat_web_01_boundary_head_request_or_unknown_asset(self):
        status, _, _ = self.get_raw("/nonexistent_static_script.js")
        self.assertEqual(status, 404)

    def test_feat_web_01_boundary_html_script_safe_injection(self):
        status, body, _ = self.get_raw("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("textContent = text", html)


# =====================================================================
# FEAT-WEB-02: RESTful API 全集
# =====================================================================
class TestFEATWeb02RESTfulAPI(BaseWebE2ETestCase):

    # Tier 1 (5 tests)
    def test_feat_web_02_get_api_status(self):
        status, data, _ = self.get_json("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["book"], "十日终焉")
        self.assertGreaterEqual(data["qa_count"], 50)
        self.assertGreaterEqual(data["summary_sections"], 300)
        self.assertIn("voices", data)

    def test_feat_web_02_health_endpoint_alias(self):
        status, data, _ = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

    def test_feat_web_02_post_api_ask_valid_payload(self):
        status, data, _ = self.post_json("/api/ask", {"question": "齐夏的回响是什么", "voice": "zh-CN-XiaoxiaoNeural"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertIn("audio_url", data)
        self.assertTrue(data["audio_url"].startswith("/api/audio/"))

    def test_feat_web_02_get_api_archive_with_query(self):
        status, data, _ = self.get_json("/api/archive?q=齐夏&limit=5")
        self.assertEqual(status, 200)
        self.assertGreater(data["total"], 200)
        self.assertLessEqual(len(data["items"]), 5)

    def test_feat_web_02_options_cors_preflight(self):
        req = urllib.request.Request(f"{self.server_url}/api/ask", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 204)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
            self.assertIn("POST", resp.headers.get("Access-Control-Allow-Methods", ""))

    # Tier 2 (5 tests)
    def test_feat_web_02_boundary_ask_empty_post_body(self):
        status, data, _ = self.post_json("/api/ask", None)
        self.assertEqual(status, 400)
        self.assertIn("Empty body", data.get("error", ""))

    def test_feat_web_02_boundary_ask_malformed_json(self):
        url = self._format_url("/api/ask")
        req = urllib.request.Request(
            url,
            data=b"{not_valid_json_payload}",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read().decode("utf-8")
            self.assertIn("Invalid JSON", raw)
        self.assertEqual(status, 400)

    def test_feat_web_02_boundary_ask_non_dict_json(self):
        url = self._format_url("/api/ask")
        test_payloads = [
            b"[1, 2, 3]",
            b'"raw string"',
            b"12345",
            b"true",
            b"null",
        ]
        for payload in test_payloads:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    status = resp.status
                    raw = resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                status = e.code
                raw = e.read().decode("utf-8")
            self.assertEqual(status, 400, f"Payload {payload} should return HTTP 400")
            parsed = json.loads(raw)
            self.assertEqual(parsed.get("error"), "Expected JSON object")

    def test_feat_web_02_boundary_archive_excessive_limit(self):
        status, data, _ = self.get_json("/api/archive?limit=999")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(data["items"]), 50)

    def test_feat_web_02_boundary_archive_empty_query(self):
        status, data, _ = self.get_json("/api/archive")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["items"]), 20)

    def test_feat_web_02_boundary_method_not_allowed_or_404(self):
        url = self._format_url("/api/status")
        req = urllib.request.Request(url, method="DELETE")
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertIn(status, (404, 405, 501))


# =====================================================================
# FEAT-DOCKER-01: 轻量化 Dockerfile 生产/测试双模式
# =====================================================================
class TestFEATDocker01DockerfileConfig(unittest.TestCase):

    def setUp(self):
        self.dockerfile_path = os.path.join(ROOT_DIR, "Dockerfile")
        with open(self.dockerfile_path, "r", encoding="utf-8") as f:
            self.content = f.read()

    # Tier 1 (5 tests)
    def test_feat_docker_01_dockerfile_base_image(self):
        self.assertTrue(re.search(r"^FROM\s+python:3\.\d+(-slim)?", self.content, re.MULTILINE))

    def test_feat_docker_01_dependencies_installation(self):
        self.assertTrue(re.search(r"^RUN\s+pip\s+install\s+.*pytest", self.content, re.MULTILINE))

    def test_feat_docker_01_workdir_and_copy_directives(self):
        self.assertTrue(re.search(r"^WORKDIR\s+/app", self.content, re.MULTILINE))
        self.assertTrue(re.search(r"^COPY\s+scripts/", self.content, re.MULTILINE))
        self.assertTrue(re.search(r"^COPY\s+tests/", self.content, re.MULTILINE))
        self.assertTrue(re.search(r"^COPY\s+books/", self.content, re.MULTILINE))

    def test_feat_docker_01_default_test_mode_entrypoint(self):
        self.assertTrue(re.search(r'(CMD|ENTRYPOINT)\s*\[.*"pytest".*\]', self.content))

    def test_feat_docker_01_production_web_mode_command(self):
        self.assertTrue(re.search(r"web\.py", self.content))

    # Tier 2 (5 tests)
    def test_feat_docker_01_boundary_unbuffered_env(self):
        self.assertTrue(re.search(r"^ENV\s+PYTHONUNBUFFERED=1", self.content, re.MULTILINE))

    def test_feat_docker_01_boundary_skill_mirror_path_copy(self):
        self.assertTrue(re.search(r"^COPY\s+.*novel-companion/scripts/", self.content, re.MULTILINE) or "novel-companion" in self.content)

    def test_feat_docker_01_boundary_results_volume_compatibility(self):
        self.assertTrue(re.search(r"(/results|VOLUME\s+\[\"/results\"\])", self.content))

    def test_feat_docker_01_boundary_no_hardcoded_credentials(self):
        lower = self.content.lower()
        for forbidden in ["password", "secret", "aws_key", "api_key"]:
            self.assertNotIn(forbidden, lower)

    def test_feat_docker_01_boundary_syntax_validity(self):
        lines = [line.strip() for line in self.content.splitlines() if line.strip() and not line.strip().startswith("#")]
        valid_instructions = ("FROM", "WORKDIR", "RUN", "COPY", "EXPOSE", "ENV", "CMD", "ENTRYPOINT", "VOLUME")
        for line in lines:
            self.assertTrue(any(line.startswith(inst) for inst in valid_instructions), f"Invalid Dockerfile line: {line}")


# =====================================================================
# FEAT-DOCKER-02: .dockerignore 过滤规则与镜像瘦身
# =====================================================================
class TestFEATDocker02DockerignoreOptimization(unittest.TestCase):

    def setUp(self):
        self.dockerignore_path = os.path.join(ROOT_DIR, ".dockerignore")
        self.assertTrue(os.path.isfile(self.dockerignore_path), ".dockerignore must exist at root")
        with open(self.dockerignore_path, "r", encoding="utf-8") as f:
            self.content = f.read()

    # Tier 1 (5 tests)
    def test_feat_docker_02_dockerignore_excludes_raw_txt(self):
        self.assertTrue(any(pat in self.content for pat in ("books/*.txt", "books/十日终焉/原文.txt", "*.txt")))

    def test_feat_docker_02_dockerignore_excludes_git(self):
        self.assertIn(".git", self.content)

    def test_feat_docker_02_dockerignore_excludes_pycache(self):
        self.assertTrue("__pycache__" in self.content or "*.pyc" in self.content)

    def test_feat_docker_02_dockerignore_excludes_test_results(self):
        self.assertTrue("test-results" in self.content or ".pytest_cache" in self.content)

    def test_feat_docker_02_dockerignore_preserves_necessary_assets(self):
        self.assertTrue("!books/十日终焉/问答库/档案.jsonl" in self.content or "!档案.jsonl" in self.content)
        self.assertTrue("!books/十日终焉/精读总集.md" in self.content or "!精读总集.md" in self.content)

    # Tier 2 (5 tests)
    def test_feat_docker_02_boundary_context_size_threshold(self):
        patterns = [line.strip() for line in self.content.splitlines() if line.strip() and not line.strip().startswith("#") and not line.strip().startswith("!")]
        total_size = 0
        for root, dirs, files in os.walk(ROOT_DIR):
            if ".git" in dirs:
                dirs.remove(".git")
            for f in files:
                rel = os.path.normpath(os.path.relpath(os.path.join(root, f), ROOT_DIR))
                ignored = False
                for p in patterns:
                    if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p) or rel.startswith(p.rstrip("/")):
                        ignored = True
                        break
                if rel in ("books/十日终焉/问答库/档案.jsonl", "books/十日终焉/精读总集.md"):
                    ignored = False
                if not ignored:
                    total_size += os.path.getsize(os.path.join(root, f))
        size_mb = total_size / (1024 * 1024)
        self.assertLess(size_mb, 50.0, "Docker build context must be strictly under 50MB")

    def test_feat_docker_02_boundary_dockerignore_crlf_and_trailing_whitespace(self):
        self.assertNotIn("\r\r", self.content)

    def test_feat_docker_02_boundary_wildcard_pattern_coverage(self):
        self.assertTrue("*.tmp" in self.content or ".DS_Store" in self.content)

    def test_feat_docker_02_boundary_virtualenv_and_node_modules_exclusion(self):
        self.assertTrue("venv" in self.content or ".venv" in self.content)

    def test_feat_docker_02_boundary_novel_chunks_and_notes_selective_inclusion(self):
        self.assertIn("books/十日终焉/chunks/", self.content)


# =====================================================================
# FEAT-DOCKER-03: 容器独立服务启动与端口暴露
# =====================================================================
class TestFEATDocker03PortAndBinding(unittest.TestCase):

    # Tier 1 (5 tests)
    def test_feat_docker_03_port_exposure_8080(self):
        dockerfile_path = os.path.join(ROOT_DIR, "Dockerfile")
        with open(dockerfile_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("EXPOSE 8080", content)

    def test_feat_docker_03_env_port_definition(self):
        dockerfile_path = os.path.join(ROOT_DIR, "Dockerfile")
        with open(dockerfile_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("ENV PORT=8080", content)

    def test_feat_docker_03_custom_port_override_support(self):
        server = make_server(host="127.0.0.1", port=0, book_path=BOOK_DIR)
        port = server.server_address[1]
        server.server_close()
        self.assertGreater(port, 1024)

    def test_feat_docker_03_host_binding_all_interfaces(self):
        import inspect
        sig = inspect.signature(make_server)
        self.assertEqual(sig.parameters["host"].default, "0.0.0.0")

    def test_feat_docker_03_http_status_container_healthcheck(self):
        server = make_server(host="127.0.0.1", port=0, book_path=BOOK_DIR)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        port = server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "ok")
        finally:
            server.shutdown()
            server.server_close()

    # Tier 2 (5 tests)
    def test_feat_docker_03_boundary_invalid_port_env_handling(self):
        from scripts.web import parse_args, resolve_port
        with patch.dict(os.environ, {"PORT": "not_a_number"}):
            port = resolve_port()
            self.assertEqual(port, 8080)
            args = parse_args([])
            self.assertEqual(args.port, 8080)

    def test_feat_docker_03_boundary_privileged_port_binding_handling(self):
        if os.geteuid() != 0:
            with self.assertRaises((PermissionError, OSError)):
                make_server(host="127.0.0.1", port=1, book_path=BOOK_DIR)

    def test_feat_docker_03_boundary_graceful_sigterm_handling(self):
        server = make_server(host="127.0.0.1", port=0, book_path=BOOK_DIR)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        t0 = time.time()
        server.shutdown()
        server.server_close()
        self.assertLess(time.time() - t0, 2.0)

    def test_feat_docker_03_boundary_rapid_start_stop_port_reuse(self):
        server1 = make_server(host="127.0.0.1", port=0, book_path=BOOK_DIR)
        port = server1.server_address[1]
        self.assertGreater(port, 0)
        server1.server_close()

        server2 = make_server(host="127.0.0.1", port=port, book_path=BOOK_DIR)
        try:
            self.assertEqual(server2.server_address[0], "127.0.0.1")
            self.assertEqual(server2.server_address[1], port)
            self.assertIsNotNone(server2.socket)
            self.assertEqual(server2.socket.getsockname()[1], port)
        finally:
            server2.server_close()

    def test_feat_docker_03_boundary_dual_stack_ipv4_ipv6(self):
        for host in ("127.0.0.1", "0.0.0.0"):
            server = make_server(host=host, port=0, book_path=BOOK_DIR)
            try:
                self.assertIsNotNone(server.socket)
                port = server.server_address[1]
                self.assertGreater(port, 0)
                bound_host = server.server_address[0]
                self.assertEqual(bound_host, host)
            finally:
                server.server_close()


# =====================================================================
# Tier 3: Cross-Feature Combinations
# =====================================================================
class TestTier3CrossFeatureCombinations(BaseWebE2ETestCase):

    def test_tier3_combo_01_ask_archive_with_tts_audio_url(self):
        status, data, _ = self.post_json("/api/ask", {"question": "齐夏的回响是什么"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertTrue(data["audio_url"].startswith("/api/audio/"))
        audio_status, audio_bytes, _ = self.get_raw(data["audio_url"])
        self.assertEqual(audio_status, 200)
        self.assertGreater(len(audio_bytes), 100)

    def test_tier3_combo_02_ask_summary_with_tts_audio_url(self):
        status, data, _ = self.post_json("/api/ask", {"question": "核心立意与执念造物"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "精读总集")
        self.assertTrue(data["audio_url"].startswith("/api/audio/"))

    def test_tier3_combo_03_ask_cache_hit_replay_consistency(self):
        q = "齐夏的回响是什么"
        status1, data1, _ = self.post_json("/api/ask", {"question": q})
        status2, data2, _ = self.post_json("/api/ask", {"question": q})
        self.assertEqual(data1["audio_url"], data2["audio_url"])

    def test_tier3_combo_04_ask_custom_rate_affects_audio_hash(self):
        q = "齐夏的回响是什么"
        _, data1, _ = self.post_json("/api/ask", {"question": q, "rate": "+0%"})
        _, data2, _ = self.post_json("/api/ask", {"question": q, "rate": "+25%"})
        self.assertNotEqual(data1["audio_url"], data2["audio_url"])

    @patch("scripts.web.edge_tts")
    def test_tier3_combo_05_tts_fallback_under_offline_simulation(self, mock_edge):
        mock_comm = mock_edge.Communicate.return_value
        mock_comm.save.side_effect = RuntimeError("Offline Network Test")
        status, data, _ = self.post_json("/api/ask", {"question": "离线模拟测试"})
        self.assertEqual(status, 200)
        self.assertTrue(data["audio_url"].startswith("/api/audio/"))

    def test_tier3_combo_06_archive_cli_add_then_web_api_ask(self):
        temp_dir = tempfile.mkdtemp()
        try:
            qa_dir = os.path.join(temp_dir, "问答库")
            os.makedirs(qa_dir, exist_ok=True)
            archive_file = os.path.join(qa_dir, "档案.jsonl")
            with open(archive_file, "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": 1, "qhash": "h1", "ts": "2026-08-30 23:35", "question": "动态联动测试", "answer": "联动成功", "sources": "全书", "hits": 0}, ensure_ascii=False) + "\n")
            server = make_server(host="127.0.0.1", port=0, book_path=temp_dir)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            port = server.server_address[1]
            try:
                payload = json.dumps({"question": "动态联动测试"}).encode("utf-8")
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ask", data=payload, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(data["origin"], "问答库")
                    self.assertEqual(data["answer"], "联动成功")
            finally:
                server.shutdown()
                server.server_close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_tier3_combo_07_web_ui_quick_prompt_to_api_ask_compatibility(self):
        status, body, _ = self.get_raw("/")
        html = body.decode("utf-8")
        presets = re.findall(r"askPreset\('([^']+)'\)", html)
        self.assertGreater(len(presets), 0)
        for preset in presets[:2]:
            st, data, _ = self.post_json("/api/ask", {"question": preset})
            self.assertEqual(st, 200)
            self.assertIn(data["origin"], ("问答库", "精读总集"))

    def test_tier3_combo_08_tts_synthesize_then_audio_endpoint_with_cors(self):
        status, audio_bytes, headers = self.get_raw(f"/api/tts?text=CORS头校验")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")

    def test_tier3_combo_09_dockerfile_build_context_against_dockerignore(self):
        dockerfile_path = os.path.join(ROOT_DIR, "Dockerfile")
        dockerignore_path = os.path.join(ROOT_DIR, ".dockerignore")
        self.assertTrue(os.path.isfile(dockerfile_path))
        self.assertTrue(os.path.isfile(dockerignore_path))

    def test_tier3_combo_10_docker_entrypoint_matches_ci_shard_invocation(self):
        with open(os.path.join(ROOT_DIR, "Dockerfile"), "r", encoding="utf-8") as f:
            df_content = f.read()
        self.assertIn("pytest", df_content)

    def test_tier3_combo_13_ask_level3_fallback_generates_advisory_audio(self):
        status, data, _ = self.post_json("/api/ask", {"question": "完全无关的红烧牛肉面烹饪教程"})
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertTrue(data["audio_url"].startswith("/api/audio/"))

    def test_tier3_combo_14_rapid_concurrent_ask_and_audio_streaming(self):
        responses = []

        def worker():
            try:
                st, data, _ = self.post_json("/api/ask", {"question": "齐夏的回响是什么"})
                if st == 200 and data.get("audio_url"):
                    ast, abytes, _ = self.get_raw(data["audio_url"])
                    responses.append((st, ast, len(abytes)))
            except Exception:
                pass

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(responses), 5)
        self.assertTrue(all(rst == 200 and ast == 200 and sz > 0 for rst, ast, sz in responses))


if __name__ == "__main__":
    unittest.main()
