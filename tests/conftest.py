"""pytest 公共 fixtures:合成编码样本、临时书目目录、脚本定位、音频校验辅助函数。"""
import io
import json
import os
import struct
import subprocess
import sys
import wave
from typing import Optional

import pytest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
_agent_scripts = os.path.join(_root, ".agents", "skills", "novel-companion", "scripts")
_app_scripts = os.path.join(_root, "scripts")
SCRIPTS_DIR = _app_scripts if os.path.isdir(_app_scripts) and os.path.isfile(os.path.join(_app_scripts, "qa_archive.py")) else _agent_scripts


def assert_valid_audio_payload(
    audio_bytes: bytes,
    expected_format: Optional[str] = None,
    allow_silent: bool = False,
    min_bytes: int = 100,
) -> None:
    """
    Validates that audio_bytes is genuine, playable audio and NOT a silent fallback.
    1. Size must be >= min_bytes (default 100).
    2. Format detection:
       - WAV: Begins with b"RIFF....WAVE", valid format chunk, valid 'data' chunk.
              Must extract PCM samples and verify non-zero amplitude:
              any(s != 0 for s in samples).
              If payload is all zeros (silent fallback), raise AssertionError.
       - MP3: Begins with ID3v2 tag (b"ID3") or valid MPEG audio frame sync (0xFF 0xEx).
              Must contain non-zero audio frame payload.
       - AIFF: Begins with b"FORM....AIFF/AIFC".
    3. If expected_format is provided, verify matching format.
    """
    if not isinstance(audio_bytes, (bytes, bytearray)):
        raise AssertionError(f"Expected bytes or bytearray, got {type(audio_bytes)}")
    if len(audio_bytes) < min_bytes:
        raise AssertionError(f"Audio payload too small: {len(audio_bytes)} bytes < {min_bytes} bytes")

    detected_format = None
    if audio_bytes.startswith(b"RIFF") and len(audio_bytes) >= 12 and audio_bytes[8:12] == b"WAVE":
        detected_format = "wav"
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                nchannels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                nframes = wf.getnframes()
                if nchannels < 1:
                    raise AssertionError(f"Invalid channel count: {nchannels}")
                if sampwidth not in (1, 2, 3, 4):
                    raise AssertionError(f"Unsupported sample width: {sampwidth}")
                if framerate <= 0:
                    raise AssertionError(f"Invalid frame rate: {framerate}")

                frames = wf.readframes(nframes)
                if not frames:
                    raise AssertionError("WAV contains no audio data frames")

                if not allow_silent:
                    if sampwidth == 2:
                        count = len(frames) // 2
                        samples = struct.unpack(f"<{count}h", frames[:count * 2])
                        if not any(s != 0 for s in samples):
                            raise AssertionError("Audio payload contains only zero-amplitude silent PCM samples")
                    elif sampwidth == 1:
                        if not any(b != 128 and b != 0 for b in frames):
                            raise AssertionError("Audio payload contains only zero-amplitude silent PCM samples")
                    else:
                        if not any(b != 0 for b in frames):
                            raise AssertionError("Audio payload contains only zero-amplitude silent PCM samples")
        except wave.Error as e:
            raise AssertionError(f"Corrupt WAV format: {e}")
    elif audio_bytes.startswith(b"ID3") or (len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0):
        detected_format = "mp3"
        offset = 0
        if audio_bytes.startswith(b"ID3") and len(audio_bytes) >= 10:
            id3_size = (
                ((audio_bytes[6] & 0x7F) << 21)
                | ((audio_bytes[7] & 0x7F) << 14)
                | ((audio_bytes[8] & 0x7F) << 7)
                | (audio_bytes[9] & 0x7F)
            )
            offset = 10 + id3_size
        audio_slice = audio_bytes[offset:] if offset < len(audio_bytes) else audio_bytes
        has_sync = False
        for i in range(min(len(audio_slice) - 1, 4096)):
            if audio_slice[i] == 0xFF and (audio_slice[i + 1] & 0xE0) == 0xE0:
                has_sync = True
                break
        if not audio_bytes.startswith(b"ID3") and not has_sync:
            raise AssertionError("MPEG sync word (0xFF 0xEx) not found in MP3 payload")

        non_zero_count = sum(1 for b in audio_slice[:8192] if b != 0)
        if non_zero_count < 10:
            raise AssertionError("Audio payload contains only zero-amplitude silent audio data")
    elif audio_bytes.startswith(b"FORM") and len(audio_bytes) >= 12 and audio_bytes[8:12] in (b"AIFF", b"AIFC"):
        detected_format = "aiff"
        data_slice = audio_bytes[12:]
        if not allow_silent and not any(b != 0 for b in data_slice):
            raise AssertionError("Audio payload contains only zero-amplitude silent AIFF samples")
    else:
        raise AssertionError(f"Unknown or corrupt audio format header: {audio_bytes[:16]!r}")

    if expected_format:
        if detected_format != expected_format.lower():
            raise AssertionError(f"Expected audio format '{expected_format}', got '{detected_format}'")


