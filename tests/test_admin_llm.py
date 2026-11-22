"""test_admin_llm.py: 大模型渠道管理后台与热切换接口测试。"""
import http.server
import json
import os
import stat
import threading
import time
import urllib.error
import urllib.request
import pytest

from scripts.web import DEFAULT_LLM_BASE, DEFAULT_LLM_KEY, DEFAULT_LLM_MODEL, make_server


def _http_json(url, method="GET", body=None, headers=None):
    req_headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw}
        return e.code, payload


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "llm_channels.json"
    monkeypatch.setenv("NOVEL_LLM_CONFIG", str(cfg_path))
    monkeypatch.setenv("NOVEL_ADMIN_TOKEN", "testtok")
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    book_path = os.path.join(root, "books", "十日终焉")
    return cfg_path, book_path


def _start_server(book_path):
    server = make_server(host="127.0.0.1", port=0, book_path=book_path)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{port}"


def test_admin_auth_and_page(admin_env):
    assert DEFAULT_LLM_KEY == ""
    assert DEFAULT_LLM_BASE == ""
    assert DEFAULT_LLM_MODEL == ""
    _, book_path = admin_env
    server, base = _start_server(book_path)
    try:
        # /admin 无需 token 返回 200 HTML 且不含秘密或硬编码厂商预设
        with urllib.request.urlopen(f"{base}/admin", timeout=5) as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers.get("Content-Type", "")
            html = resp.read().decode("utf-8")
            assert "大模型渠道管理" in html
            assert "fetchChannelModels" in html
            assert "https://api.example.com/v1" in html
            assert "testtok" not in html
            for vendor in ("step" + "fun", "ki" + "mi"):
                assert vendor not in html.lower()

        # 缺少或错误 token 返回 401
        for hdrs in ({}, {"X-Admin-Token": "wrong"}):
            code, data = _http_json(f"{base}/api/admin/llm", headers=hdrs)
            assert code == 401
            assert "error" in data

            code, _ = _http_json(
                f"{base}/api/admin/llm",
                method="POST",
                body={"action": "activate", "id": "off"},
                headers=hdrs,
            )
            assert code == 401

            code, _ = _http_json(
                f"{base}/api/admin/llm/test",
                method="POST",
                body={},
                headers=hdrs,
            )
            assert code == 401

            code, _ = _http_json(
                f"{base}/api/admin/llm/models",
                method="POST",
                body={"api_base": "https://api.example.com/v1"},
                headers=hdrs,
            )
            assert code == 401
    finally:
        server.shutdown()
        server.server_close()


