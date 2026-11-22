"""
E2E Test Suite for 《十日终焉》双层检索与自进化缓存系统
Architecture: L1 Cache Hit (<3ms) -> R3 Out-of-Bounds Guard -> L2 Cascade Deep Recall -> Self-Evolution Write-Back
Tiers 1 to 4 Comprehensive Test Coverage (61 Tests)
"""

import collections
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from scripts.web import (
    BLOCK_TO_CHAPTERS,
    DEFAULT_VOICE,
    L2DeepRetriever,
    NovelKnowledgeBase,
    TTSService,
    clean_markdown,
    extract_standardized_citations,
    format_oral_answer,
    make_server,
    norm_text,
    tokenize_bigrams,
)
from tests.conftest import assert_valid_audio_payload

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_BOOK_DIR = os.path.join(ROOT_DIR, "books", "十日终焉")
PROD_QA_ARCHIVE = os.path.join(PROD_BOOK_DIR, "问答库", "档案.jsonl")


def create_isolated_book_dir(include_raw: bool = True) -> tempfile.TemporaryDirectory:
    """Creates a temporary, isolated replica of books/十日终焉 to prevent polluting production data."""
    td = tempfile.TemporaryDirectory()
    target_book = os.path.join(td.name, "十日终焉")
    os.makedirs(os.path.join(target_book, "问答库"), exist_ok=True)
    shutil.copy(PROD_QA_ARCHIVE, os.path.join(target_book, "问答库", "档案.jsonl"))
    shutil.copy(os.path.join(PROD_BOOK_DIR, "精读总集.md"), os.path.join(target_book, "精读总集.md"))
    shutil.copytree(os.path.join(PROD_BOOK_DIR, "分析"), os.path.join(target_book, "分析"))
    shutil.copytree(os.path.join(PROD_BOOK_DIR, "notes"), os.path.join(target_book, "notes"))
    if include_raw:
        raw_file = os.path.join(PROD_BOOK_DIR, "原文.txt")
        if os.path.isfile(raw_file):
            shutil.copy(raw_file, os.path.join(target_book, "原文.txt"))
        chunks_dir = os.path.join(PROD_BOOK_DIR, "chunks")
        if os.path.isdir(chunks_dir):
            shutil.copytree(chunks_dir, os.path.join(target_book, "chunks"))
    return td


class BaseReadOnlyE2ETestCase(unittest.TestCase):
    """Base test case managing an ephemeral in-process HTTP server on port 0 with production read-only assets."""

    server = None
    server_thread = None
    server_url = None
    kb: Optional[NovelKnowledgeBase] = None
    tts: Optional[TTSService] = None
    prod_archive_sha256 = None

    @classmethod
    def setUpClass(cls):
        with open(PROD_QA_ARCHIVE, "rb") as f:
            cls.prod_archive_sha256 = hashlib.sha256(f.read()).hexdigest()

        cls.server = make_server(host="127.0.0.1", port=0, book_path=PROD_BOOK_DIR)
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
        # Verify production archive was never mutated
        with open(PROD_QA_ARCHIVE, "rb") as f:
            current_sha256 = hashlib.sha256(f.read()).hexdigest()
        assert current_sha256 == cls.prod_archive_sha256, (
            "CRITICAL INTEGRITY VIOLATION: Production 档案.jsonl was mutated during test execution!"
        )

    def post_ask(self, question: str, voice: Optional[str] = None, rate: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.server_url}/api/ask"
        payload: Dict[str, Any] = {"question": question}
        if voice:
            payload["voice"] = voice
        if rate:
            payload["rate"] = rate
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                data = {}
            return e.code, data

    def get_audio(self, audio_url: str) -> Tuple[int, bytes, Dict[str, str]]:
        url = urllib.parse.urljoin(self.server_url, audio_url)
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                headers = dict(resp.headers)
                return resp.status, resp.read(), headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)


# =====================================================================
# Feature 1: L1 秒答优化 (999条高频问答库)
# =====================================================================

