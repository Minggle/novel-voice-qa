#!/usr/bin/env python3
"""文字转语音:edge-tts 在线合成(音色自然)优先,未装/失败自动降级 macOS say;
Windows 保留 MCI 播放。任何路径都不许裸 traceback。

用法:
  python3 speak.py "要读的文本"
  python3 speak.py --file 答案.txt
  python3 speak.py --file 答案.txt --voice zh-CN-YunxiNeural --rate +10%
  python3 speak.py "文本" --voice Tingting --out 保存.aiff   # 只存文件不播放

音色:
  edge-tts(在线): zh-CN-XiaoxiaoNeural 晓晓(默认)/ zh-CN-YunxiNeural 云希 /
                  zh-CN-XiaoyiNeural 晓伊
  macOS say(离线): Tingting 婷婷 / Sinji 新吉 / Mei-Jia 美嘉
"""
import argparse
import asyncio
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# edge-tts 音色 -> macOS say 音色(降级映射)
SAY_ALIAS = {
    "zh-CN-XiaoxiaoNeural": "Tingting",
    "zh-CN-XiaoyiNeural": "Mei-Jia",
    "zh-CN-YunxiNeural": "Sinji",
    "xiaoxiao": "Tingting",
    "xiaoyi": "Mei-Jia",
    "yunxi": "Sinji",
}
# macOS say 音色 -> edge-tts 音色(反向映射: 在 Linux 等无 say 环境下走 edge-tts)
SAY_TO_EDGE = {
    "Tingting": "zh-CN-XiaoxiaoNeural",
    "Sinji": "zh-CN-YunxiNeural",
    "Mei-Jia": "zh-CN-XiaoyiNeural",
}
EDGE_DEFAULT = "zh-CN-XiaoxiaoNeural"
CACHE_DIR = os.environ.get("NOVEL_TTS_CACHE", "/tmp/novel_tts_cache")
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except OSError:
    pass


def cache_key(text: str, voice: str, rate: str) -> str:
    return hashlib.md5(f"{voice}|{rate}|{text.strip()}".encode("utf-8")).hexdigest()


