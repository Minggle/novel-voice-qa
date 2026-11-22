# 《十日终焉》智能语音问答系统

基于精读知识库与大模型的长篇小说智能语音问答应用，结合 **200 条人工核验问答库**、**精读总集深度检索**、**加密原文章节兜底**、**OpenAI 兼容大模型补充** 以及 **Edge-TTS 自然语音朗读**，同时适配桌面端与手机浏览器。

## 快速启动

先将仓库克隆到本地并进入项目目录：

```bash
git clone https://github.com/Minggle/novel-voice-qa.git
cd novel-voice-qa
```

### 方式一：本地 Python 启动

1. 创建虚拟环境并安装依赖（Python 3.9+，兼容 Debian / Ubuntu 的系统包保护机制）：
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
2. 启动 Web 服务：
   ```bash
   .venv/bin/python3 scripts/web.py --host 0.0.0.0 --port 8080
   ```
3. 浏览器打开 `http://localhost:8080` 即可使用。

常用启动参数：
- `--book books/十日终焉`：指定书籍目录（默认 `books/十日终焉`）
- `--port 8080`：指定监听端口（默认 `8080`）
- `--host 0.0.0.0`：指定监听地址（默认 `0.0.0.0`）
- `--auto-evolve`：开启检索结果自动写入问答库（默认关闭）

### 方式二：Docker Compose 启动

```bash
docker compose up web
```
启动后访问 `http://localhost:8080`。

## 功能使用说明

### 1. 前台问答与语音播报
- **三层检索问答**：优先命中本地 `问答库(已校验)`，其次检索 `精读总集` 与加密原文章节；若已配置 AI 渠道则结合检索资料生成自然口语化回答。
- **语音朗读与音色切换**：默认使用微软 Edge-TTS 在线自然音色（晓晓 / 云希 / 晓伊），断网或未安装时自动降级为 macOS `say` 本地音色或内置 PCM WAV。
- **手机端适配**：在手机浏览器中可通过左上角 `☰` 按钮呼出侧边栏，切换音色、选择预设高频问题、查看历史记录或进入管理页。
- **历史记录与「加入问答库」**：
  - 每次提问自动保存在浏览器本地 `localStorage`（最多保留 100 条），支持在侧边栏回看、单条删除或清空。
  - 对于由 AI 生成或原文检索得到的回答，可点击气泡下方的 **「＋ 加入问答库」** 将其固化到服务端档案库；后续再问同一问题将直接命中问答库秒答，无需再次调用大模型。

### 2. AI 渠道与问答后台管理（`/admin`）
- **获取管理员 Token**：首次启动服务时会在终端启动横幅打印管理后台 Token，并持久化到 `~/.novel-qa/admin_token`（亦可通过环境变量 `NOVEL_ADMIN_TOKEN` 指定）。
- **管理入口**：打开 `http://localhost:8080/admin`（或点击首页侧栏底部的「管理」链接），输入管理员 Token 后即可管理：
  - **新增 / 编辑 OpenAI 兼容渠道**：填写渠道 ID、显示名称、`API Base URL` 与 `API Key`。点击模型输入框旁的 **「获取模型」** 按钮可自动请求 `{api_base}/models` 拉取模型列表供下拉选择，也支持随时在输入框内手动自定义模型名称。
  - **热切换与测试**：支持一键测试渠道连通性、在线切换当前使用的大模型，或点击「关闭大模型」回退纯本地检索模式（配置保存于 `~/.novel-qa/llm_channels.json`）。
  - **维护用户加入的问答**：在「用户加入的问答」区块可浏览并一键删除误加入的条目。

### 3. 加密原文与常用工具脚本
仓库内的 [原文.txt.enc](books/十日终焉/原文.txt.enc) 经过 `gzip` + `AES-256-CBC (PBKDF2)` 压缩加密。Web 服务启动时会自动读取根目录 [config.json](config.json)（或环境变量 `NOVEL_BOOK_KEY`）并在**内存中**解密加载，不会在磁盘生成明文文件。

常用独立脚本位于 `scripts/`（`.agents/skills/novel-companion/scripts/` 同步保存供 Agent Skill 调用）：

```bash
# 导入新小说并自动转码为 UTF-8
python3 scripts/import_book.py 小说.txt 书名

# 按章节边界将长篇小说切分为分块文件
python3 scripts/split_book.py books/书名/原文.txt books/书名/chunks

# 加密 / 解密 / 校验原文文件
python3 scripts/book_crypto.py encrypt books/书名/原文.txt
python3 scripts/book_crypto.py decrypt books/书名/原文.txt.enc
python3 scripts/book_crypto.py verify  books/书名/原文.txt.enc

# 命令行管理问答档案库 (add / find / update / list)
python3 scripts/qa_archive.py find --book 十日终焉 --query "齐夏的回响是什么"

# 单独合成或播放语音
python3 scripts/speak.py "你好，欢迎来到终焉之地" --out output.mp3
```

## 常用环境变量

| 变量名 | 说明 | 默认值 |
|---|---|---|
| `HOST` / `PORT` | Web 服务监听地址与端口 | `0.0.0.0` / `8080` |
| `NOVEL_ADMIN_TOKEN` | 管理后台 `/api/admin/*` 鉴权 Token | 自动生成并存至 `~/.novel-qa/admin_token` |
| `NOVEL_LLM_CONFIG` | 大模型渠道配置文件路径 | `~/.novel-qa/llm_channels.json` |
| `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL` | 未建渠道配置文件时的环境变量默认渠道 | 空（默认关闭大模型） |
| `LLM_ENABLED` | 设为 `false` 可强制禁用环境变量默认渠道 | `true` |
| `NOVEL_BOOK_KEY` | 加密原文解密密码（优先级高于 `config.json`） | 读取 `config.json` 中的 `book_key` |
| `NOVEL_STARTUP_CONFIG` | 自定义启动配置文件路径 | 项目根目录 `config.json` |
| `NOVEL_AUTO_EVOLVE` | 设为 `true` 时自动将深层检索结果持久化到问答库 | `false` |

## 项目结构

```text
.
├── config.json                  # 启动配置文件（原文解密密码）
├── requirements.txt             # Python 依赖清单
├── Dockerfile                   # 容器构建配置
├── docker-compose.yml           # Compose 服务编排
├── scripts/
│   ├── web.py                   # Web 问答服务、管理后台与检索核心
│   ├── book_crypto.py           # 原文压缩加密与内存解密工具
│   ├── qa_archive.py            # 问答档案库 CLI 管理工具
│   ├── import_book.py           # 小说导入与编码转换
│   ├── split_book.py            # 长篇小说按章切块
│   ├── speak.py / speak.ps1     # 语音合成与播放脚本
├── books/十日终焉/
│   ├── 原文.txt.enc             # 加密原著全文
│   ├── 精读总集.md              # 结构化精读汇编
│   ├── 问答库/档案.jsonl        # 200 条精选校验问答库
│   ├── 分析/                    # 人物、情节与时间线、主题风格、速查索引
│   └── notes/                   # 40 篇分块精读笔记
└── .agents/skills/novel-companion/
    ├── SKILL.md                 # AI Agent 精读与问答工作流技能定义
    └── scripts/                 # 与 scripts/ 保持同步的脚本副本
```

## 安全提示
- 若将本服务部署到公网，请务必通过反向代理启用 HTTPS，并妥善保管管理员 Token（建议通过 `NOVEL_ADMIN_TOKEN` 覆盖随机生成值）。