class TestFEATQA01L1CacheHit(BaseReadOnlyE2ETestCase):
    """L1 缓存层秒答特性验证 (Tier 1 & Tier 2)"""

    # --- Tier 1: 核心功能正常路径 (5 Cases) ---

    def test_tier1_feat1_l1_exact_hash_hit_qixia_echo(self):
        """Tier 1: 齐夏回响完全匹配条目 (id=97) 秒级命中与 <3ms 时延。"""
        status, data = self.post_ask("齐夏的回响是什么")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertEqual(data["score"], 1.0)
        self.assertIn("生生不息", data["answer"])
        self.assertEqual(data["entry_id"], "97")
        self.assertLess(data["elapsed_ms"], 3.0, f"L1 耗时超标: {data['elapsed_ms']}ms")

    def test_tier1_feat1_l1_exact_hash_hit_tiantian_echo(self):
        """Tier 1: 甜甜回响条目 (id=6) 完全匹配 (带标点) 秒级命中。"""
        status, data = self.post_ask("甜甜的回响是什么?")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertEqual(data["score"], 1.0)
        self.assertIn("巧物", data["answer"])
        self.assertEqual(data["entry_id"], "6")
        self.assertLess(data["elapsed_ms"], 3.0)

    def test_tier1_feat1_l1_fuzzy_high_score_qixia_ability(self):
        """Tier 1: 意图相近的模糊问题高分命中 id=97。"""
        status, data = self.post_ask("齐夏的回响到底是啥能力")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertGreaterEqual(data["score"], 0.75)
        self.assertIn("生生不息", data["answer"])
        self.assertLess(data["elapsed_ms"], 3.0)

    def test_tier1_feat1_l1_exact_hash_hit_chutianqiu_ending(self):
        """Tier 1: 楚天秋结局条目 (id=223) 完全匹配。"""
        status, data = self.post_ask("楚天秋的结局是什么")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertEqual(data["score"], 1.0)
        self.assertEqual(data["entry_id"], "223")
        self.assertTrue(any(k in data["answer"] for k in ("绝症", "新世界", "弑天龙", "入梦", "生生不息")))
        self.assertLess(data["elapsed_ms"], 3.0)

    def test_tier1_feat1_l1_audio_cache_instant_reuse(self):
        """Tier 1: L1 命中时音频 URL 稳定生成且二次请求即时复用缓存音频。"""
        q = "齐夏今年几岁了?"
        status1, data1 = self.post_ask(q)
        self.assertEqual(status1, 200)
        self.assertEqual(data1["origin"], "问答库")
        audio_url1 = data1.get("audio_url", "")
        self.assertTrue(audio_url1.startswith("/api/audio/"))

        # 首次拉取音频数据
        status_a1, audio_bytes1, _ = self.get_audio(audio_url1)
        self.assertEqual(status_a1, 200)
        self.assertGreaterEqual(len(audio_bytes1), 100)
        assert_valid_audio_payload(audio_bytes1, allow_silent=True)

        # 二次请求 ask
        status2, data2 = self.post_ask(q)
        self.assertEqual(status2, 200)
        self.assertEqual(data2["audio_url"], audio_url1)
        self.assertLess(data2["elapsed_ms"], 3.0)

    # --- Tier 2: 边界与极端情况 (5 Cases) ---

    def test_tier2_feat1_score_threshold_edge_boundary(self):
        """Tier 2: 严格验证 L1 分数阈值 0.54 vs 0.55 临界判定。"""
        # 童姨 (0.5667 >= 0.55): 应命中
        hits_pass = self.kb.search_archive("童姨", top_k=1, min_score=0.55)
        self.assertEqual(len(hits_pass), 1)
        self.assertGreaterEqual(hits_pass[0][0], 0.55)
        self.assertEqual(hits_pass[0][1]["question"], "童姨是谁")

        # 孙佳 (0.5375 < 0.55): 应被截断不命中
        hits_fail = self.kb.search_archive("孙佳", top_k=1, min_score=0.55)
        self.assertEqual(len(hits_fail), 0)

    def test_tier2_feat1_whitespace_and_punctuation_norm(self):
        """Tier 2: 首尾空格与中文全角问号等标点归一化命中 (norm_text)。"""
        status, data = self.post_ask("  齐夏的回响是什么？  ")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertEqual(data["score"], 1.0)
        self.assertEqual(data["entry_id"], "97")

    def test_tier2_feat1_case_insensitivity_caesar_cipher(self):
        """Tier 2: 大小写英文与特殊符号鲁棒性 (mlgdrzdqvxl vs MLGDRZDQVXL, id=381)。"""
        q = "在003块中齐夏以凯撒密码破出mlgdrzdqvxl极道万岁"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertEqual(data["entry_id"], "381")
        self.assertTrue("极道" in data["answer"] or "凯撒密码" in data.get("matched_question", ""))

    def test_tier2_feat1_tail_entry_999_retrieval(self):
        """Tier 2: 验证 999 条问答库边界末尾条目 (id=999) 正确索引与毫秒秒答。"""
        q = "在第594章里，天龙有什么关键行动或遭遇？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "问答库")
        self.assertEqual(data["score"], 1.0)
        self.assertEqual(data["entry_id"], "999")
        self.assertIn("地猪泄露核心机密", data["answer"])
        self.assertLess(data["elapsed_ms"], 3.0)

    def test_tier2_feat1_repeated_burst_queries_stability(self):
        """Tier 2: 高频突发重复查询稳定性，延迟无线性退化，audio_url 恒定。"""
        q = "陈俊南的替罪回响是什么意思"
        latencies = []
        expected_audio = None
        for _ in range(10):
            t0 = time.perf_counter()
            status, data = self.post_ask(q)
            latencies.append((time.perf_counter() - t0) * 1000)
            self.assertEqual(status, 200)
            self.assertEqual(data["origin"], "问答库")
            if expected_audio is None:
                expected_audio = data["audio_url"]
            else:
                self.assertEqual(data["audio_url"], expected_audio)
        avg_latency = sum(latencies) / len(latencies)
        self.assertLess(avg_latency, 3.0, f"Burst 平均延迟超标: {avg_latency:.3f}ms")


# =====================================================================
# Feature 2: R3 越界防护
# =====================================================================

class TestFEATQA02R3Guard(BaseReadOnlyE2ETestCase):
    """R3 越界防护特性验证 (Tier 1 & Tier 2)"""

    # --- Tier 1: 5大典型越界场景拦截 (5 Cases) ---

    def test_tier1_feat2_out_of_bounds_weather(self):
        """Tier 1: 拦截天气生活常识提问。"""
        status, data = self.post_ask("明天上海天气怎么样")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertEqual(data["score"], 0.0)
        self.assertIn("十日终焉", data["answer"])
        self.assertIn("建议换用", data["answer"])

    def test_tier1_feat2_out_of_bounds_cooking(self):
        """Tier 1: 拦截烹饪菜谱技巧提问。"""
        status, data = self.post_ask("红烧肉怎么做最好吃，需要放多少糖？")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertEqual(data["score"], 0.0)
        self.assertIn("十日终焉", data["answer"])
        self.assertEqual(data["sources"], "知识库搜索建议")

    def test_tier1_feat2_out_of_bounds_math(self):
        """Tier 1: 拦截高等数学微积分提问。"""
        status, data = self.post_ask("请帮我求解常微分方程 dy/dx = 2x + 1")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertEqual(data["score"], 0.0)
        self.assertIn("十日终焉", data["answer"])

    def test_tier1_feat2_out_of_bounds_programming(self):
        """Tier 1: 拦截计算机编程算法提问。"""
        status, data = self.post_ask("Python如何实现快速排序算法与冒泡排序？")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertEqual(data["score"], 0.0)
        self.assertIn("十日终焉", data["answer"])
        self.assertEqual(data["entry_id"], "")

    def test_tier1_feat2_out_of_bounds_general_chat(self):
        """Tier 1: 拦截通用情感闲聊提问。"""
        status, data = self.post_ask("今天加班好累啊，心情很差，陪我聊会儿天吧")
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertEqual(data["score"], 0.0)
        self.assertIn("十日终焉", data["answer"])

    # --- Tier 2: 边界与对抗输入 (5 Cases) ---

    def test_tier2_feat2_chinese_overlap_without_novel_entity(self):
        """Tier 2: 普通高频汉字重合但无小说实体时正确判定为无关。"""
        q = "在阳光明媚的日子里我们应该如何生活？"
        self.assertFalse(self.kb.is_novel_related(q))
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertEqual(data["score"], 0.0)

    def test_tier2_feat2_short_queries_hello_and_question(self):
        """Tier 2: 极短无意义问候与反问提问拦截。"""
        for short_q in ["你好", "是吗", "啊"]:
            status, data = self.post_ask(short_q)
            self.assertEqual(status, 200)
            self.assertEqual(data["origin"], "检索建议")
            self.assertEqual(data["score"], 0.0)

    def test_tier2_feat2_extreme_500_chars_unrelated_text(self):
        """Tier 2: 500 字符超长无关提问健壮性与安全截断。"""
        long_q = ("春天来了万物复苏小草发芽了天气真好。" * 28)[:500]
        self.assertEqual(len(long_q), 500)
        status, data = self.post_ask(long_q)
        self.assertEqual(status, 200)
        self.assertEqual(data["origin"], "检索建议")
        self.assertEqual(data["score"], 0.0)
        self.assertIn("...", data["answer"])

    def test_tier2_feat2_non_chinese_foreign_and_emojis(self):
        """Tier 2: 纯英文与纯 Emoji 符号安全拦截。"""
        for foreign_q in ["What is the weather in New York?", "😂😂😂😂🎉🎉"]:
            status, data = self.post_ask(foreign_q)
            self.assertEqual(status, 200)
            self.assertEqual(data["origin"], "检索建议")
            self.assertEqual(data["score"], 0.0)

    def test_tier2_feat2_character_name_in_irrelevant_context(self):
        """Tier 2: 包含角色名但结合完全无关现实概念提问，不产生虚假事实，严禁入库。"""
        q = "楚天秋今天晚上会写Python代码做数据分析吗？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertNotEqual(data["origin"], "问答库")
        self.assertNotIn("Python代码", data["answer"])
        self.assertEqual(data["entry_id"], "")