def test_admin_get_masks_keys_and_upsert_activate_persist(admin_env):
    cfg_path, book_path = admin_env
    hdrs = {"X-Admin-Token": "testtok"}
    secret_key = "sk-test-1234567890-abcdef"

    server, base = _start_server(book_path)
    try:
        # 无配置文件且无环境变量时默认关闭且无硬编码预设渠道
        code, data = _http_json(f"{base}/api/admin/llm", headers=hdrs)
        assert code == 200
        assert data["active"] == "off"
        assert data["enabled"] is False
        assert data["channels"] == []

        # 校验无效参数被拒绝
        code, _ = _http_json(
            f"{base}/api/admin/llm",
            method="POST",
            body={"action": "upsert", "channel": {"id": "bad id!", "api_base": "https://example.com"}},
            headers=hdrs,
        )
        assert code == 400
        code, _ = _http_json(
            f"{base}/api/admin/llm",
            method="POST",
            body={"action": "upsert", "channel": {"id": "custom1", "api_base": "ftp://example.com"}},
            headers=hdrs,
        )
        assert code == 400

        # upsert 新渠道并 activate
        code, data = _http_json(
            f"{base}/api/admin/llm",
            method="POST",
            body={
                "action": "upsert",
                "channel": {
                    "id": "custom1",
                    "name": "Custom LLM",
                    "api_base": "https://api.example.com/v1",
                    "api_key": secret_key,
                    "model": "custom-model-v1",
                    "timeout": 12.5,
                },
            },
            headers=hdrs,
        )
        assert code == 200
        assert secret_key not in json.dumps(data, ensure_ascii=False)
        ch_view = next(c for c in data["channels"] if c["id"] == "custom1")
        assert ch_view["api_key"] == f"{secret_key[:4]}…{secret_key[-4:]}"

        # 预填缓存以验证 activate 会清空缓存
        kb = server.RequestHandlerClass.kb
        kb.llm_client._cache["k"] = "v"
        kb._l2_cache["q"] = {"answer": "cached"}

        code, data = _http_json(
            f"{base}/api/admin/llm",
            method="POST",
            body={"action": "activate", "id": "custom1"},
            headers=hdrs,
        )
        assert code == 200
        assert data["active"] == "custom1"
        assert data["enabled"] is True

        # 原地更新：kb.llm_client 与 kb.l2_retriever.llm_client 是同一个对象且均已更新
        assert kb.llm_client is kb.l2_retriever.llm_client
        assert kb.llm_client.model == "custom-model-v1"
        assert kb.llm_client.api_base == "https://api.example.com/v1"
        assert kb.llm_client.api_key == secret_key
        assert kb.llm_client.enabled is True
        assert kb.llm_client._cache == {}
        assert kb._l2_cache == {}

        # 配置文件已写入且权限 600
        assert cfg_path.is_file()
        mode = stat.S_IMODE(os.stat(cfg_path).st_mode)
        assert mode == 0o600

        # /api/status 反映最新 llm 状态且不含 key
        code, status_data = _http_json(f"{base}/api/status")
        assert code == 200
        assert status_data["llm"] == {"active": "custom1", "model": "custom-model-v1", "enabled": True}
        assert secret_key not in json.dumps(status_data, ensure_ascii=False)

        # 不能删除当前 active 渠道
        code, _ = _http_json(
            f"{base}/api/admin/llm",
            method="POST",
            body={"action": "delete", "id": "custom1"},
            headers=hdrs,
        )
        assert code == 400
    finally:
        server.shutdown()
        server.server_close()

    # 重新 make_server，验证持久化生效
    server2, base2 = _start_server(book_path)
    try:
        kb2 = server2.RequestHandlerClass.kb
        assert kb2.llm_client is kb2.l2_retriever.llm_client
        assert kb2.llm_client.model == "custom-model-v1"
        assert kb2.llm_client.api_key == secret_key
        assert kb2.active_llm_id == "custom1"

        # activate off -> enabled 为 False
        code, data = _http_json(
            f"{base2}/api/admin/llm",
            method="POST",
            body={"action": "activate", "id": "off"},
            headers=hdrs,
        )
        assert code == 200
        assert data["active"] == "off"
        assert data["enabled"] is False
        assert kb2.llm_client.enabled is False

        # 删除非 active 渠道 custom1 成功
        code, data = _http_json(
            f"{base2}/api/admin/llm",
            method="POST",
            body={"action": "delete", "id": "custom1"},
            headers=hdrs,
        )
        assert code == 200
        assert all(c["id"] != "custom1" for c in data["channels"])
    finally:
        server2.shutdown()
        server2.server_close()


def test_admin_llm_test_unreachable_fast_fail(admin_env):
    _, book_path = admin_env
    hdrs = {"X-Admin-Token": "testtok"}
    server, base = _start_server(book_path)
    try:
        code, _ = _http_json(
            f"{base}/api/admin/llm",
            method="POST",
            body={
                "action": "upsert",
                "channel": {
                    "id": "dead-local",
                    "name": "Dead Local",
                    "api_base": "http://127.0.0.1:9",
                    "api_key": "sk-deadbeef",
                    "model": "dummy-model",
                    "timeout": 2,
                },
            },
            headers=hdrs,
        )
        assert code == 200

        t0 = time.monotonic()
        code, res = _http_json(
            f"{base}/api/admin/llm/test",
            method="POST",
            body={"id": "dead-local"},
            headers=hdrs,
        )
        elapsed = time.monotonic() - t0
        assert code == 200
        assert res["ok"] is False
        assert elapsed < 3.0
    finally:
        server.shutdown()
        server.server_close()


