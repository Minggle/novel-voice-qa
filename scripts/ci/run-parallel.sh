#!/usr/bin/env bash
# 五箱并行分片测试:覆盖全部 7 个测试文件与 209+ 测试用例,ci 模板,docker 内 pytest
# instabox free plan 允许 3 starts/min，因此首批并发 3 箱，间隔 62s 并发次批 2 箱
set -uo pipefail
export INSTABOX_NO_UPDATE_CHECK=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAGE_BASE="$(mktemp -d)"
LOG_DIR="$ROOT/test-results/parallel-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

SHARDS=(
  "tests/test_e2e_voice_qa.py"
  "tests/test_e2e_scenarios.py tests/test_web.py"
  "tests/test_qa_archive.py tests/test_split_book.py tests/test_script_sync.py"
  "tests/test_speak.py"
  "tests/test_import_book.py tests/test_l2_recall.py"
)

# 期望每个分片至少执行的用例数 (总计 155 + 28 + 12 + 9 + 6 = 210)
SHARD_EXPECTED_TESTS=(155 28 12 9 6)

cleanup() {
  rm -rf "$STAGE_BASE"
}
trap cleanup EXIT

run_shard() {
  local shard_targets="$1"
  local stage="$2"
  local log="$3"
  local xml="$4"
  local name="$5"
  local expected_tests="$6"
  local shard_id="$7"

  for attempt in 1 2 3; do
    echo "==> [分片 $shard_id: $shard_targets] 尝试 $attempt 启动 instabox 箱: $name"
    if instabox run --template ci --timeout 300s --name "$name" --push "$stage/" -- \
      bash -c "
        docker build -q -t novel-tests -f Dockerfile . && \
        echo '===DOCKER_IMAGE_INFO===' && \
        docker images novel-tests --format '{{.Repository}}:{{.Tag}} size={{.Size}} id={{.ID}}' && \
        echo '===DOCKER_IMAGE_INFO_END===' && \
        mkdir -p results && \
        (docker run --rm -v "\$PWD/results:/results" novel-tests python -m pytest $shard_targets -v --junitxml=/results/shard.xml || true) && \
        if [[ -f results/shard.xml ]]; then
          echo '===JUNIT_XML_START==='
          cat results/shard.xml; echo
          echo '===JUNIT_XML_END==='
        fi
      " > "$log" 2>&1; then

      # 提取并解析 JUnit XML
      python3 - "$log" "$xml" <<'PY'
import re, sys
text = open(sys.argv[1], encoding='utf-8', errors='replace').read()
m = re.search(r'===JUNIT_XML_START===\s*\n?(.*?)===JUNIT_XML_END===', text, re.S)
open(sys.argv[2], 'w', encoding='utf-8').write(m.group(1).strip() if m else '')
PY
      if python3 -c "
import sys, xml.etree.ElementTree as ET
try:
    tree = ET.parse('$xml')
    root = tree.getroot()
    suites = [root] if root.tag == 'testsuite' else root.findall('.//testsuite')
    if not suites and 'tests' in root.attrib:
        suites = [root]
    tests = sum(int(s.attrib.get('tests', 0)) for s in suites)
    failures = sum(int(s.attrib.get('failures', 0)) for s in suites)
    errors = sum(int(s.attrib.get('errors', 0)) for s in suites)
    skipped = sum(int(s.attrib.get('skipped', 0)) for s in suites)
    time = sum(float(s.attrib.get('time', 0.0)) for s in suites)
    if tests < $expected_tests:
        print(f'FAIL: tests ({tests}) < expected ($expected_tests)')
        sys.exit(1)
    if failures > 0 or errors > 0:
        print(f'FAIL: failures={failures}, errors={errors}')
        sys.exit(1)
    print(f'OK: tests={tests}, failures={failures}, errors={errors}, skipped={skipped}, time={time:.2f}s')
    sys.exit(0)
except Exception as e:
    print(f'ERROR: {e}')
    sys.exit(1)
"; then
        echo "==> [分片 $shard_id] 测试顺利通过且 JUnit 校验达标！"
        return 0
      fi
    fi

    if grep -q "rate_limited" "$log"; then
      echo "==> [分片 $shard_id] 遇到频率限制，等待 62 秒后重试..."
      sleep 62
      name="novel-s$shard_id-$attempt-$(date +%H%M%S)"
    else
      echo "==> [分片 $shard_id] 执行未达标，请查看日志 $log"
      return 1
    fi
  done
  return 1
}