def run_script(name, args, **kw):
    """用 python3 跑技能脚本,返回 CompletedProcess。"""
    env = kw.pop("env", None)
    if env is None:
        env = os.environ.copy()

    coveragerc = os.path.join(_root, ".coveragerc")
    if os.path.isfile(coveragerc):
        if "COVERAGE_PROCESS_START" not in env:
            env["COVERAGE_PROCESS_START"] = coveragerc
        if "COVERAGE_FILE" not in env:
            env["COVERAGE_FILE"] = os.path.join(_root, ".coverage")

    current_pythonpath = env.get("PYTHONPATH", "")
    if _root not in current_pythonpath.split(os.pathsep):
        env["PYTHONPATH"] = f"{_root}{os.pathsep}{current_pythonpath}" if current_pythonpath else _root

    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, name), *args],
        capture_output=True, text=True, timeout=kw.pop("timeout", 120),
        env=env, **kw,
    )


CN_TEXT = """第一回 甄士隐梦幻识通灵 贾雨村风尘怀闺秀

满纸荒唐言,一把辛酸泪。
"""
CN_TRAD_TEXT = """第一回 甄士隱夢幻識通靈 賈雨村風塵懷閨秀

滿紙荒唐言,一把辛酸淚。
"""
EN_PREFACE = """PREFACE: This is an English preface for encoding detection tests.

"""
CN_BODY = """第一回 正文开始

桃李春风一杯酒,江湖夜雨十年灯。
"""


@pytest.fixture()
def workdir(tmp_path):
    return tmp_path


@pytest.fixture()
def gbk_file(tmp_path):
    p = tmp_path / "gbk.txt"
    p.write_bytes((EN_PREFACE + CN_TEXT).encode("gbk"))
    return p


@pytest.fixture()
def utf16_nobom_file(tmp_path):
    p = tmp_path / "utf16nobom.txt"
    p.write_bytes(CN_TEXT.encode("utf-16-le"))  # 无 BOM
    return p


@pytest.fixture()
def big5_file(tmp_path):
    p = tmp_path / "big5.txt"
    p.write_bytes(CN_TRAD_TEXT.encode("big5"))
    return p


@pytest.fixture()
def mixed_file(tmp_path):
    p = tmp_path / "mixed.txt"
    p.write_bytes((EN_PREFACE + CN_BODY).encode("utf-8"))
    return p


@pytest.fixture()
def chapter_file(tmp_path):
    p = tmp_path / "chapters.txt"
    parts = [f"第{i}回 标题{i}\n{CN_BODY * 30}\n" for i in range(1, 13)]
    p.write_text("".join(parts), encoding="utf-8")
    return p


@pytest.fixture()
def plain_file(tmp_path):
    p = tmp_path / "plain.txt"
    p.write_text(CN_BODY * 2000, encoding="utf-8")  # 无章节标记,约2万字
    return p


@pytest.fixture()
def book_dir(tmp_path):
    """临时书目目录,含 原文.txt 与 问答库/。"""
    d = tmp_path / "books" / "测试书"
    (d / "问答库").mkdir(parents=True)
    (d / "原文.txt").write_text(CN_TEXT * 100, encoding="utf-8")
    return d


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