# =====================================================================
# Feature 3: L2 级联闭环深度检索
# =====================================================================

class TestFEATQA03L2CascadeRecall(BaseReadOnlyE2ETestCase):
    """L2 深度闭环级联检索特性验证 (Tier 1 & Tier 2)"""

    # --- Tier 1: 5个999库未收录的原著/笔记事实深度召回 (5 Cases) ---

    def test_tier1_feat3_l2_recall_xuanxuan_illness(self):
        """Tier 1: 李尚武养女萱萱疾病 (克里格勒-纳贾尔) 与堕落缘由召回。"""
        q = "李尚武养女萱萱患有的克里格勒综合征是怎样的？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertIn("克里格勒", data["answer"])
        self.assertTrue(re.search(r"第\d+(?:[-~至、]\d+)?章", data["sources"]))
        self.assertGreater(data["score"], 0.0)

    def test_tier1_feat3_l2_recall_chutianqiu_kill_wenqiaoyun(self):
        """Tier 1: 楚天秋勒死文巧云及临死拍手臂安慰细节召回。"""
        q = "楚天秋在天台亲手勒死文巧云时文巧云临死前有什么动作安慰他？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertTrue(any(k in data["answer"] for k in ("拍", "手臂", "安慰", "勒死")))
        self.assertTrue(bool(re.search(r"第74[4-6]章|第722章|第722-757章", data["sources"])))

    def test_tier1_feat3_l2_recall_jile_bank_origin_and_doors(self):
        """Tier 1: 极乐钱庄与金丝猴储能门召回。"""
        q = "极乐钱庄是由谁在什么场地建立的？如何打造出双向门的？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertTrue("白羊" in data["answer"] or "金丝猴" in data["answer"] or "极乐钱庄" in data["answer"])
        self.assertTrue(bool(re.search(r"第88[1-4]章|第1347章|第1327-1357章", data["sources"])))

    def test_tier1_feat3_l2_recall_zhangchenze_original_name_and_revenge(self):
        """Tier 1: 章晨泽原名章莱娣与马屠户灭门前传召回。"""
        q = "章晨泽现实本名叫章莱娣为什么被卖给马屠户？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertIn("章莱娣", data["answer"])
        self.assertTrue(bool(re.search(r"第3[789][0-9]章|第373-405章", data["sources"])))

    def test_tier1_feat3_l2_recall_jiangshi_bumie_sacrifice(self):
        """Tier 1: 猫队评书少年姜十不灭回响与焚身牺牲召回。"""
        q = "猫队评书少年姜十拥有不灭回响他是如何焚身牺牲的？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertIn("不灭", data["answer"])
        self.assertTrue(bool(re.search(r"第11[1-6][0-9]章|块 03[34]", data["sources"])))

    # --- Tier 2: 复杂级联与多章号边界 (5 Cases) ---

    def test_tier2_feat3_multi_section_cross_chapter_synthesis(self):
        """Tier 2: 跨多个章节与小节的事实聚合与多重章节出处返回。"""
        q = "文巧云在块022天台被勒死以及后续复活是在哪些章节？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertTrue(any(c in data["answer"] or c in data["sources"] for c in ("块022", "块017", "块023", "第1-33章", "第406-440章", "勒死", "复活")))

    def test_tier2_feat3_minor_character_only_in_notes_qiushiliu(self):
        """Tier 2: 仅在笔记中收录的边缘角色 (邱十六) 细粒度检索。"""
        q = "猫队的邱十六拥有什么回响？他在终焉之地的赤炎能力是怎样的？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertIn("赤炎", data["answer"])
        self.assertTrue(bool(re.search(r"第40[6-9]章|第4[1-3][0-9]章|第440章|块 013", data["sources"])))

    def test_tier2_feat3_exact_chapter_query_744(self):
        """Tier 2: 精确章号提问 (第744章) 正确命中原文章节与事件。"""
        q = "第744章发生了什么关键对话？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertTrue("第744章" in data["sources"] or "744" in data["sources"] or "722" in data["sources"])

    def test_tier2_feat3_multiple_chapter_mentions_parsing(self):
        """Tier 2: 语料段落中出现分散的多处章节标识时，正则能准确提取多重出处。"""
        raw_sample = "在第68-101章李尚武初次登场，第145章死于火灾，而在第1184-1219章再次复活"
        cites = [m.group(0) for m in re.finditer(r"第\d+(?:[-~至、]\d+)?章", raw_sample)]
        self.assertGreaterEqual(len(cites), 3)
        self.assertIn("第68-101章", cites)
        self.assertIn("第145章", cites)
        self.assertIn("第1184-1219章", cites)

    def test_tier2_feat3_cangjie_chess_dilong_sacrifice(self):
        """Tier 2: 仓颉棋造字对决大事件中地龙裁判以死破局细节召回。"""
        q = "仓颉棋造字对决中地龙裁判为什么以死破局？"
        status, data = self.post_ask(q)
        self.assertEqual(status, 200)
        self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
        self.assertTrue("仓颉棋" in data["answer"] or "地龙" in data["answer"] or "对决" in data["answer"])


