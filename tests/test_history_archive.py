import hashlib
import hmac
import json
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

import web  # noqa: E402

REAL_BOOK_PATH = os.path.join(PROJECT_ROOT, "books", "十日终焉")
REAL_ARCHIVE_PATH = os.path.join(REAL_BOOK_PATH, "问答库", "档案.jsonl")


def _count_lines(path: str) -> int:
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _sign(secret: str, question: str, answer: str, sources: str) -> str:
    payload = f"{question}\x1f{answer}\x1f{sources}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _http_json(url: str, method: str = "GET", body=None, headers=None):
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw}
        return e.code, parsed


@pytest.fixture()
def isolated_server(tmp_path, monkeypatch):
    initial_count = _count_lines(REAL_ARCHIVE_PATH)
    assert initial_count >= 84

    llm_cfg_path = tmp_path / "llm_config.json"
    llm_cfg_path.write_text(json.dumps({"active": "off", "channels": []}), encoding="utf-8")

    monkeypatch.setenv("NOVEL_ADMIN_TOKEN", "testtok")
    monkeypatch.setenv("NOVEL_LLM_CONFIG", str(llm_cfg_path))
    monkeypatch.setenv("LLM_ENABLED", "false")

    server = web.make_server(host="127.0.0.1", port=0, book_path=REAL_BOOK_PATH)

    # 将可写书目录隔离到 tmp_path，避免污染真实 档案.jsonl
    tmp_book = tmp_path / "book"
    tmp_qa_dir = tmp_book / "问答库"
    tmp_qa_dir.mkdir(parents=True, exist_ok=True)
    tmp_archive = tmp_qa_dir / "档案.jsonl"
    shutil.copy2(REAL_ARCHIVE_PATH, tmp_archive)
    server.RequestHandlerClass.kb.book_path = str(tmp_book)

    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", server, tmp_archive
    finally:
        server.shutdown()
        server.server_close()
        assert _count_lines(REAL_ARCHIVE_PATH) == initial_count


def test_index_html_no_stale_numbers(isolated_server):
    base_url, _, _ = isolated_server
    with urllib.request.urlopen(f"{base_url}/", timeout=10) as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
    assert "253" not in html
    assert "46.6万" not in html
    assert "shiri_qa_history_v1" in html
    assert "/api/archive/add" in html
    assert "/api/voices" in html


def test_ask_archive_hit_has_empty_sig(isolated_server):
    base_url, _, _ = isolated_server
    code, data = _http_json(
        f"{base_url}/api/ask",
        method="POST",
        body={"question": "齐夏的回响是什么"},
    )
    assert code == 200
    assert data["origin"] == "问答库"
    assert data.get("sig") == ""


def test_archive_add_exists_tamper_oob_and_admin_delete(isolated_server):
    base_url, _, tmp_archive = isolated_server
    initial_count = _count_lines(REAL_ARCHIVE_PATH)

    # 1. 非问答库问题返回非空 sig (或构造合法 sig)
    q = "黑羊最后打天虎的时候发生了什么"
    code, ask_res = _http_json(
        f"{base_url}/api/ask",
        method="POST",
        body={"question": q},
    )
    assert code == 200
    assert ask_res["origin"] in ("原文深度召回", "精读总集", "大模型生成")
    assert ask_res.get("sig")

    ans = "测试回答：齐夏在决战中以生生不息与谋略逆转局势。"
    src = "精读总集 · 测试块"
    valid_sig = _sign("testtok", q, ans, src)

    # 2. 篡改 answer -> 403
    code, _ = _http_json(
        f"{base_url}/api/archive/add",
        method="POST",
        body={"question": q, "answer": ans + "被篡改", "sources": src, "sig": valid_sig},
    )
    assert code == 403

    # 3. 伪造 sig -> 403
    code, _ = _http_json(
        f"{base_url}/api/archive/add",
        method="POST",
        body={"question": q, "answer": ans, "sources": src, "sig": "deadbeef"},
    )
    assert code == 403

    # 4. 越界问题 (即使合法 sig) -> 400
    oob_q = "今天北京天气怎么样适合出门吗"
    oob_sig = _sign("testtok", oob_q, "晴天", "")
    code, _ = _http_json(
        f"{base_url}/api/archive/add",
        method="POST",
        body={"question": oob_q, "answer": "晴天", "sources": "", "sig": oob_sig},
    )
    assert code == 400

    # 5. 合法 sig 加入问答库 -> 200 added
    code, add_res = _http_json(
        f"{base_url}/api/archive/add",
        method="POST",
        body={"question": q, "answer": ans, "sources": src, "sig": valid_sig},
    )
    assert code == 200
    assert add_res["status"] == "added"
    added_id = add_res["id"]
    assert _count_lines(str(tmp_archive)) == initial_count + 1

    # 6. 同一问题再次 /api/ask -> 命中问答库
    code, ask_after = _http_json(
        f"{base_url}/api/ask",
        method="POST",
        body={"question": q},
    )
    assert code == 200
    assert ask_after["origin"] == "问答库"
    assert ask_after["answer"] == ans
    assert ask_after.get("sig") == ""

    # 7. 再次 add -> exists
    code, exists_res = _http_json(
        f"{base_url}/api/archive/add",
        method="POST",
        body={"question": q, "answer": ans, "sources": src, "sig": valid_sig},
    )
    assert code == 200
    assert exists_res["status"] == "exists"
    assert exists_res["id"] == added_id

    # 8. 未带 token 访问 admin archive 接口 -> 401
    code, _ = _http_json(f"{base_url}/api/admin/archive?added_by=user&limit=50")
    assert code == 401
    code, _ = _http_json(
        f"{base_url}/api/admin/archive/delete",
        method="POST",
        body={"id": added_id},
    )
    assert code == 401

    # 9. 带 token 列出 added_by=user 条目
    code, list_res = _http_json(
        f"{base_url}/api/admin/archive?added_by=user&limit=50",
        headers={"X-Admin-Token": "testtok"},
    )
    assert code == 200
    assert any(item["id"] == added_id and item["question"] == q for item in list_res["items"])

    # 10. 带 token 删除该条目 -> 临时档案行数恢复 initial_count，再次提问不再命中问答库
    code, del_res = _http_json(
        f"{base_url}/api/admin/archive/delete",
        method="POST",
        body={"id": added_id},
        headers={"X-Admin-Token": "testtok"},
    )
    assert code == 200
    assert del_res["status"] == "deleted"
    assert _count_lines(str(tmp_archive)) == initial_count

    code, ask_deleted = _http_json(
        f"{base_url}/api/ask",
        method="POST",
        body={"question": q},
    )
    assert code == 200
    assert ask_deleted["origin"] != "问答库"


def test_archive_add_rate_limit_429(isolated_server):
    base_url, _, _ = isolated_server
    for i in range(20):
        q = f"齐夏在第{i + 1}次轮回的经历是什么"
        ans = f"测试回答第{i + 1}条"
        sig = _sign("testtok", q, ans, "测试出处")
        code, res = _http_json(
            f"{base_url}/api/archive/add",
            method="POST",
            body={"question": q, "answer": ans, "sources": "测试出处", "sig": sig},
        )
        assert code == 200, f"Unexpected status at iteration {i}: {code} {res}"

    q21 = "齐夏在第21次轮回的经历是什么"
    ans21 = "测试回答第21条"
    sig21 = _sign("testtok", q21, ans21, "测试出处")
    code, _ = _http_json(
        f"{base_url}/api/archive/add",
        method="POST",
        body={"question": q21, "answer": ans21, "sources": "测试出处", "sig": sig21},
    )
    assert code == 429
