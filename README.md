# 小说精读 · 语音问答工作区

把小说交给 AI 精读,之后随时提问,AI 用语音回答,并且支持**跨会话的连续对话**(后面说的话,它记得前面聊过的)。

## 快速上手

1. **放书**:把小说 txt 丢进来,告诉我"精读 xxx.txt",或自己跑:
   ```
   python3 .agents/skills/novel-companion/scripts/import_book.py 小说.txt 书名
   ```
   自动识别 GBK/UTF-8/BIG5 编码,统一存到 `books/书名/原文.txt`。
2. **精读**:对我说"精读《书名》"。短书直接通读;长书分块精读,产出 `分析/`(人物、情节时间线、主题风格、速查索引)和分块笔记 `notes/`。
3. **提问**:直接打字问,比如"主角为什么在第三章撒谎?"。我会查笔记和原文,给出有章节出处的回答,**并用语音读出来**。
4. **连续对话**:正常聊天即可。"他后来怎么样了?"这种追问在会话内天然记得;关掉终端后下次说"继续聊《书名》",我会先读 `books/书名/对话记录.md` 接上之前的话题。
5. **重复问题秒答 + 答案可溯源**:每轮问答存入 `books/书名/问答库/档案.jsonl`(问题、答案、章节出处、时间、命中次数)。同样的问题再问,直接复用旧答案秒答;质疑某个旧答案时说一声,我能按关键词翻出当时的问题、答案全文和依据章节,答错了也基于档案更正。

## 连续对话是怎么记住的

- 会话内:聊天上下文自然记得。
- 跨会话:每轮问答都追加到 `books/书名/对话记录.md`。新会话开始聊之前,我会先读记录的末尾部分恢复语境。所以**不要手动删改这个文件**,它是记忆本体。

## 批量自进化充库

用于精读完成后快速填充高频问答库:
1. **分批生成盲区问题**:针对人物动机、世界观规则、关键伏笔反转,规划批次问题清单(每批 20~50 题)。
2. **集中 grep 取证**:单条命令集中检索 `精读总集.md`(必要时查 `原文.txt`)统一取证。
3. **带出处作答**:形成口语化回答(150~450 字),严格注明章节出处。
4. **一次 add 归档**:批量调用 `qa_archive.py add` 写入 `问答库/档案.jsonl`(自动去重),并追加到 `对话记录.md`(免播报提速)。
5. **抽测改写问法**:每批随机抽选问题语义改写,通过 `qa_archive.py find` 验证高分命中(≥0.85),确保缓存有效。

## 语音说明

- **在线增强(推荐)**:默认用 edge-tts(需 `pip3 install edge-tts`),享受微软自然在线音色。默认女声晓晓 `zh-CN-XiaoxiaoNeural`,可选男声 `zh-CN-YunxiNeural` 等,调用 macOS 内置 `afplay` 播放。
- **离线兜底**:未安装 edge-tts 或断网时,自动降级调用 macOS 本地 `say` 命令播放(系统中文音色,离线即用)。
- 不想打扰别人,可以只生成文件:`python3 .agents/skills/novel-companion/scripts/speak.py "文本" --out 答案.mp3`。
- 想用语音提问:macOS 任意输入框连按两次 `Fn` 键启动听写,对着麦克风说话即可。

## 文件结构

```
小说/
  README.md                     # 本文件
  books/<书名>/
    原文.txt                    # 统一转成 UTF-8 的全文
    chunks/                     # 章节/字数切块文件(长书)
    notes/NNN.md                # 分块精读笔记(长书)
    精读总集.md                 # 笔记与分析的合并单文件,问答检索第一入口
    分析/                       # 精读四件套:
      人物.md                   # 人物档案与弧线
      情节与时间线.md            # 情节推进与时间轴
      主题与风格.md              # 核心立意与写作风格
      速查索引.md               # 主要人物/关键事件 -> 章节定位
    对话记录.md                  # 问答历史 = 跨会话记忆
    问答库/档案.jsonl            # 结构化问答存档(查重秒答与答案溯源)
  .agents/skills/novel-companion/
    SKILL.md                    # AI 的工作流程定义
    scripts/
      import_book.py            # 导入转码
      split_book.py             # 章节/字数切块
      qa_archive.py             # 问答库管理(归档/检索/去重/更新)
      speak.py                  # TTS 合成 + 播放(macOS / edge-tts)
      speak.ps1                 # 离线兜底 TTS

## 测试与质量保证 (Testing and Quality Assurance)

### 1. 依赖安装与准备
```bash
pip install -r requirements.txt
```

### 2. 本地执行全量测试
运行全部测试文件与 210 项测试用例：
```bash
python3 -m pytest tests/ -v
```

### 3. 单独调试特定文件或场景
```bash
# 运行特定测试文件
python3 -m pytest tests/test_web.py -v
python3 -m pytest tests/test_speak.py -v

# 隔离运行特定场景 (例如高并发与延迟场景 07)
python3 -m pytest tests/test_e2e_scenarios.py -k scenario_07 -v

# 验证脚本同步一致性
python3 -m pytest tests/test_script_sync.py -v
```

### 4. 代码覆盖率统计 (含跨进程子进程跟踪)
项目配置了 `.coveragerc` 与 `sitecustomize.py`，支持追踪 `subprocess.run` 派生的 CLI 进程 (`import_book.py`, `split_book.py`)：
```bash
# 使用 pytest-cov 统计覆盖率
python3 -m pytest --cov=scripts tests/

# 或使用标准 coverage 工具链
python3 -m coverage run --rcfile=.coveragerc -m pytest tests/
python3 -m coverage combine
python3 -m coverage report -m
```

### 5. Docker 容器构建与测试
```bash
# 使用 Dockerfile 构建测试镜像并运行容器内回归
docker build -t novel-tests -f Dockerfile .
docker run --rm -v "$PWD/test-results:/results" novel-tests

# 使用 docker-compose 启动测试服务
docker compose -f docker-compose.test.yml run --rm test
```

### 6. instabox 云沙箱 CI 运行
```bash
# 单箱全量回归 (自动提取并解析 JUnit XML 报告，清理沙箱)
bash scripts/ci/run-instabox.sh

# 五箱并行分片测试 (5台 instabox 并行，100% 覆盖全部用例，JUnit XML 校验)
bash scripts/ci/run-parallel.sh
```

### 7. 安全与网络说明
- Web 服务默认监听 `0.0.0.0:8080`，无登录鉴权机制，**严禁未经反向代理或鉴权直连公网**，仅限本机或可信内网访问。
- TTS 音色合成默认优先尝试微软 edge-tts；若无网络或未安装，会自动降级为 macOS say 离线发音或标准 PCM WAV 兜底。

```