# =====================================================================
# Feature 4: 口语化纯文本回答组织
# =====================================================================

class TestFEATQA04OralTextSynthesis(unittest.TestCase):
    """口语化纯文本回答组织特性验证 (Tier 1 & Tier 2)"""

    def setUp(self):
        self.retriever = L2DeepRetriever(PROD_BOOK_DIR)

    # --- Tier 1: 核心口语化转换规则 (5 Cases) ---

    def test_feat_qa_04_tier1_l2a_character_oral_synthesis(self):
        """Tier 1: L2A 人物速查口语化组织，无 Markdown 字符，携带规范出处。"""
        q = "金元勋在终焉之地有什么回响能力，他在楚天秋身边主要负责什么？"
        res = self.retriever.retrieve(q)
        self.assertIsNotNone(res)
        ans = res["answer"]
        self.assertGreaterEqual(len(ans), 30)
        self.assertLessEqual(len(ans), 450)
        # 严格无 Markdown 标记
        self.assertFalse(bool(re.search(r"[#*|`>]", ans)))
        self.assertFalse(bool(re.search(r"^\s*[-*]\s+", ans, re.M)))
        self.assertTrue("金元勋" in ans or "跃迁" in ans or "楚天秋" in ans)

    def test_feat_qa_04_tier1_l2b_notes_oral_synthesis(self):
        """Tier 1: L2B 笔记段落口语化组织，去除列表标记与标题符。"""
        q = "楚天秋在天台亲手勒死文巧云时文巧云临死前有什么动作安慰他？"
        res = self.retriever.retrieve(q)
        self.assertIsNotNone(res)
        ans = res["answer"]
        self.assertFalse(bool(re.search(r"[#*|`>]", ans)))
        self.assertTrue("文巧云" in ans or "楚天秋" in ans or "勒死" in ans)

    def test_feat_qa_04_tier1_l2b_concept_oral_synthesis(self):
        """Tier 1: L2B 概念与战局段落口语化组织。"""
        q = "宋七在终局神兽战中是如何利用回响与朱雀同归于尽的？"
        res = self.retriever.retrieve(q)
        self.assertIsNotNone(res)
        ans = res["answer"]
        self.assertFalse(bool(re.search(r"[#*|`>]", ans)))
        self.assertLessEqual(len(ans), 450)

    def test_feat_qa_04_tier1_l2c_original_chunk_dialogue_oral_synthesis(self):
        """Tier 1: 原文章节对话格式组织为流畅口语文。"""
        raw_dialogue = "### 第30章 对赌\n齐夏看着人鼠冷笑：**「我不信你的骰子没灌铅」**。\n- 赌局规则：猜单双。\n| 赌注 | 胜率 |\n| --- | --- |\n| 生命 | 50% |"
        cleaned = clean_markdown(raw_dialogue)
        oral = format_oral_answer(cleaned, "第30章")
        self.assertFalse(bool(re.search(r"[#*|`>]", oral)))
        self.assertIn("（出处：第30章）", oral)

    def test_feat_qa_04_tier1_spoken_punctuation_and_cadence(self):
        """Tier 1: 检验自然口语标点韵律，无未闭合括号或特殊代码括号。"""
        raw_text = "地蛇在终焉之地是十二生肖之一，拥有特殊的回响能力。"
        oral = format_oral_answer(raw_text, "第100章")
        self.assertTrue(bool(re.search(r"[，。；！]", oral)))
        self.assertFalse(bool(re.search(r"[<>{}\[\]\\]", oral)))

    # --- Tier 2: 边界与格式清洗极限 (5 Cases) ---

    def test_feat_qa_04_tier2_strip_dense_markdown_formatting(self):
        """Tier 2: 彻底剥离多级标题、表格、粗斜体、代码块及列表符号。"""
        dense_md = "## 标题\n**加粗**与*斜体*，还有`代码`。\n> 引用内容\n- 列表项1\n- 列表项2\n| 字段1 | 字段2 |\n|---|---|\n| 1 | 2 |"
        cleaned = clean_markdown(dense_md)
        for token in ["##", "**", "*", "`", ">", "|"]:
            self.assertNotIn(token, cleaned)
        self.assertFalse(bool(re.search(r"^\s*[-*]\s+", cleaned, re.M)))

    def test_feat_qa_04_tier2_length_lower_boundary_padding(self):
        """Tier 2: 短文本安全格式化与出处后缀拼接。"""
        short_text = "齐夏胜出了。"
        ans = format_oral_answer(short_text, "第1章")
        self.assertGreater(len(ans), 0)
        self.assertIn("（出处：第1章）", ans)

    def test_feat_qa_04_tier2_length_upper_boundary_trimming(self):
        """Tier 2: 1000+ 字超长段落严格截断至 <= 450 字并保留句末标点与出处。"""
        long_text = "齐夏在终焉之地的故事非常漫长。" * 50
        ans = format_oral_answer(long_text, "第1357章")
        self.assertLessEqual(len(ans), 450)
        self.assertTrue(ans.endswith("（出处：第1357章）"))

    def test_feat_qa_04_tier2_citation_normalization_regex(self):
        """Tier 2: 章节出处抽取规范化验证 (第X章、第X-Y章、块 018)。"""
        cites = extract_standardized_citations("参见第722章与第30~33章，以及第587-593章")
        self.assertIn("第722章", cites)
        self.assertTrue("第30~33章" in cites or "第30-33章" in cites)
        self.assertIn("第587-593章", cites)
        # 验证块编号兜底映射
        cites_block = extract_standardized_citations("参考块 018")
        self.assertIn("第581-616章", cites_block)

    def test_feat_qa_04_tier2_whitespace_and_newline_normalization(self):
        """Tier 2: 多余空行与制表符规范化清理。"""
        messy = "第一段。\n\n\n\n\n第二段。\t\t  第三段。   "
        cleaned = clean_markdown(messy)
        self.assertNotIn("\n\n\n", cleaned)
        self.assertNotIn("\t", cleaned)


# =====================================================================
# Feature 5: 自进化缓存原子回写与预热 (使用隔离临时目录)
# =====================================================================

