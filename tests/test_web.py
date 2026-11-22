"""test_web.py: 《十日终焉》语音问答 Web 服务的单元与集成测试。
覆盖:
- 服务状态健康检查 (/api/status)
- 问答库秒答检索 (/api/ask)
- 精读总集深度检索 (/api/ask)
- 多音色 TTS 切换与播放链路 (晓晓/云希/晓伊/婷婷)
- 音频文件缓存与响应
- 问答库索引浏览接口 (/api/archive)
- 异常请求与边界情况
- executor/LRU 待合成注册、限流、500字上限
- score_qa 归一化契约 [0,1]
- say→edge 反向映射 (Linux/无 say 环境)
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import pytest

from conftest import assert_valid_audio_payload
from scripts.web import make_server, DEFAULT_VOICE, VOICES, SAY_TO_EDGE


@pytest.fixture(scope="module")
def web_server():
    """启动后台测试服务器, 运行完毕后自动关闭。"""
    # 查找 books/十日终焉 路径
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    book_path = os.path.join(root, "books", "十日终焉")

    # 绑定随机高位端口
    port = 18888
    server = make_server(host="127.0.0.1", port=port, book_path=book_path)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)  # 等待启动

    yield f"http://127.0.0.1:{port}"

    server.shutdown()
    server.server_close()


def test_status_endpoint(web_server):
    url = f"{web_server}/api/status"
    req = urllib.request.urlopen(url)
    assert req.status == 200
    data = json.loads(req.read().decode("utf-8"))
    assert data["status"] == "ok"
    assert data["book"] == "十日终焉"
    assert data["qa_count"] >= 50
    assert data["summary_sections"] > 50
    assert "zh-CN-XiaoxiaoNeural" in data["voices"]
    assert "zh-CN-YunxiNeural" in data["voices"]
    assert "zh-CN-XiaoyiNeural" in data["voices"]

    # 附带测试端口/主机/参数解析容错
    from scripts.web import resolve_port, resolve_host, parse_args
    assert resolve_port(None) in range(0, 65536)
    assert resolve_port("not_a_port") == 8080
    assert resolve_port(-1) == 8080
    assert resolve_port(99999) == 8080
    assert resolve_port(8888) == 8888
    assert resolve_host("127.0.0.1") == "127.0.0.1"
    assert resolve_host("") in ("0.0.0.0", "")
    args = parse_args([])
    assert args.port in range(0, 65536)


def test_index_html(web_server):
    url = f"{web_server}/"
    req = urllib.request.urlopen(url)
    assert req.status == 200
    html = req.read().decode("utf-8")
    assert "十日终焉" in html
    assert "智能语音问答" in html
    assert "zh-CN-XiaoxiaoNeural" in html


def test_ask_archive_hit(web_server):
    """测试高频问答库秒答。"""
    url = f"{web_server}/api/ask"
    payload = json.dumps({"question": "齐夏的回响是什么", "voice": DEFAULT_VOICE}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    data = json.loads(resp.read().decode("utf-8"))
    assert data["origin"] == "问答库"
    assert "生生不息" in data["answer"]
    assert data["score"] == 1.0, f"score 应归一化为 1.0, 实际为 {data['score']}"
    assert data["elapsed_ms"] < 2000
    assert data.get("audio_url", "").startswith("/api/audio/")

    # 校验各类场景下的 score_qa 严格保持在 [0, 1] 契约
    from scripts.web import NovelKnowledgeBase
    kb = NovelKnowledgeBase.__new__(NovelKnowledgeBase)
    kb.qa_entries = []
    kb.summary_sections = []
    for q, cand in [
        ("齐夏的回响是什么", "齐夏的回响是什么"),
        ("齐夏", "楚天秋"),
        ("", ""),
        ("什么是回响", "甜甜的回响是什么"),
        ("极道是什么", "极道组织起源"),
    ]:
        s = kb.score_qa(q, {"question": cand, "answer": "答"})
        assert 0.0 <= s <= 1.0, f"score_qa({q!r}, {cand!r}) = {s} 超出 [0,1]"


def test_ask_summary_retrieval(web_server):
    """测试问答库未直接收录但精读总集包含的深层分析检索。"""
    url = f"{web_server}/api/ask"
    payload = json.dumps({"question": "核心立意与执念造物", "voice": "zh-CN-YunxiNeural"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    data = json.loads(resp.read().decode("utf-8"))
    assert data["origin"] in ("精读总集", "大模型生成", "原文深度召回")
    assert "执念" in data["answer"] or "想念" in data["answer"]
    assert "精读总集" in data["sources"]


def test_tts_voice_switch_all_three_edge(web_server):
    """测试 edge-tts 晓晓/云希/晓伊 三种主要音色的实时合成，并校验 500 字上限与令牌桶。"""
    voices = ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural"]
    for v in voices:
        url = f"{web_server}/api/tts?text={urllib.parse.quote('测试语音音色')}&voice={v}"
        req = urllib.request.urlopen(url)
        assert req.status == 200
        content = req.read()
        assert req.headers.get("Content-Type") in ("audio/mpeg", "audio/aiff", "audio/wav")
        assert_valid_audio_payload(content)

    # 1. 500 字上限校验：超长 400，恰好 500 字 200
    long_text = "齐夏" * 300  # 600 字
    url_long = f"{web_server}/api/tts?text={urllib.parse.quote(long_text)}&voice={DEFAULT_VOICE}"
    try:
        urllib.request.urlopen(url_long)
        assert False, "应返回 400"
    except urllib.error.HTTPError as e:
        assert e.code == 400

    text_500 = "齐夏" * 250  # 500 字
    url_500 = f"{web_server}/api/tts?text={urllib.parse.quote(text_500)}&voice={DEFAULT_VOICE}"
    resp = urllib.request.urlopen(url_500)
    assert resp.status == 200

    # 2. 令牌桶限流单元与补充测试
    from scripts.web import TTSService
    tts = TTSService(cache_dir="/tmp/test_limit_dir")
    tts._rate_limit_rps = 2
    tts._rate_tokens = 2.0
    assert tts.check_rate_limit() is True
    assert tts.check_rate_limit() is True
    assert tts.check_rate_limit() is False
    tts._rate_last_time -= 1.0
    assert tts.check_rate_limit() is True
    # 令牌桶上限容量与重填上限测试
    tts._rate_last_time -= 10.0
    assert tts.check_rate_limit() is True
    assert tts._rate_tokens <= float(tts._rate_limit_rps)
    tts.shutdown(wait=False)


def test_tts_say_fallback_voice(web_server, tmp_path):
    """测试本地 say 兜底音色（无 say 环境自动反向映射到 edge-tts，禁止静音）。"""
    url = f"{web_server}/api/tts?text={urllib.parse.quote('测试婷婷兜底音色')}&voice=Tingting"
    req = urllib.request.urlopen(url)
    assert req.status == 200
    content = req.read()
    assert req.headers.get("Content-Type") in ("audio/aiff", "audio/wav", "audio/mpeg")
    assert_valid_audio_payload(content)

    # 校验 SAY_TO_EDGE 完整性
    for say_v in ("Tingting", "Sinji", "Mei-Jia"):
        assert say_v in SAY_TO_EDGE
        assert SAY_TO_EDGE[say_v] in VOICES

    # 校验 Linux 下 say 音色反向映射到 edge-tts
    import unittest.mock as mock
    from scripts.web import TTSService
    tts = TTSService(cache_dir=str(tmp_path))
    captured = []
    class FakeComm:
        def __init__(self, text, voice, rate):
            captured.append(voice)
        async def save(self, path):
            with open(path, "wb") as f:
                f.write(b"\xff\xfb\x90\x44" + b"\x00" * 300)
    fake_et = mock.MagicMock()
    fake_et.Communicate = FakeComm
    with mock.patch("scripts.web.edge_tts", fake_et), \
         mock.patch("scripts.web.IS_MAC", False):
        path, mime, eng = tts.synthesize("婷婷测试", "Tingting", "+0%")
    assert eng == "edge-tts"
    assert captured[0] == "zh-CN-XiaoxiaoNeural"

    # 校验无 edge-tts 且无 say 时的 fallback-wav
    with mock.patch("scripts.web.edge_tts", None), \
         mock.patch("scripts.web.IS_MAC", False):
        path_wav, mime_wav, eng_wav = tts.synthesize("降级测试", DEFAULT_VOICE, "+0%")
    assert eng_wav == "fallback-wav"
    assert mime_wav == "audio/wav"


def test_audio_cache_endpoint(web_server, tmp_path):
    """测试通过 ask 产出的 audio_url 是否能正常下载音频，及 TTSService LRU/缓存机制。"""
    url = f"{web_server}/api/ask"
    payload = json.dumps({"question": "甜甜的回响是什么"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read().decode("utf-8"))
    audio_url = data.get("audio_url")
    assert audio_url

    # 抓取音频
    full_audio_url = f"{web_server}{audio_url}"
    audio_req = urllib.request.urlopen(full_audio_url)
    assert audio_req.status == 200
    audio_bytes = audio_req.read()
    assert_valid_audio_payload(audio_bytes)

    # 验证 TTSService LRU 淘汰、executor 安全、shutdown 与缓存确定性
    from scripts.web import TTSService, make_server
    tts = TTSService(cache_dir=str(tmp_path), max_pending=3)
    assert tts._closed is False
    for i in range(4):
        tts.register_pending(f"k{i:031d}", f"text{i}", DEFAULT_VOICE, "+0%")
    assert len(tts.pending_tasks) == 3
    assert "k0000000000000000000000000000000" not in tts.pending_tasks

    # 测试 TTSService.shutdown 与 submit_background_synthesize 保护
    tts.shutdown(wait=False)
    assert tts._closed is True
    assert tts.submit_background_synthesize("测试", DEFAULT_VOICE, "+0%") is None
    # 幂等多次 shutdown 不抛异常
    tts.shutdown(wait=True)

    # 验证 ThreadingNovelServer.server_close 触发 tts.shutdown
    dummy_server = make_server(host="127.0.0.1", port=0)
    server_tts = getattr(dummy_server.RequestHandlerClass, "tts", None)
    assert server_tts is not None
    assert server_tts._closed is False
    dummy_server.server_close()
    assert server_tts._closed is True

    assert tts.is_cached("deadbeef" * 4) is None
    k1 = tts.cache_key("a", "v", "r")
    k2 = tts.cache_key("a", "v", "r")
    assert k1 == k2 and len(k1) == 32


def test_archive_search_endpoint(web_server):
    """测试问答库索引浏览接口。"""
    url = f"{web_server}/api/archive?q={urllib.parse.quote('齐夏')}&limit=5"
    req = urllib.request.urlopen(url)
    assert req.status == 200
    data = json.loads(req.read().decode("utf-8"))
    assert data["total"] >= 50
    assert len(data["items"]) >= 1
    assert any("齐夏" in item["question"] for item in data["items"])


def test_empty_question_handling(web_server):
    url = f"{web_server}/api/ask"
    payload = json.dumps({"question": "   "}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    data = json.loads(resp.read().decode("utf-8"))
    assert "请输入" in data["answer"]


def test_invalid_json_handling(web_server):
    url = f"{web_server}/api/ask"
    req = urllib.request.Request(url, data=b"not-a-json", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
        assert False, "Should raise HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_non_dict_json_handling(web_server):
    url = f"{web_server}/api/ask"
    for payload in (b"12345", b"[1, 2]", b'"hello"', b"null", b"true"):
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            assert False, f"Should raise HTTPError for {payload}"
        except urllib.error.HTTPError as e:
            assert e.code == 400
            data = json.loads(e.read().decode("utf-8"))
            assert data["error"] == "Expected JSON object"


def test_ask_entity_disambiguation(web_server):
    """验证实体与泛概念消歧:
    - 什么是回响 不得误判为甜甜
    - 齐夏的真实身份 不得误判为许流年
    - 余念安到底存不存在 不得误判为人鼠舒画
    - 道到底是什么东西 不得误判为极道组织
    """
    url = f"{web_server}/api/ask"

    # 1. 什么是回响
    payload = json.dumps({"question": "什么是回响", "voice": DEFAULT_VOICE}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    assert "张丽娟" not in resp["answer"]
    assert any(k in resp["answer"] for k in ("异能", "能力", "契机", "信念", "潜意识", "回响"))

    # 2. 齐夏的真实身份是什么
    payload = json.dumps({"question": "齐夏的真实身份是什么"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    assert "许流年表面是原住民" not in resp["answer"]
    assert any(k in resp["answer"] for k in ("齐夏", "白羊", "羊哥", "主角"))

    # 3. 余念安到底存不存在
    payload = json.dumps({"question": "余念安到底存不存在"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    assert "舒画(人鼠)" not in resp["answer"]
    assert "余念安" in resp["answer"] or "执念造物" in resp["answer"]

    # 4. 道到底是什么东西
    payload = json.dumps({"question": "道到底是什么东西"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    assert "极道万岁" not in resp["answer"]
    assert any(k in resp["answer"] for k in ("眼球", "门票", "货币", "3600", "骗局"))


def test_say_audio_format_is_wave(web_server):
    """测试 macOS say 兜底输出标准浏览器兼容的 RIFF WAVE 音频。"""
    url = f"{web_server}/api/tts?text={urllib.parse.quote('测试标准WAV格式输出')}&voice=Tingting"
    req = urllib.request.urlopen(url)
    assert req.status == 200
    content = req.read()
    assert len(content) > 100
    if req.headers.get("Content-Type") == "audio/wav":
        assert content[:4] == b"RIFF"
        assert content[8:12] == b"WAVE"


def test_audio_path_safety(web_server):
    """测试音频端点非法哈希与路径遍历防御。"""
    invalid_urls = [
        f"{web_server}/api/audio/../etc/passwd",
        f"{web_server}/api/audio/invalid_hash_string",
        f"{web_server}/api/audio/0123456789abcdef0123456789abcdef.sh",
    ]
    for u in invalid_urls:
        try:
            urllib.request.urlopen(u)
            assert False, f"Should reject {u}"
        except urllib.error.HTTPError as e:
            assert e.code == 404


def test_long_and_emoji_query(web_server):
    """测试极端长文本与纯 emoji 输入稳定性。"""
    url = f"{web_server}/api/ask"

    # 纯 Emoji
    payload = json.dumps({"question": "😂😂😂😂" * 50}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    assert resp["origin"] == "检索建议"

    # 超长输入
    payload = json.dumps({"question": "齐夏" * 300}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    assert resp["origin"] in ("问答库", "精读总集", "大模型生成", "原文深度召回", "检索建议")


def test_voices_endpoint(web_server):
    """测试 GET /api/voices 端点。"""
    url = f"{web_server}/api/voices"
    req = urllib.request.urlopen(url)
    assert req.status == 200
    data = json.loads(req.read().decode("utf-8"))
    assert data["status"] == "ok"
    assert "zh-CN-XiaoxiaoNeural" in data["voices"]
    assert "zh-CN-YunxiNeural" in data["voices"]
    assert "zh-CN-XiaoyiNeural" in data["voices"]
    assert "Tingting" in data["voices"]
    assert len(data.get("items", [])) >= 5


def test_audio_cache_endpoint_without_extension(web_server):
    """测试 GET /api/audio/<hash> 不带扩展名请求。"""
    url = f"{web_server}/api/ask"
    payload = json.dumps({"question": "陈俊南的回响是什么"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read().decode("utf-8"))
    audio_url = data.get("audio_url")
    assert audio_url

    # 去掉扩展名，如 /api/audio/3f8d...
    bare_audio_url = audio_url.rsplit(".", 1)[0]
    full_audio_url = f"{web_server}{bare_audio_url}"
    audio_req = urllib.request.urlopen(full_audio_url)
    assert audio_req.status == 200
    assert audio_req.headers.get("Content-Type") in ("audio/mpeg", "audio/wav", "audio/aiff")
    audio_bytes = audio_req.read()
    assert len(audio_bytes) > 200


def test_ask_out_of_scope_fallback(web_server):
    """测试 Level 3/4 兜底机制：超出小说范围的问题返回引导建议。"""
    url = f"{web_server}/api/ask"
    payload = json.dumps({"question": "明天上海天气怎么样"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    data = json.loads(resp.read().decode("utf-8"))
    assert data["origin"] == "检索建议"
    assert data["score"] == 0.0
    assert "在当前精读档案中未检索到完全匹配的直接答案" in data["answer"]
    assert "建议换用" in data["answer"]
    assert data["entry_id"] == ""


def test_concurrent_burst_20_requests_no_resets(web_server):
    """测试 TCP Listen Backlog=64 下 20 线程并发突发请求无丢包或重置。"""
    import concurrent.futures

    def worker(i):
        url = f"{web_server}/api/ask"
        payload = json.dumps({"question": "齐夏的回响是什么", "voice": DEFAULT_VOICE}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data.get("origin")

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(worker, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 20
    assert all(status == 200 for status, _ in results)
    assert all(origin == "问答库" for _, origin in results)


def test_archive_invalid_limit_coercion(web_server):
    """测试 /api/archive 对非法 limit 参数的容错与兜底。"""
    for bad_limit in ["invalid_str", "", "-10", "abc"]:
        url = f"{web_server}/api/archive?limit={bad_limit}"
        req = urllib.request.urlopen(url)
        assert req.status == 200
        data = json.loads(req.read().decode("utf-8"))
        assert data["total"] >= 50
        assert len(data["items"]) <= 50
        assert len(data["items"]) > 0


def test_ask_non_string_question_coercion(web_server):
    """测试 POST /api/ask 输入非字符串类型 question 时的强制转换与鲁棒性。"""
    test_cases = [
        {"question": None},
        {"question": 12345},
        {"question": True},
        {"question": ["list_val"]},
        {"question": {"dict_key": "val"}},
    ]
    for body in test_cases:
        url = f"{web_server}/api/ask"
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "answer" in data
        assert "origin" in data