for idx in "${!SHARDS[@]}"; do
  i=$((idx + 1))
  shard_targets="${SHARDS[$idx]}"
  expected_count="${SHARD_EXPECTED_TESTS[$idx]}"
  stage="$STAGE_BASE/shard-$i"
  mkdir -p "$stage/scripts" "$stage/tests" "$stage/books/十日终焉/问答库" "$stage/books/十日终焉/notes" "$stage/books/十日终焉/分析" "$stage/.agents/skills/novel-companion/scripts"
  cp "$ROOT/Dockerfile" "$stage/"
  cp "$ROOT/requirements.txt" "$stage/"
  cp "$ROOT/.coveragerc" "$stage/" 2>/dev/null || true
  cp "$ROOT/sitecustomize.py" "$stage/" 2>/dev/null || true
  cp "$ROOT/.dockerignore" "$stage/" 2>/dev/null || true
  cp "$ROOT/docker-compose.yml" "$stage/" 2>/dev/null || true
  cp -r "$ROOT/scripts"/* "$stage/scripts/"
  cp -r "$ROOT/scripts"/* "$stage/.agents/skills/novel-companion/scripts/"
  cp "$ROOT"/tests/*.py "$stage/tests/"
  cp "$ROOT/books/十日终焉/问答库/档案.jsonl" "$stage/books/十日终焉/问答库/"
  cp "$ROOT/books/十日终焉/精读总集.md" "$stage/books/十日终焉/"
  cp -r "$ROOT/books/十日终焉/notes"/* "$stage/books/十日终焉/notes/" 2>/dev/null || true
  cp -r "$ROOT/books/十日终焉/分析"/* "$stage/books/十日终焉/分析/" 2>/dev/null || true

  box_name="novel-s$i-$(date +%H%M%S)"
  log_file="$LOG_DIR/shard-$i.log"
  xml_file="$LOG_DIR/shard-$i.xml"
  echo "==> 派发分片 $i: $shard_targets (预期用例数: $expected_count)"
  run_shard "$shard_targets" "$stage" "$log_file" "$xml_file" "$box_name" "$expected_count" "$i" &

  # 前 3 箱直接并发，第 4、5 箱间隔 62s 启动以避开 3/min 限频
  if [[ $i -eq 3 ]]; then
    echo "==> 前 3 箱已并发启动，等待 62 秒以满足 instabox 频率限制后再启动后 2 箱..."
    sleep 62
  else
    sleep 2
  fi
done

echo "==> 等待所有并行分片测试完成..."
wait

echo "==> 所有分片运行结果汇总:"
FAILED=0
TOTAL_TESTS=0
TOTAL_FAILURES=0
TOTAL_ERRORS=0
TOTAL_SKIPPED=0

for xml in "$LOG_DIR"/*.xml; do
  [[ -f "$xml" ]] || continue
  shard_name="$(basename "$xml" .xml)"
  summary=$(python3 -c "
import sys, xml.etree.ElementTree as ET
try:
    tree = ET.parse('$xml')
    root = tree.getroot()
    suites = [root] if root.tag == 'testsuite' else root.findall('.//testsuite')
    if not suites and 'tests' in root.attrib:
        suites = [root]
    tests = sum(int(s.attrib.get('tests', 0)) for s in suites)
    failures = sum(int(s.attrib.get('failures', 0)) for s in suites)
    errors = sum(int(s.attrib.get('errors', 0)) for s in suites)
    skipped = sum(int(s.attrib.get('skipped', 0)) for s in suites)
    time = sum(float(s.attrib.get('time', 0.0)) for s in suites)
    status = 'PASS' if (failures == 0 and errors == 0 and tests > 0) else 'FAIL'
    print(f'{status}|{tests}|{failures}|{errors}|{skipped}|{time:.2f}s')
except Exception as e:
    print('ERROR|0|0|1|0|0.0s')
")
  IFS='|' read -r status tests failures errors skipped duration <<< "$summary"
  echo "  $shard_name: status=$status, tests=$tests, failures=$failures, errors=$errors, skipped=$skipped, time=$duration"
  TOTAL_TESTS=$((TOTAL_TESTS + tests))
  TOTAL_FAILURES=$((TOTAL_FAILURES + failures))
  TOTAL_ERRORS=$((TOTAL_ERRORS + errors))
  TOTAL_SKIPPED=$((TOTAL_SKIPPED + skipped))
  if [[ "$status" != "PASS" ]]; then
    FAILED=1
  fi
done

echo "==> 总计: tests=$TOTAL_TESTS, failures=$TOTAL_FAILURES, errors=$TOTAL_ERRORS, skipped=$TOTAL_SKIPPED"
if [[ $TOTAL_TESTS -lt 209 || $FAILED -ne 0 ]]; then
  echo "==> 警告: 存在未通过的分片或用例总数不足 209，请检查日志！"
  exit 1
fi
echo "==> 恭喜！5 台 instabox 并行分片测试全部成功通过 (209/209 用例验证)！"
exit 0