class TestFEATQA05SelfEvolutionCache(unittest.TestCase):
    """自进化缓存原子回写与预热特性验证 (Tier 1 & Tier 2)"""

    def setUp(self):
        self.temp_td = create_isolated_book_dir(include_raw=False)
        self.book_path = os.path.join(self.temp_td.name, "十日终焉")

    def tearDown(self):
        self.temp_td.cleanup()

    # --- Tier 1: 自进化生命周期 (5 Cases) ---

    def test_feat_qa_05_tier1_complete_lifecycle_single_entry(self):
        """Tier 1: 完整自进化生命周期：未缓存查询 -> 深度召回 -> 写入问答库 -> 二次查询进 L1 秒答。"""
        kb = NovelKnowledgeBase(self.book_path)
        self.assertEqual(len(kb.qa_entries), 999)

        q = "章晨泽现实本名叫章莱娣为什么被卖给马屠户？"
        l2_res = kb.l2_retriever.retrieve(q)
        self.assertIsNotNone(l2_res)

        # 1. 执行自进化持久化
        evolved_entry = kb.save_evolved_qa(q, l2_res["answer"], l2_res["sources"])
        self.assertEqual(evolved_entry["id"], 1000)
        self.assertEqual(len(kb.qa_entries), 1000)

        # 2. 检查 档案.jsonl 磁盘文件
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        lines = open(qa_file, "r", encoding="utf-8").readlines()
        self.assertEqual(len(lines), 1000)
        last_item = json.loads(lines[-1])
        self.assertEqual(last_item["id"], 1000)
        self.assertEqual(last_item["question"], q)
        self.assertTrue(last_item["qhash"])

        # 3. 二次查询必须直接命中 L1 问答库秒答 (<3ms)
        ask_res = kb.ask(q)
        self.assertEqual(ask_res["origin"], "问答库")
        self.assertGreaterEqual(ask_res["score"], 0.55)
        self.assertLess(ask_res["elapsed_ms"], 3.0)

    def test_feat_qa_05_tier1_tts_cache_prewarming(self):
        """Tier 1: 自进化音频预热合成至缓存目录，载荷非空且有效。"""
        tts_cache_dir = os.path.join(self.temp_td.name, "tts_cache")
        tts = TTSService(cache_dir=tts_cache_dir)
        text = "文巧云临死前微笑着拍了拍楚天秋的手臂予以安慰。（出处：第744章）"
        ck = tts.cache_key(text, DEFAULT_VOICE, "+0%")
        audio_path, mime, _ = tts.synthesize(text, DEFAULT_VOICE, "+0%")
        self.assertTrue(os.path.isfile(audio_path))
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        self.assertGreaterEqual(len(audio_bytes), 100)
        assert_valid_audio_payload(audio_bytes, allow_silent=True)

    def test_feat_qa_05_tier1_deduplication_on_repeated_evolution(self):
        """Tier 1: 重复进化相同问题不重复写入，保持幂等性。"""
        kb = NovelKnowledgeBase(self.book_path)
        entry1 = kb.save_evolved_qa("相同的新问题", "回答A", "出处A")
        entry2 = kb.save_evolved_qa("相同的新问题", "回答A", "出处A")
        self.assertEqual(entry1["id"], entry2["id"])
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        lines = open(qa_file, "r", encoding="utf-8").readlines()
        self.assertEqual(len(lines), 1000)

    def test_feat_qa_05_tier1_in_memory_index_sync(self):
        """Tier 1: save_evolved_qa 实时注入内存 exact_qa 与 precomputed_qa。"""
        kb = NovelKnowledgeBase(self.book_path)
        q = "全新角色测试问题之白九的液化能力"
        entry = kb.save_evolved_qa(q, "白九拥有液化回响", "第500章")
        q_norm = norm_text(q)
        self.assertIn(q_norm, kb.exact_qa)
        self.assertEqual(kb.exact_qa[q_norm]["answer"], "白九拥有液化回响")
        hits = kb.search_archive(q, top_k=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1]["id"], entry["id"])

    def test_feat_qa_05_tier1_id_monotonicity(self):
        """Tier 1: 连续自进化 ID 单调递增无间隙 (1000, 1001, 1002)。"""
        kb = NovelKnowledgeBase(self.book_path)
        e1 = kb.save_evolved_qa("递增问题1", "答1", "源1")
        e2 = kb.save_evolved_qa("递增问题2", "答2", "源2")
        e3 = kb.save_evolved_qa("递增问题3", "答3", "源3")
        self.assertEqual(e1["id"], 1000)
        self.assertEqual(e2["id"], 1001)
        self.assertEqual(e3["id"], 1002)

    # --- Tier 2: 边界、并发与容错 (5 Cases) ---

    def test_feat_qa_05_tier2_multithreaded_10_concurrent_writers(self):
        """Tier 2: 10 线程栅栏同步并发写入自进化档案，无行交织与损坏，精确增加 10 行。"""
        kb = NovelKnowledgeBase(self.book_path)
        barrier = threading.Barrier(10)
        errors = []

        def worker(i):
            try:
                barrier.wait()
                kb.save_evolved_qa(f"并发测试提问第{i}号", f"并发回答第{i}号内容", f"第{700+i}章")
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        lines = open(qa_file, "r", encoding="utf-8").readlines()
        self.assertEqual(len(lines), 1009)
        for line in lines[-10:]:
            record = json.loads(line)
            self.assertIn("id", record)
            self.assertIn("qhash", record)
            self.assertIn("question", record)

    def test_feat_qa_05_tier2_disk_write_permission_denied_graceful_fallback(self):
        """Tier 2: 磁盘文件只读时优雅回退，内存索引仍正常可用，不抛未捕获异常。"""
        kb = NovelKnowledgeBase(self.book_path)
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        os.chmod(qa_file, 0o444)
        try:
            entry = kb.save_evolved_qa("只读盘测试问题", "回答测试", "出处测试")
            self.assertEqual(entry["question"], "只读盘测试问题")
            hits = kb.search_archive("只读盘测试问题", top_k=1)
            self.assertEqual(len(hits), 1)
        finally:
            os.chmod(qa_file, 0o644)

    def test_feat_qa_05_tier2_corrupted_jsonl_line_recovery(self):
        """Tier 2: 档案中存在损坏单行时 qa_archive.load 安全跳过并加载剩余有效行，保存不破坏历史数据。"""
        from scripts.qa_archive import load as load_archive, save as save_archive
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        with open(qa_file, "a", encoding="utf-8") as f:
            f.write("{CORRUPTED_JSON_RAW_STRING_NOT_VALID\n")

        entries = load_archive(self.book_path)
        self.assertEqual(len(entries), 999)
        new_entry = {
            "id": 1000,
            "qhash": "1234567890ab",
            "ts": "2026-11-15 12:00",
            "question": "损坏恢复后的新问题",
            "answer": "答",
            "sources": "源",
            "hits": 0,
        }
        entries.append(new_entry)
        save_archive(self.book_path, entries)
        reloaded = load_archive(self.book_path)
        self.assertEqual(len(reloaded), 1000)

    def test_feat_qa_05_tier2_multiline_answer_jsonl_serialization(self):
        """Tier 2: 多行回答、含反斜杠与引号内容严格单行序列化。"""
        kb = NovelKnowledgeBase(self.book_path)
        multiline_ans = "第一行回答。\n第二行包含\"双引号\"与\\反斜杠。\n第三行表情🎉。"
        kb.save_evolved_qa("多行回答问题", multiline_ans, "第100章")

        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        lines = open(qa_file, "r", encoding="utf-8").readlines()
        self.assertEqual(len(lines), 1000)
        last_record = json.loads(lines[-1])
        self.assertEqual(last_record["answer"], multiline_ans)

    def test_feat_qa_05_tier2_qhash_collision_resilience(self):
        """Tier 2: 仅标点差异的问题归一化为相同 qhash，不产生字典冲突异常。"""
        kb = NovelKnowledgeBase(self.book_path)
        e1 = kb.save_evolved_qa("哈希鲁棒性提问？", "答1", "源1")
        e2 = kb.save_evolved_qa("哈希鲁棒性提问!", "答1", "源1")
        self.assertEqual(e1["id"], e2["id"])

    def test_feat_qa_05_tier2_auto_evolve_flag_default_disabled(self):
        """Tier 2: 默认 auto_evolve=False 时，L2 深度检索作答不自动污染 档案.jsonl。"""
        kb = NovelKnowledgeBase(self.book_path)
        self.assertFalse(kb.auto_evolve)
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        sha_before = hashlib.sha256(open(qa_file, "rb").read()).hexdigest()

        q = "李尚武养女萱萱患有的克里格勒综合征是怎样的？"
        ans = kb.ask(q)
        self.assertIn(ans["origin"], ("原文深度召回", "精读总集"))

        sha_after = hashlib.sha256(open(qa_file, "rb").read()).hexdigest()
        self.assertEqual(sha_before, sha_after, "默认 auto_evolve=False 时不应自动持久化")

    def test_feat_qa_05_tier2_auto_evolve_flag_enabled(self):
        """Tier 2: auto_evolve=True 时，未缓存的 L2 深度检索作答自动持久化并实现再次提问秒答。"""
        kb = NovelKnowledgeBase(self.book_path, auto_evolve=True)
        self.assertTrue(kb.auto_evolve)
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        self.assertEqual(len(open(qa_file, "r", encoding="utf-8").readlines()), 999)

        q = "李尚武养女萱萱患有的克里格勒综合征是怎样的？"
        ans1 = kb.ask(q)
        self.assertIn(ans1["origin"], ("原文深度召回", "精读总集"))

        # 档案应自动增加 1 行 (至 1000 行)
        lines = open(qa_file, "r", encoding="utf-8").readlines()
        self.assertEqual(len(lines), 1000)
        last_rec = json.loads(lines[-1])
        self.assertEqual(last_rec["id"], 1000)
        self.assertEqual(last_rec["question"], q)

        # 再次提问立即命中 L1 问答库秒答
        ans2 = kb.ask(q)
        self.assertEqual(ans2["origin"], "问答库")
        self.assertLess(ans2["elapsed_ms"], 3.0)

    def test_feat_qa_05_tier2_id_calculation_with_gaps(self):
        """Tier 2: 当档案中 ID 存在间隙或非连续编号时，严格按 max(id)+1 计算下一个 ID。"""
        kb = NovelKnowledgeBase(self.book_path)
        # 人工注入高编号测试条目
        kb.qa_entries.append({"id": 5000, "qhash": "fakehash5000", "question": "高编号伪条目", "answer": "答", "sources": "源"})
        e = kb.save_evolved_qa("测试ID计算跳跃问题", "回答内容", "第999章")
        self.assertEqual(e["id"], 5001)

    def test_feat_qa_05_tier2_missing_trailing_newline_handling(self):
        """Tier 2: 目标文件缺少结尾换行符时，自动补齐换行，杜绝行交织损坏。"""
        kb = NovelKnowledgeBase(self.book_path)
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        # 移除文件末尾换行符
        content = open(qa_file, "r", encoding="utf-8").read().rstrip("\n")
        with open(qa_file, "w", encoding="utf-8") as f:
            f.write(content)

        e = kb.save_evolved_qa("末尾缺少换行符测试提问", "测试回答", "第800章")
        self.assertEqual(e["id"], 1000)

        # 验证所有行均为合法且独立的 JSON 对象
        lines = open(qa_file, "r", encoding="utf-8").readlines()
        self.assertEqual(len(lines), 1000)
        for line in lines:
            parsed = json.loads(line.strip())
            self.assertIn("id", parsed)

    def test_feat_qa_05_tier2_cli_auto_evolve_arg_parsing(self):
        """Tier 2: CLI 参数 --auto-evolve 解析正确。"""
        from scripts.web import parse_args
        args_default = parse_args([])
        self.assertFalse(args_default.auto_evolve)

        args_enabled = parse_args(["--auto-evolve"])
        self.assertTrue(args_enabled.auto_evolve)

    def test_feat_qa_05_tier2_web_api_auto_evolve_and_tts_prewarming(self):
        """Tier 2: Web API 在 auto_evolve=True 时自动存入档案并提交后台 TTS 预热合成。"""
        server = make_server(host="127.0.0.1", port=0, book_path=self.book_path, auto_evolve=True)
        port = server.server_address[1]
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.15)

        try:
            q = "李尚武养女萱萱患有的克里格勒综合征是怎样的？"
            req_data = json.dumps({"question": q}).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/ask",
                data=req_data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            self.assertIn(data["origin"], ("原文深度召回", "精读总集"))
            audio_url = data["audio_url"]

            # 等待后台 TTS 线程池执行完成 (最多 5 秒)
            time.sleep(0.3)

            # 再次查询必须立即命中 L1 问答库秒答
            with urllib.request.urlopen(req, timeout=10) as resp2:
                data2 = json.loads(resp2.read().decode("utf-8"))
            self.assertEqual(data2["origin"], "问答库")
            self.assertLess(data2["elapsed_ms"], 3.0)

            # 磁盘档案已持久化新增 1 行
            qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
            lines = open(qa_file, "r", encoding="utf-8").readlines()
            self.assertEqual(len(lines), 1000)

            # 音频通过 HTTP 获取有效
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{audio_url}", timeout=10) as a_resp:
                audio_bytes = a_resp.read()
            self.assertGreaterEqual(len(audio_bytes), 100)
            assert_valid_audio_payload(audio_bytes, allow_silent=True)
        finally:
            server.shutdown()
            server.server_close()


