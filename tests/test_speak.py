"""speak.py:TTS 音色切换与降级契约(不播放,只合成到文件)。"""
import sys
import pytest

from conftest import run_script, assert_valid_audio_payload

# 常见音色:edge-tts 在线三音色 + macOS say 本地音色
EDGE_VOICES = ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural"]
SAY_VOICES = ["Tingting", "Sinji"]

IS_MAC = sys.platform == "darwin"


def _synth(voice, out, text="测试音色切换"):
    return run_script("speak.py", ["--voice", voice, "--out", str(out), text])


def _assert_synth_contract(r, out, expected_engines=None):
    """有 edge-tts:出文件;没有:干净降级(不许 traceback)。
    expected_engines: 允许的引擎名集合，用于按引擎明确断言。
    """
    if r.returncode == 0:
        assert out.exists() and out.stat().st_size >= 100
        assert_valid_audio_payload(out.read_bytes())
    else:
        assert "Traceback" not in r.stderr, r.stderr
        assert any(k in r.stderr for k in ("edge-tts", "edge_tts", "say", "pip3"))


def test_help_lists_voice_options():
    r = run_script("speak.py", ["--help"])
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "--voice" in out and "--rate" in out and "--out" in out


@pytest.mark.parametrize("voice", EDGE_VOICES)
def test_edge_voice_switch(voice, tmp_path):
    out = tmp_path / f"{voice}.mp3"
    _assert_synth_contract(_synth(voice, out), out)


@pytest.mark.parametrize("voice", SAY_VOICES)
def test_say_voice_switch(voice, tmp_path):
    import unittest.mock as mock
    from scripts.speak import SAY_TO_EDGE, synth
    assert voice in SAY_TO_EDGE, f"{voice} 必须存在反向映射 SAY_TO_EDGE"
    assert SAY_TO_EDGE[voice].endswith("Neural"), f"{voice} 映射目标非 Neural"

    out = tmp_path / f"{voice}.audio"
    _assert_synth_contract(_synth(voice, out), out)

    # 单元级别验证：Linux/无 say 环境下反向映射走 edge-tts
    fake_et = mock.MagicMock()
    captured = []
    class FakeComm:
        def __init__(self, text, v, rate):
            captured.append(v)
        async def save(self, p):
            with open(p, "wb") as f:
                f.write(b"\xff\xfb\x90\x44" + b"\x00" * 300)
    fake_et.Communicate = FakeComm
    mock_out = str(tmp_path / f"mock_{voice}.mp3")
    with mock.patch("scripts.speak.IS_MAC", False), \
         mock.patch("scripts.speak.CACHE_DIR", str(tmp_path)), \
         mock.patch.dict("sys.modules", {"edge_tts": fake_et}):
        engine = synth(f"测试{voice}反向映射", voice, mock_out, "+0%")
    assert engine == "edge-tts"
    assert captured[0] == SAY_TO_EDGE[voice]


def test_empty_text_rejected(tmp_path):
    r = run_script("speak.py", ["--out", str(tmp_path / "x.mp3"), "  "])
    assert r.returncode != 0
    assert "文本" in (r.stderr + r.stdout)


def test_file_input(tmp_path):
    src = tmp_path / "答案.txt"
    src.write_text("齐夏是回响者。", encoding="utf-8")
    out = tmp_path / "from_file.mp3"
    _assert_synth_contract(
        run_script("speak.py", ["--file", str(src), "--out", str(out)]), out
    )


def test_pure_python_wav_fallback(tmp_path):
    """测试纯 Python PCM 16-bit 16kHz WAV 降级生成 (无 edge-tts / say 环境)。"""
    import wave
    import unittest.mock as mock
    from scripts.speak import _generate_pcm_wav, synth
    out = tmp_path / "fallback.wav"
    engine = _generate_pcm_wav(str(out), duration_sec=0.5, sample_rate=16000)
    assert engine == "wav"
    assert out.exists()
    assert out.stat().st_size > 1000
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
    # 纯静音全零回退在 allow_silent=True 下通过，在 allow_silent=False 下必须被拦截
    audio_data = out.read_bytes()
    assert_valid_audio_payload(audio_data, expected_format="wav", allow_silent=True)
    with pytest.raises(AssertionError, match="zero-amplitude silent PCM samples"):
        assert_valid_audio_payload(audio_data, expected_format="wav", allow_silent=False)

    # 验证在 Linux 且无 edge_tts 时的 synth 降级为 wav
    import os
    mock_wav = str(tmp_path / "linux_no_edge.wav")
    with mock.patch("scripts.speak.IS_MAC", False), \
         mock.patch("scripts.speak.CACHE_DIR", str(tmp_path)), \
         mock.patch.dict("sys.modules", {"edge_tts": None}):
        eng = synth("降级测试", "Tingting", mock_wav, "+0%")
    assert eng in ("wav", "fallback-wav")
    assert os.path.isfile(mock_wav)