def test_admin_fetch_models_and_env_default(admin_env, tmp_path, monkeypatch):
    _, book_path = admin_env
    hdrs = {"X-Admin-Token": "testtok"}
    seen_auth = []

    class MockModelsHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen_auth.append(self.headers.get("Authorization", ""))
            if self.path != "/v1/models":
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps({
                "object": "list",
                "data": [
                    {"id": "zeta-model"},
                    {"id": "alpha-model"},
                    {"id": "alpha-model"},
                    {"id": "beta-model"},
                ],
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    mock_srv = http.server.HTTPServer(("127.0.0.1", 0), MockModelsHandler)
    mock_port = mock_srv.server_address[1]
    mock_thread = threading.Thread(target=mock_srv.serve_forever, daemon=True)
    mock_thread.start()

    server, base = _start_server(book_path)
    try:
        # 非法 api_base 返回 400
        code, _ = _http_json(
            f"{base}/api/admin/llm/models",
            method="POST",
            body={"api_base": "ftp://invalid.local/v1"},
            headers=hdrs,
        )
        assert code == 400

        # 1. 新建渠道填写时直接通过 api_base + api_key 获取模型列表
        code, res = _http_json(
            f"{base}/api/admin/llm/models",
            method="POST",
            body={"api_base": f"http://127.0.0.1:{mock_port}/v1", "api_key": "sk-fetch-123"},
            headers=hdrs,
        )
        assert code == 200
        assert res["ok"] is True
        assert res["models"] == ["alpha-model", "beta-model", "zeta-model"]
        assert seen_auth[-1] == "Bearer sk-fetch-123"

        # 2. 保存渠道后，编辑模式下留空 api_key 可自动复用已保存 Key 获取模型列表
        code, _ = _http_json(
            f"{base}/api/admin/llm",
            method="POST",
            body={
                "action": "upsert",
                "channel": {
                    "id": "mock-ch",
                    "name": "Mock Provider",
                    "api_base": f"http://127.0.0.1:{mock_port}/v1",
                    "api_key": "sk-saved-secret",
                    "model": "custom-manual-model",
                    "timeout": 10,
                },
            },
            headers=hdrs,
        )
        assert code == 200

        code, res = _http_json(
            f"{base}/api/admin/llm/models",
            method="POST",
            body={"id": "mock-ch", "api_base": f"http://127.0.0.1:{mock_port}/v1", "api_key": ""},
            headers=hdrs,
        )
        assert code == 200
        assert res["ok"] is True
        assert res["models"] == ["alpha-model", "beta-model", "zeta-model"]
        assert seen_auth[-1] == "Bearer sk-saved-secret"

        # 3. 不可达地址快速返回 ok: false 且不泄露 Key
        t0 = time.monotonic()
        code, res = _http_json(
            f"{base}/api/admin/llm/models",
            method="POST",
            body={"api_base": "http://127.0.0.1:9/v1", "api_key": "sk-secret-do-not-leak"},
            headers=hdrs,
        )
        assert code == 200
        assert res["ok"] is False
        assert res["models"] == []
        assert "sk-secret-do-not-leak" not in res.get("error", "")
        assert time.monotonic() - t0 < 3.0
    finally:
        server.shutdown()
        server.server_close()
        mock_srv.shutdown()
        mock_srv.server_close()

    # 4. 环境变量显式提供默认渠道时，未建配置文件也能生成通用 default 渠道并打码展示
    monkeypatch.setenv("NOVEL_LLM_CONFIG", str(tmp_path / "env_only_channels.json"))
    monkeypatch.setenv("LLM_API_BASE", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-env-987654321")
    monkeypatch.setenv("LLM_MODEL", "custom-env-model")
    server_env, base_env = _start_server(book_path)
    try:
        code, data = _http_json(f"{base_env}/api/admin/llm", headers=hdrs)
        assert code == 200
        assert data["active"] == "default"
        assert data["enabled"] is True
        assert len(data["channels"]) == 1
        assert data["channels"][0]["id"] == "default"
        assert data["channels"][0]["api_key"] == "sk-e…4321"
        assert "sk-env-987654321" not in json.dumps(data, ensure_ascii=False)
    finally:
        server_env.shutdown()
        server_env.server_close()