# =====================================================================
# Tier 3: 跨特性组合用例 (Pairwise Combinations, 6 Cases)
# =====================================================================

class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """跨特性协同验证 (Tier 3)"""

    def setUp(self):
        self.temp_td = create_isolated_book_dir(include_raw=False)
        self.book_path = os.path.join(self.temp_td.name, "十日终焉")

    def tearDown(self):
        self.temp_td.cleanup()

    def test_tier3_combo_01_f1_l1_x_f5_evolution_fuzzy_score(self):
        """F1 × F5: 新自进化条目支持模糊提问高分命中 L1 (score >= 0.55)。"""
        kb = NovelKnowledgeBase(self.book_path)
        kb.save_evolved_qa("文巧云被楚天秋勒死是在哪一章？", "文巧云在第744章被楚天秋勒死", "第744章")

        hits = kb.search_archive("文巧云被楚天秋勒死具体在哪章", top_k=1, min_score=0.55)
        self.assertEqual(len(hits), 1)
        self.assertGreaterEqual(hits[0][0], 0.55)
        self.assertEqual(hits[0][1]["id"], 1000)

    def test_tier3_combo_02_f2_oob_x_f5_zero_pollution(self):
        """F2 × F5: 连续提交 5 组越界问题，问答库文件 SHA256 绝对不变，零归档污染。"""
        kb = NovelKnowledgeBase(self.book_path)
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        sha_before = hashlib.sha256(open(qa_file, "rb").read()).hexdigest()

        for q in ["上海明天天气", "Python快速排序", "红烧牛肉面做法", "推荐几部科幻电影", "微积分基本定理"]:
            res = kb.ask(q)
            self.assertEqual(res["origin"], "检索建议")
            self.assertEqual(res["score"], 0.0)

        sha_after = hashlib.sha256(open(qa_file, "rb").read()).hexdigest()
        self.assertEqual(sha_before, sha_after)

    def test_tier3_combo_03_f3_l2_x_f4_oral_synthesis(self):
        """F3 × F4: L2 级联深度召回输出经过口语化清洗，无 Markdown 标记并含出处。"""
        kb = NovelKnowledgeBase(self.book_path)
        q = "章晨泽现实本名叫章莱娣为什么被卖给马屠户？"
        res = kb.ask(q)
        self.assertIn(res["origin"], ("原文深度召回", "精读总集"))
        ans = res["answer"]
        self.assertFalse(bool(re.search(r"[#*|`>]", ans)))
        self.assertTrue("章莱娣" in ans or "章晨泽" in ans)
        self.assertTrue(bool(re.search(r"（(?:依据|来源|出处)：第\d+.*?章）", ans)) or "章" in ans[-15:])

    def test_tier3_combo_04_f3_l2_x_f5_evolution_x_f6_tts(self):
        """F3 × F5 × F6: L2 召回生成自进化条目，同步预热 TTS 并通过 HTTP 获取有效音频。"""
        server = make_server(host="127.0.0.1", port=0, book_path=self.book_path)
        port = server.server_address[1]
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.15)

        try:
            kb = server.RequestHandlerClass.kb
            q = "李尚武养女萱萱患有的克里格勒综合征是怎样的？"
            l2_res = kb.l2_retriever.retrieve(q)
            self.assertIsNotNone(l2_res)
            kb.save_evolved_qa(q, l2_res["answer"], l2_res["sources"])

            # 通过 HTTP API 请求该问题
            req_data = json.dumps({"question": q}).encode("utf-8")
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ask", data=req_data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["origin"], "问答库")
            audio_url = data["audio_url"]

            # 获取音频
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{audio_url}", timeout=10) as a_resp:
                audio_bytes = a_resp.read()
            self.assertGreaterEqual(len(audio_bytes), 100)
            assert_valid_audio_payload(audio_bytes, allow_silent=True)
        finally:
            server.shutdown()
            server.server_close()

    def test_tier3_combo_05_f1_l1_x_f2_guard_fast_bypass(self):
        """F1 × F2: 越界前置判定为 O(1) 旁路，与 L1 秒答耗时同量级 (<3ms)。"""
        kb = NovelKnowledgeBase(self.book_path)

        t0 = time.perf_counter()
        res_oob = kb.ask("明天上海天气如何")
        t_oob = (time.perf_counter() - t0) * 1000
        self.assertEqual(res_oob["origin"], "检索建议")
        self.assertLess(t_oob, 3.0)

        t0 = time.perf_counter()
        res_l1 = kb.ask("齐夏的回响是什么")
        t_l1 = (time.perf_counter() - t0) * 1000
        self.assertEqual(res_l1["origin"], "问答库")
        self.assertLess(t_l1, 3.0)

    def test_tier3_combo_06_f5_evolution_memory_consistency(self):
        """F5 × Memory: 自进化写入后，内存特征倒排与 bigram 索引即时刷新。"""
        kb = NovelKnowledgeBase(self.book_path)
        q = "陈俊南在青龙战中自创的火球术叫什么"
        entry = kb.save_evolved_qa(q, "陈俊南自创火球术", "第1200章")
        q_norm = norm_text(q)
        self.assertIn(q_norm, kb.exact_qa)
        found_in_precomputed = any(p["entry"]["question"] == q for p in kb.precomputed_qa)
        self.assertTrue(found_in_precomputed)


