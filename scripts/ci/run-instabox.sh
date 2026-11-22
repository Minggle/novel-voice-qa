#!/usr/bin/env bash
# 单箱全量测试:instabox ci 模板箱内 docker build + pytest
# 用法: scripts/ci/run-instabox.sh [--keep]
set -euo pipefail
export INSTABOX_NO_UPDATE_CHECK=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

# 1) 组装暂存区(只带测试需要的文件与核心知识库,不带原文大文件)
STAGE="$(mktemp -d)/novel-ci"
mkdir -p "$STAGE/scripts" "$STAGE/tests" "$STAGE/books/十日终焉/问答库" "$STAGE/books/十日终焉/notes" "$STAGE/books/十日终焉/分析" "$STAGE/.agents/skills/novel-companion/scripts"
cp "$ROOT/Dockerfile" "$STAGE/"
cp "$ROOT/requirements.txt" "$STAGE/"
cp "$ROOT/.coveragerc" "$STAGE/" 2>/dev/null || true
cp "$ROOT/sitecustomize.py" "$STAGE/" 2>/dev/null || true
cp "$ROOT/.dockerignore" "$STAGE/"
cp "$ROOT/docker-compose.yml" "$STAGE/" 2>/dev/null || true
cp -r "$ROOT/scripts"/* "$STAGE/scripts/"
cp -r "$ROOT/scripts"/* "$STAGE/.agents/skills/novel-companion/scripts/"
cp "$ROOT"/tests/*.py "$STAGE/tests/"
cp "$ROOT/books/十日终焉/问答库/档案.jsonl" "$STAGE/books/十日终焉/问答库/"
cp "$ROOT/books/十日终焉/精读总集.md" "$STAGE/books/十日终焉/"
cp -r "$ROOT/books/十日终焉/notes"/* "$STAGE/books/十日终焉/notes/" 2>/dev/null || true
cp -r "$ROOT/books/十日终焉/分析"/* "$STAGE/books/十日终焉/分析/" 2>/dev/null || true

# 2) 建箱
NAME="novel-ci-$(date +%Y%m%d-%H%M%S)"
echo "==> 创建 instabox ci 箱: $NAME"
BOX_JSON="$(instabox create --template ci --name "$NAME" --delete-after 15m --json)"
BOX_ID="$(echo "$BOX_JSON" | python3 -c '
import json, re, sys
txt = sys.stdin.read()
m = re.search(r"\{.*\}", txt, re.S)
print(json.loads(m.group(0))["id"] if m else "")
')"
echo "==> machineId: $BOX_ID"

cleanup() {
  if [[ "$KEEP" == "1" ]]; then
    echo "==> --keep:箱已保留 $BOX_ID (删除: instabox rm $BOX_ID --yes)"
  else
    instabox rm "$BOX_ID" --yes >/dev/null 2>&1 || true
    echo "==> 箱已删除"
  fi
  rm -rf "$(dirname "$STAGE")" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 3) 推送暂存区到箱内 HOME
instabox push "$BOX_ID" "$STAGE" >/dev/null
echo "==> 已推送 $(find "$STAGE" -type f | wc -l | tr -d ' ') 个文件"

# 4) 箱内 docker build + pytest
set +e
instabox exec --timeout 300 "$BOX_ID" -- bash -c '
  set -e
  cd ~/novel-ci
  docker build -q -t novel-tests -f Dockerfile .
  mkdir -p results
  docker run --rm -v "$PWD/results:/results" novel-tests
'
RC=$?
set -e

# 5) 取回 junit 结果
if [[ $RC -eq 0 || -d "$ROOT/test-results" ]]; then
  mkdir -p "$ROOT/test-results"
  instabox pull "$BOX_ID" novel-ci/results "$ROOT/test-results" || true
  if [[ -f "$ROOT/test-results/results/results.xml" && ! -f "$ROOT/test-results/results.xml" ]]; then
    cp "$ROOT/test-results/results/results.xml" "$ROOT/test-results/results.xml" 2>/dev/null || true
  fi

  # 6) 校验 junit XML 结果
  if [[ -f "$ROOT/test-results/results.xml" ]]; then
    echo "==> 解析并校验 JUnit XML 报告..."
    python3 -c '
import sys, xml.etree.ElementTree as ET
try:
    tree = ET.parse("'"$ROOT"'/test-results/results.xml")
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    if not suites and "tests" in root.attrib:
        suites = [root]
    tests = sum(int(s.attrib.get("tests", 0)) for s in suites)
    failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
    errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
    skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
    print(f"==> JUnit XML 校验通过: tests={tests}, failures={failures}, errors={errors}, skipped={skipped}")
    if tests < 207 or failures > 0 or errors > 0:
        sys.exit(1)
except Exception as e:
    print(f"==> JUnit XML 校验失败: {e}", file=sys.stderr)
    sys.exit(1)
' || RC=1
  fi
fi

echo "==> pytest exit=$RC"
exit $RC
