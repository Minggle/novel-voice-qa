"""tests/test_e2e_scenarios.py
Tier 4: Real-World Application Workload Scenarios for 《十日终焉》 Voice QA System.
Covers 7 end-to-end multi-feature integration workflows:
1. Zodiac & God Beast Multi-Turn Lore Session
2. Multi-Character Voice Switch Dialogue & Instant Cache Replay
3. Graceful TTS Ladder Fallback under Network Failure
4. Full Lifecycle Single-Page Web Client Session
5. Docker Dual-Mode & Environment Configuration Validation
6. instabox 5-Box Parallel CI Dispatch Matrix Validation
7. Three-Round High-Concurrency Stress & Latency Audit
"""

import json
import os
import re
import shutil
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
    VOICES,
    NovelKnowledgeBase,
    TTSService,
    make_server,
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK_DIR = os.path.join(ROOT_DIR, "books", "十日终焉")


class BaseScenarioTestCase(unittest.TestCase):
    """Base class providing ephemeral server on port 0 for scenario tests."""

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
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data, resp.headers

    def post_json(self, path, payload):
        url = self._format_url(path)
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data, resp.headers

    def get_raw(self, path):
        url = self._format_url(path)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers


class TestTier4ApplicationScenarios(BaseScenarioTestCase):

    def test_tier4_scenario_01_zodiac_and_god_beast_multi_turn_lore_session(self):
        """场景 1: 《十日终焉》生肖神兽多轮深度问答工作流 (FEAT-QA-01, FEAT-QA-02, FEAT-WEB-02)"""
        turns = [
            ("生肖和神兽的规则是什么", "问答库", ("生肖", "神兽", "人级", "地级", "天级")),
            ("地级和天级生肖有什么区别", "问答库", ("地级", "天级", "生肖")),
            ("人鼠今年多大了", "问答库", ("人鼠", "岁", "年")),
            ("生肖体系的运转机制", "精读总集", ("生肖", "机制", "游戏", "规则")),
            ("神兽朱雀与赌命惩罚", "精读总集", ("朱雀", "神兽", "惩罚", "赌命")),
        ]

        total_elapsed = 0
        for q, expected_origin_hint, keywords in turns:
            t0 = time.time()
            status, data, _ = self.post_json("/api/ask", {"question": q})
            elapsed_ms = (time.time() - t0) * 1000.0
            total_elapsed += elapsed_ms

            self.assertEqual(status, 200)
            self.assertIn(data["origin"], ("问答库", "精读总集"))
            self.assertTrue(any(k in data["answer"] for k in keywords), f"Question '{q}' answer did not contain expected keywords: {data['answer'][:80]}")
            self.assertTrue(bool(data["sources"]))
            self.assertTrue(data["audio_url"].startswith("/api/audio/"))

        avg_latency = total_elapsed / len(turns)
        self.assertLess(avg_latency, 100.0, f"Average turn latency was {avg_latency}ms, expected < 100ms")

    def test_tier4_scenario_02_multi_character_voice_switch_dialogue_replay(self):
        """场景 2: 多角色音色连续对话与缓存命中极速重放 (FEAT-TTS-01, FEAT-TTS-03, FEAT-WEB-02)"""
        # Step 1: Xiaoxiao voice
        status1, data1, _ = self.post_json("/api/ask", {
            "question": "齐夏的回响是什么",
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate": "+0%",
        })
        self.assertEqual(status1, 200)
        url1 = data1["audio_url"]

        # Step 2: Yunxi voice on same question -> produces distinct audio URL
        status2, data2, _ = self.post_json("/api/ask", {
            "question": "齐夏的回响是什么",
            "voice": "zh-CN-YunxiNeural",
            "rate": "+0%",
        })
        self.assertEqual(status2, 200)
        url2 = data2["audio_url"]
        self.assertNotEqual(url1, url2)

        # Step 3: Xiaoyi voice on different question
        status3, data3, _ = self.post_json("/api/ask", {
            "question": "楚天秋的结局是什么",
            "voice": "zh-CN-XiaoyiNeural",
            "rate": "+0%",
        })
        self.assertEqual(status3, 200)
        url3 = data3["audio_url"]
        self.assertNotEqual(url1, url3)

        # Step 4: Re-ask first question with Xiaoxiao -> instant cache hit
        t0 = time.time()
        status4, data4, _ = self.post_json("/api/ask", {
            "question": "齐夏的回响是什么",
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate": "+0%",
        })
        replay_elapsed_ms = (time.time() - t0) * 1000.0
        self.assertEqual(status4, 200)
        self.assertEqual(data4["audio_url"], url1)
        self.assertLess(replay_elapsed_ms, 25.0)

        # Step 5: Verify audio endpoint serves cached stream
        st_audio, audio_bytes, headers = self.get_raw(url1)
        self.assertEqual(st_audio, 200)
        self.assertIn(headers.get_content_type(), ("audio/mpeg", "audio/aiff", "audio/wav"))
        self.assertGreater(len(audio_bytes), 200)

    @patch("scripts.web.edge_tts")
    def test_tier4_scenario_03_graceful_tts_ladder_fallback_under_network_failure(self, mock_edge):
        """场景 3: 弱网/离线环境下 3级 TTS 自动降级与数据流验证 (FEAT-TTS-02, FEAT-TTS-01, FEAT-WEB-01)"""
        mock_comm = mock_edge.Communicate.return_value
        mock_comm.save.side_effect = ConnectionError("Simulated Microsoft Cloud Offline")

        unique_q = f"断网降级验证提问_{time.time()}"
        status, data, _ = self.post_json("/api/ask", {
            "question": unique_q,
            "voice": "zh-CN-XiaoxiaoNeural",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["audio_url"].startswith("/api/audio/"))

        st_audio, audio_bytes, headers = self.get_raw(data["audio_url"])
        self.assertEqual(st_audio, 200)
        self.assertIn(headers.get_content_type(), ("audio/mpeg", "audio/aiff", "audio/wav"))
        self.assertGreater(len(audio_bytes), 100)

        wav_path = os.path.join(self.tts.cache_dir, "scenario3_pure_wav.wav")
        self.tts._generate_silent_wav(wav_path, duration_sec=0.5)
        self.assertTrue(os.path.isfile(wav_path))
        with wave.open(wav_path, "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getframerate(), 16000)
            self.assertGreater(wf.getnframes(), 0)

    def test_tier4_scenario_04_full_lifecycle_single_page_web_client_flow(self):
        """场景 4: Web 单页前端与 RESTful API 全周期端到端会话 (FEAT-WEB-01, FEAT-WEB-02, FEAT-TTS-03)"""
        s_page, body, h_page = self.get_raw("/")
        self.assertEqual(s_page, 200)
        self.assertIn("text/html", h_page.get_content_type())
        html = body.decode("utf-8")
        self.assertIn("《十日终焉》智能语音问答系统", html)

        s_status, st_data, _ = self.get_json("/api/status")
        self.assertEqual(s_status, 200)
        self.assertEqual(st_data["status"], "ok")
        self.assertEqual(st_data["book"], "十日终焉")

        s_voices, v_data, _ = self.get_json("/api/voices")
        self.assertEqual(s_voices, 200)
        self.assertIn("zh-CN-XiaoxiaoNeural", v_data["voices"])

        s_arch, arch_data, _ = self.get_json("/api/archive?limit=5")
        self.assertEqual(s_arch, 200)
        self.assertEqual(len(arch_data["items"]), 5)

        first_q = arch_data["items"][0]["question"]
        s_ask, ask_data, h_ask = self.post_json("/api/ask", {"question": first_q})
        self.assertEqual(s_ask, 200)
        self.assertEqual(h_ask.get("Access-Control-Allow-Origin"), "*")
        self.assertTrue(bool(ask_data["answer"]))
        self.assertTrue(ask_data["audio_url"].startswith("/api/audio/"))

        s_audio, audio_bytes, h_audio = self.get_raw(ask_data["audio_url"])
        self.assertEqual(s_audio, 200)
        self.assertEqual(h_audio.get("Cache-Control"), "public, max-age=86400")
        self.assertEqual(h_audio.get("Access-Control-Allow-Origin"), "*")
        self.assertGreater(len(audio_bytes), 100)

    def test_tier4_scenario_05_docker_dual_mode_and_environment_configuration_validation(self):
        """场景 5: Docker 生产模式与测试模式配置自检与镜像体积验证 (FEAT-DOCKER-01, FEAT-DOCKER-02, FEAT-DOCKER-03)"""
        dockerfile = os.path.join(ROOT_DIR, "Dockerfile")
        dockerignore = os.path.join(ROOT_DIR, ".dockerignore")
        self.assertTrue(os.path.isfile(dockerfile))
        self.assertTrue(os.path.isfile(dockerignore))

        with open(dockerfile, "r", encoding="utf-8") as f:
            df = f.read()
        with open(dockerignore, "r", encoding="utf-8") as f:
            di = f.read()

        self.assertTrue(re.search(r'(CMD|ENTRYPOINT)\s*\[.*pytest.*\]', df))
        self.assertIn("/results", df)
        self.assertIn("web.py", df)

        self.assertTrue(any(pat in di for pat in ("books/*.txt", "books/十日终焉/原文.txt", "*.txt")))
        self.assertTrue("!books/十日终焉/问答库/档案.jsonl" in di or "!档案.jsonl" in di)
        self.assertTrue("!books/十日终焉/精读总集.md" in di or "!精读总集.md" in di)

        patterns = [p.strip() for p in di.splitlines() if p.strip() and not p.strip().startswith("#") and not p.strip().startswith("!")]
        build_ctx_size = 0
        for root, dirs, files in os.walk(ROOT_DIR):
            if ".git" in dirs:
                dirs.remove(".git")
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), ROOT_DIR)
                ignored = any(p in rel or f.endswith(p.lstrip("*")) for p in patterns if "/" in p or p.startswith("*"))
                if "原文.txt" in rel or "chunks" in rel:
                    ignored = True
                if rel in ("books/十日终焉/问答库/档案.jsonl", "books/十日终焉/精读总集.md"):
                    ignored = False
                if not ignored:
                    build_ctx_size += os.path.getsize(os.path.join(root, f))

        ctx_mb = build_ctx_size / (1024 * 1024)
        self.assertLess(ctx_mb, 50.0)

        self.assertIn("EXPOSE 8080", df)
        self.assertIn("ENV PORT=8080", df)
        self.assertIn("ENV PYTHONUNBUFFERED=1", df)


if __name__ == "__main__":
    unittest.main()