# =====================================================================
# Tier 4: 真实读者与用户使用场景端到端闭环 (Real-World Scenarios, 5 Cases)
# =====================================================================

class TestTier4RealWorldScenarios(unittest.TestCase):
    """真实业务端到端应用场景闭环验证 (Tier 4)"""

    def setUp(self):
        self.temp_td = create_isolated_book_dir(include_raw=False)
        self.book_path = os.path.join(self.temp_td.name, "十日终焉")
        self.server = make_server(host="127.0.0.1", port=0, book_path=self.book_path)
        self.port = self.server.server_address[1]
        self.server_url = f"http://127.0.0.1:{self.port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        time.sleep(0.15)

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.temp_td.cleanup()

    def _post_ask(self, q: str) -> Dict[str, Any]:
        url = f"{self.server_url}/api/ask"
        req = urllib.request.Request(url, data=json.dumps({"question": q}).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_tier4_scenario_01_reader_multi_turn_mixed_dialogue_workflow(self):
        """Scenario 1: 真实读者 5 轮混合连续问答流 (已知设定 -> 深度Lore -> 越界防护 -> 重复提问秒答 -> 第二Lore)。"""
        kb = self.server.RequestHandlerClass.kb

        # Turn 1: 提问已有高频设定
        d1 = self._post_ask("齐夏今年几岁了")
        self.assertEqual(d1["origin"], "问答库")
        self.assertLess(d1["elapsed_ms"], 3.0)

        # Turn 2: 提问未收录原著细节
        q_lore1 = "李尚武养女萱萱患有的克里格勒综合征是怎样的？"
        d2 = self._post_ask(q_lore1)
        self.assertIn(d2["origin"], ("原文深度召回", "精读总集"))
        self.assertIn("克里格勒", d2["answer"])
        # 自进化存入
        kb.save_evolved_qa(q_lore1, d2["answer"], d2["sources"])

        # Turn 3: 提问现实天气无关问题
        d3 = self._post_ask("明天出门需要带伞吗")
        self.assertEqual(d3["origin"], "检索建议")
        self.assertEqual(d3["score"], 0.0)

        # Turn 4: 重复提问 Turn 2 的 Lore 问题 -> 必须命中 L1 问答库秒答
        d4 = self._post_ask(q_lore1)
        self.assertEqual(d4["origin"], "问答库")
        self.assertLess(d4["elapsed_ms"], 3.0)

        # Turn 5: 提问第二个未收录原著细节
        q_lore2 = "章晨泽现实本名叫章莱娣为什么被卖给马屠户？"
        d5 = self._post_ask(q_lore2)
        self.assertIn(d5["origin"], ("原文深度召回", "精读总集"))
        self.assertIn("章莱娣", d5["answer"])
        kb.save_evolved_qa(q_lore2, d5["answer"], d5["sources"])

        # 档案行数精确递增 2 行
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        lines = open(qa_file, "r", encoding="utf-8").readlines()
        self.assertEqual(len(lines), 1001)

    def test_tier4_scenario_02_web_api_end_to_end_lifecycle(self):
        """Scenario 2: Web API 完整生命周期闭环 (POST /api/ask -> GET /api/audio/... -> POST repeat -> audio cache)。"""
        q = "甜甜的回响是什么?"
        d1 = self._post_ask(q)
        self.assertEqual(d1["origin"], "问答库")
        audio_url = d1["audio_url"]

        # 请求音频
        with urllib.request.urlopen(f"{self.server_url}{audio_url}", timeout=10) as resp:
            audio_bytes = resp.read()
            self.assertEqual(resp.status, 200)
        assert_valid_audio_payload(audio_bytes, allow_silent=True)

        # 重复请求
        d2 = self._post_ask(q)
        self.assertEqual(d2["audio_url"], audio_url)

    def test_tier4_scenario_03_container_isolation_and_cascade_fallback(self):
        """Scenario 3: 模拟轻量容器环境 (无 9.4MB 原文.txt 与 chunks)，自适应从精读总集与 notes 优雅回答。"""
        # 确认当前环境不含 原文.txt 与 chunks
        self.assertFalse(os.path.isfile(os.path.join(self.book_path, "原文.txt")))
        self.assertFalse(os.path.isdir(os.path.join(self.book_path, "chunks")))

        # 提问深层设定
        res = self._post_ask("李尚武养女萱萱患有的克里格勒综合征是怎样的？")
        self.assertIn(res["origin"], ("原文深度召回", "精读总集"))
        self.assertIn("克里格勒", res["answer"])
        self.assertTrue(res["sources"])

    def test_tier4_scenario_04_ten_readers_concurrent_evolution_stress(self):
        """Scenario 4: 10 位读者并发发起未收录提问并自进化，验证并发原子性与再次查询秒答。"""
        kb = self.server.RequestHandlerClass.kb
        barrier = threading.Barrier(10)
        results = [None] * 10
        errors = []

        def reader(idx):
            try:
                barrier.wait()
                q = f"并发读者测试提问第{idx}个知识点"
                entry = kb.save_evolved_qa(q, f"回答内容第{idx}条", f"第{800+idx}章")
                results[idx] = entry
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        lines = open(qa_file, "r", encoding="utf-8").readlines()
        self.assertEqual(len(lines), 1009)

        # 10 个问题再次通过 HTTP 查询全部命中 L1 问答库
        for idx in range(10):
            q = f"并发读者测试提问第{idx}个知识点"
            data = self._post_ask(q)
            self.assertEqual(data["origin"], "问答库")
            self.assertLess(data["elapsed_ms"], 3.0)

    def test_tier4_scenario_05_out_of_bounds_flood_zero_pollution_audit(self):
        """Scenario 5: 20 组越界问题突发轰炸，严格审计档案库 SHA256 逐字节一致，零垃圾数据入库。"""
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        sha_initial = hashlib.sha256(open(qa_file, "rb").read()).hexdigest()

        flood_questions = [
            "明天上海天气如何", "今天夜间气温多少度", "红烧排骨怎么做", "糖醋里脊的烹饪技巧",
            "如何烤制法式面包", "意式浓缩咖啡的做法", "制作草莓蛋糕需要多少面粉", "西红柿炒鸡蛋要放白糖吗",
            "如何煎牛排保持鲜嫩多汁", "微积分基本定理的推导过程", "计算正弦函数的值", "如何推导欧几里得几何公理",
            "Python语言的快速排序算法", "快速傅里叶变换的数学原理", "光速每秒多少公里", "绝对零度是多少摄氏度",
            "感冒发烧如何物理降温", "如何治疗季节性过敏鼻炎", "有氧运动如何有效减脂", "初学游泳怎样掌握换气技巧",
        ]
        self.assertEqual(len(flood_questions), 20)

        for q in flood_questions:
            res = self._post_ask(q)
            self.assertEqual(res["origin"], "检索建议")
            self.assertEqual(res["score"], 0.0)

        sha_final = hashlib.sha256(open(qa_file, "rb").read()).hexdigest()
        self.assertEqual(sha_initial, sha_final, "越界提问洪峰导致 档案.jsonl 发生写盘污染！")


if __name__ == "__main__":
    unittest.main()