def _generate_pcm_wav(out_path: str, duration_sec: float = 1.0, sample_rate: int = 16000) -> str:
    """纯 Python 生成标准 PCM 16-bit 16kHz 单声道 WAV 文件 (第三级离线兜底)。"""
    n_frames = int(duration_sec * sample_rate)
    with wave.open(out_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        data = struct.pack("<" + ("h" * n_frames), *([0] * n_frames))
        wav_file.writeframes(data)
    return "wav"


def _fail(msg):
    print(f"[speak] {msg}", file=sys.stderr)
    sys.exit(1)


def play_audio(path: str) -> None:
    """按平台播放音频文件。"""
    if IS_MAC:
        if shutil.which("afplay"):
            subprocess.run(["afplay", path], check=True)
            return
        _fail("未找到 afplay,无法播放")
    if IS_WIN:
        import ctypes
        winmm = ctypes.WinDLL("winmm")

        def mci(cmd):
            buf = ctypes.create_unicode_buffer(512)
            code = winmm.mciSendStringW(cmd, buf, 511, 0)
            if code:
                raise RuntimeError(f"MCI 错误 {code}: {cmd}")

        full = os.path.abspath(path)
        mci(f'open "{full}" type mpegvideo alias zcodetts')
        try:
            mci("play zcodetts wait")
        finally:
            mci("close zcodetts")
        return
    for player in ("ffplay", "mpv", "aplay"):
        if shutil.which(player):
            if player == "ffplay":
                subprocess.run([player, "-nodisp", "-autoexit", path], check=False)
            else:
                subprocess.run([player, path], check=False)
            return
    _fail("当前系统没有可用播放器(仅生成文件请用 --out)")


def _rate_to_wpm(rate: str) -> int:
    """'+10%' / '-10%' / '+0%' -> say 的每分钟词数。"""
    base = 180
    try:
        pct = int(rate.strip().rstrip("%").lstrip("+"))
    except ValueError:
        return base
    return max(80, min(400, int(base * (1 + pct / 100))))


def _synth_say(text: str, voice: str, out: str, rate: str) -> str:
    if IS_MAC and shutil.which("say"):
        cmd = ["say", "-v", voice, "-r", str(_rate_to_wpm(rate)), "-o", out, text]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
            return "say"
        print(f"[speak] say 合成失败: {r.stderr.strip()}, 降级 WAV", file=sys.stderr)
    return _generate_pcm_wav(out)


def synth(text: str, voice: str, out: str, rate: str) -> str:
    """合成到 out, 返回实际使用的引擎 ('edge-tts', 'say', 'wav' 或 'cache')。
    三级降级链路:
      - Neural / SAY_ALIAS(edge-tts名) -> Edge-TTS -> macOS say -> fallback-wav
      - SAY_TO_EDGE(say名 Tingting/Sinji/Mei-Jia):
          有 say (macOS) -> say 优先 -> 失败降级 fallback-wav
          无 say (Linux) -> Edge-TTS 反向映射 -> 失败降级 fallback-wav
    非阻塞 MD5 缓存优先复用。
    """
    text = text.strip()
    if not text:
        _fail("没有要朗读的文本")

    ck = cache_key(text, voice, rate)
    # 检查缓存
    for ext in (".mp3", ".aiff", ".wav"):
        cpath = os.path.join(CACHE_DIR, ck + ext)
        if os.path.isfile(cpath) and os.path.getsize(cpath) > 0:
            try:
                shutil.copyfile(cpath, out)
                return "cache"
            except OSError:
                pass

    engine = "wav"

    # --- 分支 A: say 专属音色 (Tingting/Sinji/Mei-Jia) ---
    if voice in SAY_TO_EDGE:
        # 有 say (macOS): say 优先
        if IS_MAC and shutil.which("say"):
            engine = _synth_say(text, voice, out, rate)
        else:
            # 无 say (Linux/容器): 反向映射 -> edge-tts
            edge_voice = SAY_TO_EDGE[voice]
            try:
                import edge_tts as _et

                async def _save_say_edge():
                    await _et.Communicate(text, edge_voice, rate=rate).save(out)

                try:
                    asyncio.run(_save_say_edge())
                    if os.path.isfile(out) and os.path.getsize(out) > 0:
                        engine = "edge-tts"
                        try:
                            shutil.copyfile(out, os.path.join(CACHE_DIR, ck + ".mp3"))
                        except OSError:
                            pass
                        return engine
                except Exception as e:
                    print(f"[speak] edge-tts({edge_voice})合成失败({e}), 降级 fallback-wav", file=sys.stderr)
            except ImportError:
                print(f"[speak] edge-tts 未安装, 降级 fallback-wav", file=sys.stderr)
            # 最终兜底
            engine = _generate_pcm_wav(out)

    # --- 分支 B: Neural 音色 / SAY_ALIAS shorthand (edge-tts 优先) ---
    elif voice.endswith("Neural") or voice in SAY_ALIAS:
        try:
            import edge_tts
            edge_voice = voice if voice.endswith("Neural") else EDGE_DEFAULT

            async def _save():
                await edge_tts.Communicate(text, edge_voice, rate=rate).save(out)

            try:
                asyncio.run(_save())
                if os.path.isfile(out) and os.path.getsize(out) > 0:
                    engine = "edge-tts"
                    try:
                        shutil.copyfile(out, os.path.join(CACHE_DIR, ck + ".mp3"))
                    except OSError:
                        pass
                    return engine
            except Exception as e:
                print(f"[speak] edge-tts 合成失败({e}), 降级 say", file=sys.stderr)
        except ImportError:
            pass

        say_voice = SAY_ALIAS.get(voice, "Tingting")
        engine = _synth_say(text, say_voice, out, rate)

    # --- 分支 C: 其它原生 say 音色直通 ---
    else:
        engine = _synth_say(text, voice, out, rate)

    if os.path.isfile(out) and os.path.getsize(out) > 0:
        cache_ext = ".aiff" if engine == "say" else (".mp3" if engine == "edge-tts" else ".wav")
        try:
            shutil.copyfile(out, os.path.join(CACHE_DIR, ck + cache_ext))
        except OSError:
            pass
    return engine


def main():
    ap = argparse.ArgumentParser(description="文字转语音并播放(edge-tts 优先,say 兜底)")
    ap.add_argument("text", nargs="*", help="要朗读的文本")
    ap.add_argument("--file", help="从文件读取文本(UTF-8),优先于命令行文本")
    ap.add_argument("--voice", default=EDGE_DEFAULT,
                    help="音色。edge-tts: zh-CN-XiaoxiaoNeural(默认)/zh-CN-YunxiNeural/"
                         "zh-CN-XiaoyiNeural;say: Tingting/Sinji/Mei-Jia")
    ap.add_argument("--rate", default="+0%", help="语速,如 +10%% 或 -10%%")
    ap.add_argument("--out", help="保存为音频文件(mp3/aiff)而不播放")
    args = ap.parse_args()

    if args.file:
        try:
            with open(args.file, encoding="utf-8-sig") as f:
                text = f.read().strip()
        except OSError as e:
            _fail(f"读不到文件: {e}")
    else:
        text = " ".join(args.text).strip()
    if not text:
        _fail("没有要朗读的文本")

    if args.out:
        engine = synth(text, args.voice, args.out, args.rate)
        print(f"已保存({engine}): {args.out}")
        return

    fd, tmp = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        engine = synth(text, args.voice, tmp, args.rate)
        path = tmp
        if engine == "say":  # say 产出 aiff,换后缀更老实
            aiff = tmp + ".aiff"
            os.replace(tmp, aiff)
            path = aiff
        elif engine == "wav":
            wav = tmp + ".wav"
            os.replace(tmp, wav)
            path = wav
        play_audio(path)
    finally:
        for p in (tmp, tmp + ".aiff", tmp + ".wav"):
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    main()
