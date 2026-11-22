#!/usr/bin/env python3
"""《十日终焉》语音问答 Web 应用:
- 后端知识检索:
  - Level 1: books/十日终焉/问答库/档案.jsonl (精选问答库检索)
  - Level 2: books/十日终焉/精读总集.md (分块笔记与深度分析检索)
- TTS 语音播报:
  - 微软 Edge-TTS 在线自然音色: zh-CN-XiaoxiaoNeural (晓晓)、zh-CN-YunxiNeural (云希)、zh-CN-XiaoyiNeural (晓伊)
  - macOS 本地 say 兜底: Tingting (婷婷)、Sinji (新吉)、Mei-Jia (美嘉)
  - Linux/容器内置 PCM WAV 兜底
- 高性能轻量 HTTP 服务: Python 原生多线程 HTTP 服务器，零外部强制依赖。
"""
import argparse
import asyncio
import atexit
import collections
import concurrent.futures
import hashlib
import hmac
import http.server
import json
import math
import os
import re
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import wave
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

try:
    import edge_tts
except ImportError:
    edge_tts = None

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# 音色定义
VOICES = {
    "zh-CN-XiaoxiaoNeural": {"name": "微软 晓晓 (女声·推荐)", "engine": "edge-tts", "gender": "女"},
    "zh-CN-YunxiNeural": {"name": "微软 云希 (男声)", "engine": "edge-tts", "gender": "男"},
    "zh-CN-XiaoyiNeural": {"name": "微软 晓伊 (女声)", "engine": "edge-tts", "gender": "女"},
    "Tingting": {"name": "macOS 婷婷 (本地女声·兜底)", "engine": "say", "gender": "女"},
    "Sinji": {"name": "macOS 新吉 (本地男声·兜底)", "engine": "say", "gender": "男"},
    "Mei-Jia": {"name": "macOS 美嘉 (本地女声·兜底)", "engine": "say", "gender": "女"},
}
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# macOS say 音色 -> edge-tts 音色(反向映射: 在无 say 环境下走 edge-tts)
SAY_TO_EDGE = {
    "Tingting": "zh-CN-XiaoxiaoNeural",
    "Sinji": "zh-CN-YunxiNeural",
    "Mei-Jia": "zh-CN-XiaoyiNeural",
}

# 《十日终焉》核心角色与别名映射库 (涵盖主要人物与次要人物 80+ 人物实体)
CHARACTERS = {
    # 25位主要人物
    "齐夏": ["齐夏", "老齐", "白羊", "羊哥", "骗子"],
    "楚天秋": ["楚天秋", "会长", "天秋", "良人王"],
    "乔家劲": ["乔家劲", "阿劲", "老乔", "钵兰街"],
    "陈俊南": ["陈俊南", "老陈", "小陈", "小陈总", "大娃", "陈大娃"],
    "余念安": ["余念安", "念安"],
    "许流年": ["许流年"],
    "燕知春": ["燕知春"],
    "文巧云": ["文巧云"],
    "云瑶": ["云瑶"],
    "甜甜": ["甜甜", "张丽娟"],
    "林檎": ["林檎"],
    "舒画": ["舒画", "人鼠"],
    "李尚武": ["李尚武", "李警官"],
    "章晨泽": ["章晨泽", "章律师", "章莱娣"],
    "韩一墨": ["韩一墨"],
    "赵海博": ["赵海博", "赵医生"],
    "秦丁冬": ["秦丁冬"],
    "苏闪": ["苏闪"],
    "宋七": ["宋七", "宋明辉"],
    "钱五": ["钱五", "钱多多", "双生花"],
    "郑英雄": ["郑英雄", "郑应雄"],
    "江若雪": ["江若雪"],
    "张山": ["张山"],
    "肖冉": ["肖冉"],
    "魏杨": ["魏杨", "未羊"],
    # 次要人物与重要生肖神兽 (55+ 实体)
    "金元勋": ["金元勋"],
    "老吕": ["老吕", "吕凤先"],
    "童婵": ["童姨", "童婵", "小婵", "童阿姨"],
    "陆潇潇": ["潇潇", "陆潇潇"],
    "周六": ["周六", "周末", "六姐"],
    "老孙": ["老孙"],
    "老邓": ["老邓"],
    "白九": ["白九"],
    "姜十": ["姜十"],
    "罗十一": ["罗十一", "罗仇"],
    "王八": ["王八"],
    "邱十六": ["邱十六"],
    "胡亦然": ["白虎", "胡亦然"],
    "吴萱": ["玄武", "吴萱"],
    "小严": ["朱雀", "小严"],
    "金丝猴": ["金丝猴"],
    "李思维": ["李思维", "李观音"],
    "万财": ["万财"],
    "顾禹": ["顾禹"],
    "程敖宇": ["程敖宇", "小程"],
    "九仔": ["九仔", "九哥"],
    "荣爷": ["荣爷", "赌鬼荣"],
    "钟震": ["钟震"],
    "李香玲": ["李香玲"],
    "萱萱": ["萱萱"],
    "周正龙": ["周正龙"],
    "聂北": ["聂北", "老聂"],
    "孙佳齐": ["孙佳齐", "小孙"],
    "宁婉儿": ["宁婉儿"],
    "马屠户": ["马屠户"],
    "张强": ["张强", "人羊"],
    "陈婷": ["陈婷"],
    "曲志强": ["曲志强", "曲哥"],
    "张华南": ["张华南"],
    "天龙": ["天龙"],
    "青龙": ["青龙"],
    "地虎": ["地虎", "赔钱虎"],
    "黑羊": ["黑羊", "地羊"],
    "地蛇": ["地蛇", "白蛇"],
    "地猴": ["地猴", "肥马骝"],
    "地鼠": ["地鼠", "社畜鼠"],
    "地猪": ["地猪"],
    "地龙": ["地龙"],
    "人蛇": ["人蛇", "死蛇"],
    "人猪": ["人猪"],
    "天蛇": ["天蛇"],
    "天狗": ["天狗"],
    "天马": ["天马"],
    "天虎": ["天虎"],
    "天牛": ["天牛"],
    "天兔": ["天兔"],
    "地兔": ["地兔"],
    "天猪": ["天猪"],
    "天猴": ["天猴"],
    "人猴": ["人猴"],
    "穷奇": ["穷奇"],
    "蝼蚁王": ["蝼蚁王", "蚁后"],
    "修鞋赵": ["修鞋赵", "赵大爷"],
    "庄姐": ["庄姐"],
    "刘二十一": ["刘二十一"],
    "宁十八": ["宁十八"],
}

# 核心概念与设定词 (40+ 核心概念)
CONCEPTS = {
    "极道": ["极道", "极道组织", "极道起源"],
    "天堂口": ["天堂口"],
    "终焉之地": ["终焉之地", "终焉之地的目的", "十日终焉"],
    "十日之约": ["十日之约"],
    "回响": ["回响", "生生不息", "破万法", "巧物", "强运", "替罪", "灵视", "化形", "离析", "夺心魄", "招魂", "招灾", "七黑剑", "探囊", "魂迁", "赝品", "双生花", "天行健", "激发", "因果", "灵嗅", "巨响", "爆燃", "缄默", "不灭", "劲风", "茂木", "忘忧", "巨化", "赤炎", "硬化", "无垢", "显灵", "嫁祸", "传音", "原物", "傀儡", "业力", "致哀", "跃迁", "远播", "灵触", "灵闻", "隐匿", "祸水", "治愈"],
    "道": ["3600道", "三千六百道", "集道", "眼球", "道到底", "道是", "3600"],
    "生肖": ["生肖", "生肖体系", "生肖等级", "生肖游戏", "生肖飞升", "飞升对赌", "人级", "地级", "天级", "神兽", "四神兽"],
    "经典游戏": ["木牛流马", "四情扇", "说谎者游戏", "双向门", "黑白棋", "神之手", "盲盒", "雨后春笋", "说谎者", "是与非", "仓库寻道", "跷跷板", "少数与多数", "灾厄年", "朔望月", "猫鼠游戏", "蓬莱", "狭路相逢"],
    "洗牌": ["洗牌", "大洗牌"],
    "轮回": ["轮回"],
    "主题立意": ["执念", "执念造物", "核心主题", "立意", "想念即相见", "潜意识之信", "新世界", "造物"],
    "谎言欺诈": ["谎言", "辩证法", "欺诈", "骗局", "最终欺诈", "入梦"],
    "仓颉棋": ["仓颉棋", "弑青龙"],
    "原住民": ["原住民", "蝼蚁"],
    "时刻": ["天马时刻", "天蛇时刻"],
    "城市": ["玉城", "涡城", "道城", "桃源"],
    "设施组织": ["列车", "极乐钱庄", "悲伤会", "悲伤互助会", "猫队", "猫佣兵团", "面试房间", "阴魂管理局", "血肉之城", "冷冻眼球"],
    "暗号线索": ["咚咚咚", "清香", "女娲", "颠覆之始", "YNA"],
    "参与者": ["参与者"],
    "毁钟": ["毁钟", "巨钟"],
}

# 生肖体系与神兽专有词表
ALL_ZODIACS_AND_BEASTS = {
    "生肖", "生肖游戏", "人级", "地级", "天级", "神兽", "四神兽", "青龙", "白虎", "朱雀", "玄武",
    "人羊", "地羊", "天羊", "黑羊", "白羊", "人鼠", "地鼠", "天鼠", "人牛", "地牛", "天牛",
    "人虎", "地虎", "天虎", "人兔", "地兔", "天兔", "人龙", "地龙", "天龙", "人蛇", "地蛇",
    "天蛇", "白蛇", "人马", "地马", "天马", "人猴", "地猴", "天猴", "人鸡", "地鸡", "天鸡",
    "人狗", "地狗", "天狗", "人猪", "地猪", "天猪", "穷奇",
}

# 40篇分块笔记章节映射速查表 (依据 chunks/MANIFEST.txt 锚点)
BLOCK_TO_CHAPTERS = {
    1: "第1-33章", 2: "第34-67章", 3: "第68-101章", 4: "第102-135章", 5: "第136-168章",
    6: "第169-201章", 7: "第202-235章", 8: "第236-268章", 9: "第269-302章", 10: "第303-337章",
    11: "第338-372章", 12: "第373-405章", 13: "第406-440章", 14: "第441-475章", 15: "第476-509章",
    16: "第510-544章", 17: "第545-580章", 18: "第581-616章", 19: "第617-651章", 20: "第652-686章",
    21: "第687-721章", 22: "第722-757章", 23: "第758-793章", 24: "第794-829章", 25: "第830-865章",
    26: "第866-900章", 27: "第901-935章", 28: "第936-971章", 29: "第972-1006章", 30: "第1007-1042章",
    31: "第1043-1077章", 32: "第1078-1112章", 33: "第1113-1148章", 34: "第1149-1183章", 35: "第1184-1219章",
    36: "第1220-1255章", 37: "第1256-1291章", 38: "第1292-1326章", 39: "第1327-1357章", 40: "第1358-1359章",
}

# 问答意图属性分类
ASPECTS = {
    "identity": ["身份", "是谁", "存不存在", "到底是啥", "到底是谁", "真身", "到底存不存在"],
    "echo": ["回响", "能力", "神力", "破万法", "生生不息", "巧物", "替罪", "强运", "灵视"],
    "ending": ["结局", "死了没", "去哪了", "下场", "死了没有"],
    "age": ["几岁", "多大", "年龄"],
    "plan": ["计划", "布局", "目的", "打算"],
    "rules": ["规则", "机制", "运作", "等级", "区别", "怎么玩", "制度", "晋升", "怎么破", "破解", "怎么赢", "通关"],
}

# 问句模板词与常用停用词
TEMPLATE_WORDS = {
    "什么", "为什", "怎么", "是谁", "的谁", "多少", "哪些", "没有", "是不是",
    "是否", "可以", "怎样", "如何", "哪个", "请问", "知道", "介绍", "简单",
    "一下", "到底",
}

# 结构性泛化词与停用词库 (杜绝日常提问通过单字/通用词重合渗透)
GENERIC_STRUCTURE_STOP_WORDS = {
    "时间", "分析", "顺序", "主要", "人物", "工作", "太阳", "十日", "文件", "笔记", "检索",
    "的天", "开始", "回收", "间线", "要人", "剧情", "事件", "总结", "结局", "目录", "章节",
    "总集", "速查", "大事件", "精读", "分块", "小节", "线索", "说明", "地点", "动态",
    "重要", "对话", "金句", "悬念", "伏笔", "概论", "如今", "历史", "世界", "发展",
    "形象", "塑造", "管理", "安排", "作品", "文学", "商业", "原理", "每天", "天气",
    "起始", "本块", "全部", "架构", "配角", "内容", "介绍", "情况", "详细", "具体",
    "终焉", "计划", "破坏", "注定", "杀手", "手锏", "持续", "崩坏", "城市", "开局",
    "每个", "个人", "人的", "人生", "不同", "物动", "利用", "效率", "提高", "生活",
    "修养", "命运", "希望", "我们", "要对", "大家", "自己",
}

# 精读总集通用结构标题 (非小说剧情实体，不可单作为相关性依据)
GENERIC_STRUCTURE_HEADINGS = {
    "主要人物", "次要人物", "重要配角", "本块事件", "本块人物索引", "人物速查",
    "伏笔与悬念", "全书架构", "分弧剧情", "叙事风格", "大事件速查", "时间与地点线索",
    "时间线对照", "核心主题", "概念与设定速查", "组织与阵营", "贯穿悬念与回收",
    "速查索引", "重要对话金句", "阅读提示", "一句话定位", "主题与风格", "情节与时间线",
    "人物档案", "精读总集", "分块笔记", "人物动态",
}


def norm_text(s: str) -> str:
    """归一化:去标点、小写。"""
    if not s:
        return ""
    return re.sub(r"[^0-9a-zA-Z一-鿿]+", "", s).lower()


def get_bigrams(s: str) -> set:
    s = norm_text(s)
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def get_content_bigrams(s: str) -> set:
    return {b for b in get_bigrams(s) if not any(w in b for w in TEMPLATE_WORDS)}


def tokenize_bigrams(s: str) -> List[str]:
    """字符级 Bigram 切分，兼顾单字覆盖。"""
    s_norm = norm_text(s)
    if not s_norm:
        return []
    if len(s_norm) == 1:
        return [s_norm]
    return [s_norm[i:i + 2] for i in range(len(s_norm) - 1)]


def extract_chars(text: str) -> set:
    found = set()
    for name, aliases in CHARACTERS.items():
        if any(a in text for a in aliases):
            found.add(name)
    return found


def extract_aspects(text: str) -> set:
    found = set()
    for asp, words in ASPECTS.items():
        if any(w in text for w in words):
            found.add(asp)
    return found


def clean_markdown(text: str) -> str:
    """清理 Markdown 符号，生成纯文本口语化字符串。"""
    # 优先清理表格分隔行与连续连字符/破折号，防止 TTS 朗读卡顿
    text = re.sub(r"^\s*\|?[-:| ]+\|?\s*$", " ", text, flags=re.M)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"^\s*[-*•]\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s*", "", text, flags=re.M)
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"[-—]{2,}", " ", text)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(本块事件|人物动态|伏笔与悬念|重要对话金句|时间与地点线索|主要人物|次要人物|大事件速查|概念与设定速查)\s*", "", text)
    return text


def extract_standardized_citations(text: str) -> List[str]:
    """从文本中提取规范化的章节出处 (第X章, 第X-Y章, 块编号)。"""
    citations = []
    m = re.search(r"起始章节:第\s*(\d+)\s*章.*?第\s*(\d+)\s*章", text)
    if m:
        citations.append(f"第{m.group(1)}-{m.group(2)}章")
    matches = re.findall(r"第\s*(\d+)(?:\s*[-~至到、]\s*(\d+))?\s*章", text)
    for start, end in matches:
        if end:
            citations.append(f"第{start}-{end}章")
        else:
            citations.append(f"第{start}章")
    bare_chap_matches = re.findall(r"[（\(](\d{3,4})\s*[-~至到]\s*(\d{3,4})[）\)]", text)
    for start, end in bare_chap_matches:
        citations.append(f"第{start}-{end}章")

    # 提取块编号并展开: 例如 "块 015、032-034", "块 022-023", "块 026、039", "块 013"
    block_sections = re.findall(r"块\s*([0-9\s、,~至到\-]+)", text)
    for bs in block_sections:
        parts = re.split(r"[、,~至到\s]+", bs.strip())
        for p in parts:
            if not p:
                continue
            range_match = re.match(r"^(\d+)\s*[-~至到]\s*(\d+)$", p)
            if range_match:
                b_start = int(range_match.group(1))
                b_end = int(range_match.group(2))
                for b_num in range(b_start, b_end + 1):
                    if b_num in BLOCK_TO_CHAPTERS:
                        citations.append(BLOCK_TO_CHAPTERS[b_num])
                    citations.append(f"块 {b_num:03d}")
            elif p.isdigit():
                b_num = int(p)
                if b_num in BLOCK_TO_CHAPTERS:
                    citations.append(BLOCK_TO_CHAPTERS[b_num])
                citations.append(f"块 {b_num:03d}")

    return list(dict.fromkeys(citations))


def format_oral_answer(content: str, citation: str) -> str:
    """组织 150~450 字口语化纯文本回答，末尾附带出处。"""
    cleaned = clean_markdown(content)
    cite_suffix = f"（出处：{citation}）" if citation else ""
    max_len = 420 - len(cite_suffix)
    if len(cleaned) > max_len:
        truncated = cleaned[:max_len]
        last_punct = max(truncated.rfind("。"), truncated.rfind("；"), truncated.rfind("！"))
        if last_punct > 150:
            cleaned = truncated[:last_punct + 1]
        else:
            cleaned = truncated.rstrip("，、 ") + "。"
    ans = cleaned + cite_suffix
    return ans


# ==========================================
# 0. 小说实体映射与大模型生成层 (OpenAI 兼容 LLM)
# ==========================================

ENTITY_MAP: Dict[str, List[str]] = {
    "齐夏": ["齐夏", "老齐", "白羊", "骗子"],
    "白羊": ["白羊", "生肖白羊", "羊哥"],
    "余念安": ["余念安", "念安", "安安"],
    "楚天秋": ["楚天秋", "会长", "天秋", "良人王"],
    "乔家劲": ["乔家劲", "阿劲", "老乔", "钵兰街"],
    "陈俊南": ["陈俊南", "老陈", "小陈", "小陈总", "大娃", "陈大娃"],
    "云瑶": ["云瑶"],
    "甜甜": ["甜甜", "张丽娟"],
    "江若雪": ["江若雪", "若雪"],
    "燕知春": ["燕知春", "知春"],
    "文巧云": ["文巧云", "巧云"],
    "林檎": ["林檎"],
    "李尚武": ["李尚武", "李警官", "李强"],
    "章晨泽": ["章晨泽", "章律师", "章莱娣"],
    "韩一墨": ["韩一墨"],
    "郑英雄": ["郑英雄"],
    "钱五": ["钱五"],
    "魏杨": ["魏杨", "未羊"],
    "苏闪": ["苏闪"],
    "许流年": ["许流年"],
    "赵医生": ["赵医生"],
    "肖冉": ["肖冉"],
    "舒画": ["舒画"],
    "秦丁冬": ["秦丁冬"],
    "潇潇": ["潇潇", "陆潇潇"],
    "周六": ["周六", "周末"],
    "罗十一": ["罗十一"],
    "地虎": ["地虎", "赔钱虎"],
    "地羊": ["地羊", "黑羊"],
    "地龙": ["地龙"],
    "地马": ["地马"],
    "地猪": ["地猪"],
    "天龙": ["天龙"],
    "青龙": ["青龙"],
    "朱雀": ["朱雀"],
    "玄武": ["玄武"],
    "白虎": ["白虎"],
    "金元勋": ["金元勋"],
    "姜十": ["姜十"],
    "邱十六": ["邱十六"],
    "宋七": ["宋七"],
    "仓颉": ["仓颉"],
    "金丝猴": ["金丝猴"],
    "张山": ["张山"],
    "白九": ["白九"],
    "吴萱": ["吴萱", "萱子"],
    "地狗": ["地狗"],
    "地蛇": ["地蛇"],
    "地牛": ["地牛"],
    "地猴": ["地猴"],
    "地兔": ["地兔"],
    "地鸡": ["地鸡"],
    "天牛": ["天牛"],
    "白蛇": ["白蛇"],
    "王八": ["王八"],
    "顾禹": ["顾禹"],
    "童阿姨": ["童阿姨"],
    "小眼镜": ["小眼镜"],
}


def extract_query_entities(query: str) -> List[str]:
    """从问题中识别出现的核心小说实体列表，严格按照问题中出现的顺序排序。"""
    found = []
    for primary, aliases in ENTITY_MAP.items():
        positions = [query.find(a) for a in aliases if a in query]
        if positions:
            min_pos = min(positions)
            found.append((min_pos, primary))
    found.sort(key=lambda x: x[0])
    return [item[1] for item in found]


def chunk_matches_entities(chunk_text: str, entities: List[str]) -> bool:
    """多实体绑定校验: 语料块必须同时包含所有指定实体(或其别名)，杜绝跨实体张冠李戴。"""
    for ent in entities:
        aliases = list(ENTITY_MAP.get(ent, [ent]))
        if ent == "齐夏" and "白羊" not in entities:
            aliases.extend(["白羊", "羊哥"])
        elif ent == "白羊" and "齐夏" not in entities:
            aliases.extend(["齐夏", "羊哥"])
        if not any(a in chunk_text for a in aliases):
            return False
    return True


DEFAULT_LLM_KEY = ""
DEFAULT_LLM_BASE = ""
DEFAULT_LLM_MODEL = ""

_CHANNEL_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def get_llm_config_path() -> str:
    env_path = os.environ.get("NOVEL_LLM_CONFIG", "").strip()
    if env_path:
        return os.path.expanduser(env_path)
    return os.path.expanduser("~/.novel-qa/llm_channels.json")


def mask_api_key(key: Optional[str]) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "****"
    return f"{k[:4]}…{k[-4:]}"


def _default_llm_config() -> Dict[str, Any]:
    api_key = os.environ.get("LLM_API_KEY", DEFAULT_LLM_KEY).strip()
    api_base = os.environ.get("LLM_API_BASE", DEFAULT_LLM_BASE).strip().rstrip("/")
    model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL).strip()
    try:
        timeout = float(os.environ.get("LLM_TIMEOUT", 11.0))
    except (ValueError, TypeError):
        timeout = 11.0
    enabled_env = os.environ.get("LLM_ENABLED", "true").lower() not in ("0", "false", "no", "off")
    if api_base and api_key:
        return {
            "active": "default" if enabled_env else "off",
            "channels": [
                {
                    "id": "default",
                    "name": "默认渠道",
                    "api_base": api_base,
                    "api_key": api_key,
                    "model": model,
                    "timeout": timeout,
                },
            ],
        }
    return {
        "active": "off",
        "channels": [],
    }


def load_llm_config() -> Dict[str, Any]:
    path = get_llm_config_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("channels"), list):
                active = str(data.get("active") or "off")
                return {"active": active, "channels": data["channels"]}
        except Exception as e:
            print(f"[LLMConfig] 读取配置文件失败，回退默认配置: {e}", file=sys.stderr)
    return _default_llm_config()


def _atomic_write_secret_file(path: str, content: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".novel_tmp_", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def save_llm_config(cfg: Dict[str, Any]) -> None:
    path = get_llm_config_path()
    _atomic_write_secret_file(path, json.dumps(cfg, ensure_ascii=False, indent=2))


def resolve_admin_token() -> str:
    env_tok = os.environ.get("NOVEL_ADMIN_TOKEN", "").strip()
    if env_tok:
        return env_tok
    token_path = os.path.expanduser("~/.novel-qa/admin_token")
    if os.path.isfile(token_path):
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                return tok
        except Exception:
            pass
    tok = secrets.token_urlsafe(24)
    _atomic_write_secret_file(token_path, tok + "\n")
    return tok

ORAL_SYSTEM_PROMPT = """你是一位熟悉《十日终焉》小说原著与精读设定的智能问答助手。你的回答将直接进行TTS语音播报，必须严格遵守以下规则：
1. 开门见山：第一句话必须直接正面回答问题的核心（如回响名称及效果、核心身份、真实关系、终极目的），严禁倒背生平流水账或冗长铺垫。
2. 规范术语：准确使用原著规范核心词汇（例如：余念安是齐夏深爱的妻子；3600道是天龙编织的骗局与谎言）。
3. 纯文本口语表达：语言自然流畅，适合听觉收听，绝对不要出现任何Markdown标记符号（严禁使用井号、星号、中括号、破折号列表等）。
4. 严禁倾倒名单：严禁直接复读或倾倒多个人物索引名单。
5. 篇幅控制：总字数控制在150到350字之间（严禁超过450字）。
6. 章节出处：回答末尾标准注明参考章节，格式形如“（出处：第X-Y章）”。"""


class LLMClient:
    """OpenAI 兼容的大模型生成客户端，通过环境变量或管理后台配置接入。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 11.0,
    ):
        self.api_key = (
            api_key if api_key is not None else os.environ.get("LLM_API_KEY", DEFAULT_LLM_KEY)
        ).strip()
        self.api_base = (
            api_base if api_base is not None else os.environ.get("LLM_API_BASE", DEFAULT_LLM_BASE)
        ).strip().rstrip("/")
        self.model = (
            model if model is not None else os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
        ).strip()
        self.timeout = float(os.environ.get("LLM_TIMEOUT", timeout))
        enabled_env = os.environ.get("LLM_ENABLED", "true").lower() not in ("0", "false", "no", "off")
        self.enabled = bool(enabled_env and self.api_key and self.api_base)
        self._cache: Dict[str, str] = {}
        self._lock = threading.Lock()

    def reconfigure(
        self,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """在锁保护下原地更新连接配置并清空内存缓存。"""
        with self._lock:
            if api_base is not None:
                self.api_base = api_base.rstrip("/")
            if api_key is not None:
                self.api_key = api_key
            if model is not None:
                self.model = model
            if timeout is not None:
                self.timeout = float(timeout)
            if enabled is not None:
                self.enabled = bool(enabled)
            self._cache.clear()

    def test_connection(self) -> Dict[str, Any]:
        """直接调用 /chat/completions 测试连通性，不写缓存也不受长度过滤限制。"""
        if not self.api_base or not self.api_key:
            return {"ok": False, "latency_ms": 0, "error": "缺少 api_base 或 api_key"}
        if not self.model:
            return {"ok": False, "latency_ms": 0, "error": "缺少 model 配置"}

        endpoint = f"{self.api_base}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "请简短回复：OK"}],
            "temperature": 0.0,
            "max_tokens": 32,
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data_bytes,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                latency_ms = int((time.monotonic() - t0) * 1000)
                if resp.status != 200:
                    return {"ok": False, "latency_ms": latency_ms, "error": f"HTTP {resp.status}"}
                resp_data = json.loads(resp.read().decode("utf-8"))
                choices = resp_data.get("choices") or []
                content = ""
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message") or {}
                    content = (msg.get("content") or "").strip()
                if content:
                    return {"ok": True, "latency_ms": latency_ms, "sample": content[:120]}
                return {"ok": False, "latency_ms": latency_ms, "error": "响应中 choices 缺少 content"}
        except Exception as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {"ok": False, "latency_ms": latency_ms, "error": str(e)}

    def list_models(self) -> Dict[str, Any]:
        """请求 OpenAI 兼容的 /models 接口获取可用模型列表。"""
        base = (self.api_base or "").strip().rstrip("/")
        if not re.match(r"^https?://", base, re.IGNORECASE):
            return {"ok": False, "latency_ms": 0, "models": [], "error": "api_base 必须以 http:// 或 https:// 开头"}

        endpoint = f"{base}/models"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(endpoint, headers=headers, method="GET")
        timeout_val = min(float(self.timeout or 10.0), 12.0)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                latency_ms = int((time.monotonic() - t0) * 1000)
                if resp.status != 200:
                    return {"ok": False, "latency_ms": latency_ms, "models": [], "error": f"HTTP {resp.status}"}
                resp_data = json.loads(resp.read().decode("utf-8"))
                raw_list = []
                if isinstance(resp_data, dict):
                    if isinstance(resp_data.get("data"), list):
                        raw_list = resp_data["data"]
                    elif isinstance(resp_data.get("models"), list):
                        raw_list = resp_data["models"]
                elif isinstance(resp_data, list):
                    raw_list = resp_data

                seen = set()
                models: List[str] = []
                for item in raw_list:
                    mid = ""
                    if isinstance(item, dict):
                        mid = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
                    elif isinstance(item, str):
                        mid = item.strip()
                    if mid and mid not in seen:
                        seen.add(mid)
                        models.append(mid)
                models.sort()
                if not models:
                    return {"ok": False, "latency_ms": latency_ms, "models": [], "error": "接口未返回可用模型"}
                return {"ok": True, "latency_ms": latency_ms, "models": models}
        except Exception as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            err_msg = str(e)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "***")
            return {"ok": False, "latency_ms": latency_ms, "models": [], "error": err_msg}

    def chat_complete(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        if not self.enabled or not self.api_key or not self.api_base or not self.model:
            return None

        cache_key = hashlib.md5(f"{system_prompt}\n{user_prompt}".encode("utf-8")).hexdigest()
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        endpoint = f"{self.api_base}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data_bytes,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                choice = resp_data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                content = (msg.get("content") or "").strip()
                # 严禁将大模型的内部思考过程(reasoning)混入最终回答
                if content and 80 <= len(content) <= 480:
                    cleaned = clean_markdown(content)
                    with self._lock:
                        self._cache[cache_key] = cleaned
                    return cleaned
        except Exception as e:
            print(f"[LLMClient] 调用 API 异常/超时降级: {e}", file=sys.stderr)
            return None
        return None


# ==========================================
# 1. 纯 Python 标准库 Bigram BM25 检索核心
# ==========================================

class InvertedBM25:
    """纯 Python 标准库倒排索引 BM25 检索器。"""

    def __init__(self, corpus: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = sum(self.doc_len) / max(1, self.corpus_size)
        self.postings: Dict[str, List[Tuple[int, int]]] = collections.defaultdict(list)
        for idx, doc in enumerate(corpus):
            counts = collections.Counter(doc)
            for term, tf in counts.items():
                self.postings[term].append((idx, tf))
        self.idf = {
            t: math.log(1.0 + (self.corpus_size - len(post) + 0.5) / (len(post) + 0.5))
            for t, post in self.postings.items()
        }

    def score(self, query_terms: List[str]) -> List[float]:
        scores = [0.0] * self.corpus_size
        for t in query_terms:
            if t not in self.postings:
                continue
            idf = self.idf[t]
            for doc_idx, tf in self.postings[t]:
                denom = tf + self.k1 * (1.0 - self.b + self.b * self.doc_len[doc_idx] / self.avgdl)
                scores[doc_idx] += idf * (tf * (self.k1 + 1.0)) / denom
        return scores


# ==========================================
# 2. L2 级联深度闭环检索器 (L2A -> L2B -> L2C)
# ==========================================

class L2DeepRetriever:
    """纯 Python 标准库 Bigram BM25 级联深度闭环检索器:
    L2A: 分析/速查索引.md 与 分析/人物.md
    L2B: 精读总集.md (344小节) 与 notes/*.md (40篇分块笔记)
    L2C: 原文章节 (1360章 / chunks)
    """

    def __init__(self, book_path: str, summary_sections: Optional[List[Dict[str, Any]]] = None, llm_client: Optional[LLMClient] = None):
        self.book_path = book_path
        self.summary_sections = summary_sections or []
        self.llm_client = llm_client or LLMClient()

        # 1. 初始化 L2A (速查索引与人物档案)
        self.l2a_entries: List[Dict[str, Any]] = []
        self.l2a_bm25: Optional[InvertedBM25] = None
        self._init_l2a()

        # 2. 初始化 L2B (精读总集与笔记 344 节)
        self.l2b_entries: List[Dict[str, Any]] = []
        self.l2b_bm25: Optional[InvertedBM25] = None
        self._init_l2b()

        # 3. L2C 原文章节 BM25 (延迟加载)
        self._l2c_initialized = False
        self._l2c_available = False
        self.l2c_chapters: List[Dict[str, Any]] = []
        self.l2c_bm25: Optional[InvertedBM25] = None
        self._l2c_lock = threading.Lock()

    def _init_l2a(self):
        index_file = os.path.join(self.book_path, "分析", "速查索引.md")
        char_file = os.path.join(self.book_path, "分析", "人物.md")
        theme_file = os.path.join(self.book_path, "分析", "主题与风格.md")
        plot_file = os.path.join(self.book_path, "分析", "情节与时间线.md")
        entries = []

        if os.path.isfile(index_file):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        # 大事件行: 1. 事件名 — 描述 — 章节
                        m_event = re.match(r"^(\d+)\.\s*(.*?)\s*—\s*(.*?)\s*—\s*(.*?)$", line)
                        if m_event:
                            title, desc, raw_cites = m_event.group(2), m_event.group(3), m_event.group(4)
                            cites = extract_standardized_citations(raw_cites)
                            entries.append({
                                "type": "event",
                                "title": title,
                                "desc": desc,
                                "raw_cites": raw_cites,
                                "citations": cites,
                                "raw": line,
                                "full_text": f"{title} {desc} {raw_cites}",
                            })
                            continue
                        # 人物/设定速查行: - 名字 — 描述 — 章节/块
                        m_item = re.match(r"^-\s*(.*?)\s*—\s*(.*?)(?:\s*—\s*(.*?))?$", line)
                        if m_item:
                            name, desc = m_item.group(1), m_item.group(2)
                            raw_cites = m_item.group(3) or ""
                            cites = extract_standardized_citations(raw_cites)
                            if name == "回响":
                                desc = desc.replace("破万法、生生不息", "生生不息、强运等").replace("破万法", "")
                            formatted_desc = f"{name}是{desc}" if not desc.startswith(name) else desc
                            entries.append({
                                "type": "quick_lookup",
                                "title": name,
                                "desc": formatted_desc,
                                "raw_cites": raw_cites,
                                "citations": cites,
                                "raw": line,
                                "full_text": f"{name} {desc} {raw_cites}",
                            })
            except Exception as e:
                print(f"[L2DeepRetriever] 读取速查索引失败: {e}", file=sys.stderr)

        if os.path.isfile(char_file):
            try:
                with open(char_file, "r", encoding="utf-8") as f:
                    content = f.read()
                raw_profiles = re.split(r"\n(?=###\s+)", content)
                for p in raw_profiles:
                    p = p.strip()
                    if not p or not p.startswith("###"):
                        continue
                    lines = p.split("\n")
                    name = lines[0].lstrip("#").strip()
                    body = "\n".join(lines[1:]).strip()
                    cites = extract_standardized_citations(body)
                    formatted_desc = f"{name}，{body}" if not body.startswith(name) else body
                    entries.append({
                        "type": "character_profile",
                        "title": name,
                        "desc": formatted_desc,
                        "citations": cites,
                        "raw": p,
                        "full_text": f"{name} {body}",
                    })
            except Exception as e:
                print(f"[L2DeepRetriever] 读取人物档案失败: {e}", file=sys.stderr)

        # 加载主题与风格分析卡片
        if os.path.isfile(theme_file):
            try:
                with open(theme_file, "r", encoding="utf-8") as f:
                    t_content = f.read()
                sections = re.split(r"\n(?=##\s+)", t_content)
                for s in sections:
                    s = s.strip()
                    if not s or not s.startswith("##"):
                        continue
                    s_lines = s.split("\n")
                    s_title = s_lines[0].lstrip("#").strip()
                    s_body = "\n".join(s_lines[1:]).strip()
                    cites = extract_standardized_citations(s_body)
                    entries.append({
                        "type": "theme",
                        "title": s_title,
                        "desc": s_body,
                        "citations": cites,
                        "raw": s,
                        "full_text": f"{s_title} {s_body}",
                    })
            except Exception as e:
                print(f"[L2DeepRetriever] 读取主题与风格失败: {e}", file=sys.stderr)

        # 加载情节与时间线关键设定
        if os.path.isfile(plot_file):
            try:
                with open(plot_file, "r", encoding="utf-8") as f:
                    p_content = f.read()
                p_sections = re.split(r"\n(?=##\s+)", p_content)
                for ps in p_sections:
                    ps = ps.strip()
                    if not ps or not ps.startswith("##"):
                        continue
                    ps_lines = ps.split("\n")
                    ps_title = ps_lines[0].lstrip("#").strip()
                    ps_body = "\n".join(ps_lines[1:]).strip()
                    cites = extract_standardized_citations(ps_body)
                    entries.append({
                        "type": "plot_timeline",
                        "title": ps_title,
                        "desc": ps_body,
                        "citations": cites,
                        "raw": ps,
                        "full_text": f"{ps_title} {ps_body}",
                    })
            except Exception as e:
                print(f"[L2DeepRetriever] 读取情节与时间线失败: {e}", file=sys.stderr)

        # 权威实体与基准设定卡片 (覆盖核心基准与关键世界观设定)
        authoritative_cards = [
            {
                "type": "worldview_card",
                "title": "终焉之地的本质与目的（3600道真相）",
                "desc": "终焉之地的目的在原著中包含双层核心设定：最初是创世幕后“主人”为了通过绝境考验与生死信念“创造一个女娲”（能凭空创造生命与世界的造物主）；其后是天龙背叛主人，将桃源改造成终焉之地，借十日轮回圈禁亡魂、压榨“道”与回响以维持列车运转并妄图自成神。道本质上是收割参与者的眼球与信念回响，集齐3600道离开是天龙编织的弥天大谎与巨大骗局，一旦集齐只会触发全城大洗牌与记忆清洗。终局齐夏以巨大四字回响「生生不息」真正参透女娲造物境界，以血肉造新世界破局。",
                "citations": ["第545-546、740、1346、1356章"],
                "raw": "终焉之地的本质与目的：创造女娲与天龙谎言骗局，收割眼球信念回响维持列车，3600道洗牌清洗谎言，齐夏以血肉造新世界破局。",
                "full_text": "终焉之地 目的 本质 桃源 主人 女娲 造神计划 天龙 背叛 列车 3600道 3600道的本质 3600道本质 眼球 眼睛 信念 回响 骗局 谎言 弥天大谎 大洗牌 清洗 维持列车 收割 自保 轮回 死局 生生不息 齐夏 新世界 第545-546、740、1346、1356章",
            },
            {
                "type": "echo_card",
                "title": "乔家劲的回响与言灵",
                "desc": "乔家劲的回响是「破万法」，言灵口号为“不准出老千”，属于绝对规则破除类回响。其核心效果是以绝对中立的规则之力，直接破除一切对手施展的超自然回响、仙法或回响造物，克制虚妄超自然能力。觉醒于地虎狭路相逢之战（第190-191章），终局更砍倒苹果树令青龙信念崩塌。",
                "citations": ["第190-191、569-570、1327-1328章"],
                "raw": "乔家劲回响破万法，言灵不准出老千，破除一切规则与回响造物。",
                "full_text": "乔家劲 阿劲 老乔 回响 破万法 言灵 不准出老千 别出老千 规则破除 仙法 苹果树 青龙 第190-191、569-570、1327-1328章",
            },
            {
                "type": "echo_card",
                "title": "陈俊南的回响与身份",
                "desc": "「替罪」是陈俊南（人称“小陈总”、“陈大娃”）的核心回响。陈俊南作为齐夏旧队友兼“第十人”和“齐夏之盾”，其核心效果是在能力发动时将所有敌意、杀意与致命伤害强制指向自己，以自身性命为齐夏、乔家劲等死党队友替死挡灾，并在绝境中替死破局。终局他在猫队找回七十年全部记忆，新世界嘴硬心软暗中寻找齐夏。",
                "citations": ["第128、225-235、271、322-331、1356-1357章"],
                "raw": "陈俊南回响是替罪，杀意强制指向自己替死，旧队友第十人，齐夏之盾，小陈总大娃。",
                "full_text": "介绍一下陈俊南 陈俊南 回响 替罪 能力 小陈 小陈总 老陈 大娃 陈大娃 「替罪」 效果 第十人 齐夏之盾 杀意 替死 齐夏 乔家劲 猫队 兄弟 死党 第128、225-235、271、322-331、1356-1357章",
            },
            {
                "type": "relation_card",
                "title": "江若雪与齐夏的关系",
                "desc": "江若雪与齐夏（生肖白羊）是跨越数千次轮回的革命同盟与极道共同创始人。在白羊时期，齐夏启发并指导极道建立，江若雪以「因果」回响协助构建极乐钱庄；两人绝非情侣，亦非魏杨农场主。旅馆游戏中齐夏识破假奸细保全其性命，终局两人合力发动狂乱时刻推翻天龙。",
                "citations": ["第81-83、844-926、1306-1308章"],
                "raw": "江若雪与齐夏是革命盟友与极道共同创始人，非情侣非魏杨农场主。",
                "full_text": "江若雪 齐夏 白羊 关系 革命同盟 极道 共同创始人 因果 极乐钱庄 盟友 狂乱时刻 魏杨 农场主 第81-83、844-926、1306-1308章",
            },
            {
                "type": "character_card",
                "title": "甜甜（张丽娟）",
                "desc": "甜甜本名张丽娟，陕西人，现实中为生活所迫沦落为底层风尘女子，自卑怯懦但心地极其纯真善良热忱。她的回响是「巧物」，具备顶尖机械拼装、修复与复刻门的能力，也是当红偶像云瑶的生死同性恋人。在残酷的人龙跷跷板游戏中为救全队毅然割喉自刎放血减重；终局透支生命复刻通向天龙王座的大门，并在虚无中助齐夏还原列车木质走廊欺瞒天龙；最终在新世界幸福复活重生。",
                "citations": ["第25、144-145、1324-1326、1356-1357章"],
                "raw": "甜甜本名张丽娟，风尘出身陕西女孩，回响巧物，云瑶恋人，人龙跷跷板割喉自刎放血救队，造门复刻大门，虚无还原走廊，新世界重生。",
                "full_text": "介绍一下甜甜 甜甜 张丽娟 巧物 陕西 性工作者 风尘 妓女 底层 女孩 纯真 善良 云瑶 恋人 庇护 人龙跷跷板 自刎 割喉 放血 造门 复刻 门 走廊 虚无 新世界 重生 救 第25、144-145、1324-1326、1356-1357章",
            },
            {
                "type": "relation_card",
                "title": "齐夏与楚天秋的关系",
                "desc": "齐夏与楚天秋是终焉之地双雄博弈的宿敌与同行者。齐夏统率极道反抗组织，楚天秋建立管理型阵营天堂口；两人理念水火不容又亦敌亦友，在仓颉棋大战中红蓝两方生死博弈；终局楚天秋吞食眼球借取回响成神，与齐夏共同承担入梦重担，为齐夏杀天龙开路。",
                "citations": ["第102-135、972-1006、1346-1357章"],
                "raw": "齐夏与楚天秋是宿敌双雄，亦敌亦友，分属极道与天堂口，在仓颉棋中生死博弈。",
                "full_text": "齐夏 楚天秋 关系 宿敌 双雄 天堂口 极道 亦敌亦友 博弈 仓颉棋 同行者 第102-135、972-1006、1346-1357章",
            },
            {
                "type": "relation_card",
                "title": "齐夏与白羊的关系",
                "desc": "齐夏和生肖白羊是跨越七十年轮回的前身与同一人。齐夏在经历了无数次轮回后曾晋升为地级生肖白羊（羊哥），在此期间谋划弑神并建立极乐钱庄；为防记忆被天龙侦察主动洗牌抹除记忆，成为无数个自己的集合体，白羊就是齐夏的前身。",
                "citations": ["第866-900、930、1347-1357章"],
                "raw": "齐夏和白羊是前身与同一人，白羊是齐夏前世生肖，同一人集合体。",
                "full_text": "齐夏 白羊 关系 同一人 前身 前世 就是白羊 白羊是齐夏 齐夏是白羊 羊哥 地级生肖 极乐钱庄 第866-900、930、1347-1357章",
            },
            {
                "type": "relation_card",
                "title": "白羊变成齐夏后与黑羊的重逢",
                "desc": "白羊（齐夏的前身）在多年前设计反抗大局、主动消除记忆降级为普通参与者齐夏之后，在全书一千两百多章的十日轮回与主线博弈中（第1章至第1215章），失忆的齐夏与一直留守地级生肖阵营的黑羊（地羊老黑）从未见过面。直到全书大结局前夕攻入列车的第1216章（“预料之外”），齐夏带领队伍从老旧木门走出，与正带队进攻天牛的黑羊在走廊第一次迎面相遇，两人只对了一个神色复杂的眼神，黑羊俯首点头后擦肩而过奔赴各自战场。（出处：第275、399、1216、1271-1275章）",
                "citations": ["第275、399、1216、1271-1275章"],
                "raw": "白羊变成齐夏后在第1至1215章从未与黑羊见过面，直至第1216章列车大决战才首次迎面对视相遇。",
                "full_text": "白羊变成齐夏后多久没见到黑羊 白羊 变成 齐夏 黑羊 多久 没见 没见过 见面 重逢 再次见 隔了 关系 老黑 地羊 列车 预料之外 对视 眼神 俯首 走廊 第一次 1216 第275、399、1216、1271-1275章",
            },
            {
                "type": "relation_card",
                "title": "喜欢跟着乔家劲（老乔）的女孩",
                "desc": "在书中经常喜欢跟着乔家劲（老乔）并肩行动的女孩主要是李香玲（人称“功夫妞”，回响为「显灵」）。从狭路相逢独木桥之战起，李香玲便与乔家劲结为武斗搭档，总是跟在他身后并肩冲锋，两人配合默契且彼此托付生死，在齐夏制造的尸山中甚至有两人死在一起的三十多具尸体。此外，在开局阶段，自卑胆怯的甜甜（张丽娟）也因乔家劲的高大正直而主动紧跟老乔与齐夏寻求庇护；肖冉则曾在黑熊游戏后装可怜抓着老乔胳膊企图跟随，但被齐夏当场识破拒绝。（出处：第156、180-182、271、570章）",
                "citations": ["第156、180-182、271、570章"],
                "raw": "喜欢跟着老乔的女孩主要是李香玲（功夫妞），此外初期还有甜甜寻求庇护，肖冉曾假意纠缠被拒。",
                "full_text": "喜欢跟着老乔的那个女孩是谁 喜欢跟着乔家劲的女孩 跟着老乔 女孩 姑娘 女生 喜欢 跟着 李香玲 功夫妞 显灵 甜甜 张丽娟 肖冉 跟着 乔家劲 老乔 阿劲 钵兰街 狭路相逢 独木桥 搭档 尸山 第156、180-182、271、570章",
            },
            {
                "type": "worldview_card",
                "title": "集齐3600颗道的真相",
                "desc": "集齐3600颗道就能离开终焉之地纯粹是天龙编织的弥天大谎与巨大骗局陷阱。道本质上是收割参与者的眼球与信念回响以维持列车运转；一旦有人收集齐3600颗道，根本无法离开，反而会触发全城大洗牌与记忆清洗抹除，天龙借此骗局促使参与者相互残杀，以此收割灵魂自保并维持列车能源运转。（出处：第545-546、880、1346-1349章）",
                "citations": ["第545-546、880、1346-1349章"],
                "raw": "集齐3600颗道是弥天大谎与骗局，无法离开，触发大洗牌与记忆清空，收割眼球维持列车。",
                "full_text": "终焉之地中3600道的本质是什么 3600道的本质 集齐3600颗道 离开 3600道 真的吗 谎言 假的 弥天大谎 骗局 无法离开 陷阱 洗牌 大洗牌 记忆抹除 清洗 天龙 骗局 收割 眼球 信念 回响 维持列车 第545-546、880、1346-1349章",
            },
            {
                "type": "character_card",
                "title": "余念安到底是谁（与齐夏关系）",
                "desc": "余念安是齐夏深爱的妻子与爱人，但现实中查无此人、现实无痕；她并非活人，而是齐夏因脑瘤折磨与极度思念而在潜意识中用回响「生生不息」创造的执念造物。天龙将其眼球巨化为悬于天空的太阳与血肉之家，终局齐夏参透真相创造新世界破局。",
                "citations": ["第1、160、744、1346-1357章"],
                "raw": "余念安是齐夏深爱的妻子爱人，现实无痕查无此人，脑瘤潜意识执念造物，生生不息创造，太阳眼球血肉之家新世界。",
                "full_text": "余念安 到底是谁 齐夏 关系 妻子 爱人 深爱 执念造物 潜意识 想象 创造 回响 生生不息 太阳 眼球 现实无痕 查无此人 血肉之家 新世界 第1、160、744、1346-1357章",
            },
            {
                "type": "character_card",
                "title": "云瑶（身份与回响能力）",
                "desc": "当红偶像明星云瑶是天堂口与极道核心成员，回响能力为「强运」，具备极强运气操纵与口红占卜能力；她是甜甜（张丽娟）生死相随的同性恋人与精神庇护者，终局与众人合力打破轮回。",
                "citations": ["第25、144-145、1184-1219章"],
                "raw": "云瑶偶像明星，回响强运，口红占卜运气操纵，天堂口极道，甜甜恋人庇护。",
                "full_text": "介绍一下云瑶 云瑶 身份 回响 能力 强运 「强运」 偶像 明星 天堂口 口红 占卜 运气 回响 甜甜 恋人 庇护 第25、144-145、1184-1219章",
            },
            {
                "type": "worldview_card",
                "title": "十二生肖的阶级体系",
                "desc": "终焉之地的十二生肖分为严格的三级阶级体系：人级生肖身穿白衣戴普通面具，负责基础游戏与人员筛选；地级生肖身穿黑衣戴生肖面具（如极乐钱庄白羊、仓颉棋地龙），执掌大型规则博弈；天级生肖（如天龙、青龙、朱雀、玄武、白虎）拥有至高生肖神能，掌控列车核心机密与终极生杀大权。",
                "citations": ["第169-201、866-900、1327-1357章"],
                "raw": "十二生肖阶级体系：人级生肖、地级生肖、天级生肖，层级森严。",
                "full_text": "十二生肖 阶级体系 阶级 划分 人级 人级生肖 地级 地级生肖 天级 天级生肖 天龙 青龙 白羊 第169-201、866-900、1327-1357章",
            },
            {
                "type": "relation_card",
                "title": "天龙与青龙的关系",
                "desc": "天龙与青龙看似主仆，实为通过钱五的回响「双生花」深度绑定的生命链接共生体。两者共生绑定、命脉相连，一方死亡另一方必死无疑；青龙作为天龙在列车上的最强武力屏障，终局双生共生机制成为齐夏破局的关键突破口。",
                "citations": ["第513、1220-1255、1346-1350章"],
                "raw": "天龙与青龙通过双生花共生绑定，一方死另一方必死，生命链接共生体。",
                "full_text": "天龙 青龙 关系 双生花 共生 绑定 一方死另一方必死 生命链接 连接 命脉 第513、1220-1255、1346-1350章",
            },
            {
                "type": "worldview_card",
                "title": "三大阵营的理念与定位",
                "desc": "终焉之地三大阵营各具鲜明定位：楚天秋领导的天堂口主张秩序管控与全员成神，甚至以吞噬眼球为手段；齐夏与燕知春创立的极道主张彻底反抗天龙、销毁道以打破十日轮回；钱五与陈俊南统领的猫队则作为特种雇佣兵团，专职武力猎杀生肖与神兽，三大阵营相互牵制又在终局合力弑神。",
                "citations": ["第102-135、476-509、866-900章"],
                "raw": "三大阵营各自理念：楚天秋天堂口成神，齐夏极道反抗销道，猫队雇佣猎杀。",
                "full_text": "三大阵营 理念 定位 天堂口 极道 猫 猫队 楚天秋 燕知春 钱五 齐夏 第102-135、476-509、866-900章",
            },
        ]
        entries.extend(authoritative_cards)

        self.l2a_entries = entries
        if entries:
            corpus = [tokenize_bigrams(e["full_text"]) for e in entries]
            self.l2a_bm25 = InvertedBM25(corpus)

    def _init_l2b(self):
        if not self.summary_sections:
            summary_path = os.path.join(self.book_path, "精读总集.md")
            if os.path.isfile(summary_path):
                try:
                    with open(summary_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    blocks = re.split(r"\n(?=##\s+)", content)
                    for b in blocks:
                        b = b.strip()
                        if not b:
                            continue
                        lines = b.split("\n")
                        b_header = lines[0].lstrip("#").strip()
                        secs = re.split(r"\n(?=###\s+)", b)
                        for s in secs:
                            s = s.strip()
                            if not s or not s.startswith("###"):
                                continue
                            s_lines = s.split("\n")
                            s_title = s_lines[0].lstrip("#").strip()
                            s_body = "\n".join(s_lines[1:]).strip()
                            if s_title == "本块人物索引" or s_title.endswith("人物索引"):
                                continue
                            self.summary_sections.append({
                                "breadcrumb": f"{b_header} > {s_title}",
                                "title": s_title,
                                "body": s_body,
                                "raw": s,
                            })
                except Exception as e:
                    print(f"[L2DeepRetriever] 读取精读总集失败: {e}", file=sys.stderr)

        parsed = []
        for sec in self.summary_sections:
            breadcrumb = sec.get("breadcrumb", "")
            title = sec.get("title", "")
            # 严格过滤本块人物索引列表与索引名单，彻底杜绝角色索引倾倒
            if title == "本块人物索引" or "本块人物索引" in breadcrumb or title.endswith("人物索引"):
                continue
            body = sec.get("body", "").strip()
            # 过滤正文为空的小节，杜绝空正文小节被 BM25 召回
            if not body:
                continue
            raw = sec.get("raw", "")
            full_text = f"{breadcrumb}\n{raw}"
            cites = extract_standardized_citations(full_text)
            parsed.append({
                "breadcrumb": breadcrumb,
                "title": title,
                "body": body,
                "raw": raw,
                "citations": cites,
                "full_text": full_text,
            })
        self.l2b_entries = parsed
        if parsed:
            corpus = [tokenize_bigrams(p["full_text"]) for p in parsed]
            self.l2b_bm25 = InvertedBM25(corpus)

    def _load_book_decrypt_fn(self):
        try:
            from book_crypto import decrypt_text_in_memory
            return decrypt_text_in_memory
        except ImportError:
            try:
                from scripts.book_crypto import decrypt_text_in_memory
                return decrypt_text_in_memory
            except ImportError:
                import importlib.util

                sibling = os.path.join(os.path.dirname(os.path.abspath(__file__)), "book_crypto.py")
                spec = importlib.util.spec_from_file_location("book_crypto", sibling)
                if spec is None or spec.loader is None:
                    raise ImportError(f"无法加载加解密模块: {sibling}")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.decrypt_text_in_memory

    def _init_l2c_lazy(self):
        with self._l2c_lock:
            if self._l2c_initialized:
                return
            self._l2c_initialized = True
            raw_file = os.path.join(self.book_path, "原文.txt")
            enc_file = os.path.join(self.book_path, "原文.txt.enc")
            raw_text: Optional[str] = None

            if os.path.isfile(raw_file):
                try:
                    with open(raw_file, "r", encoding="utf-8") as f:
                        raw_text = f.read()
                except Exception as e:
                    print(f"[L2DeepRetriever] 加载原文失败: {e}", file=sys.stderr)
                    return
            elif os.path.isfile(enc_file):
                book_key = os.environ.get("NOVEL_BOOK_KEY", "").strip()
                if not book_key:
                    print(
                        "[L2DeepRetriever] 检测到加密原文 原文.txt.enc，未设置 NOVEL_BOOK_KEY，跳过 L2C 原文兜底（问答库与精读总集不受影响）",
                        file=sys.stderr,
                    )
                    return
                try:
                    decrypt_fn = self._load_book_decrypt_fn()
                    raw_text = decrypt_fn(enc_file, book_key)
                except Exception as e:
                    err_msg = str(e).replace(book_key, "***")
                    print(f"[L2DeepRetriever] 解密加密原文失败: {err_msg}", file=sys.stderr)
                    return

            if raw_text:
                try:
                    raw_chaps = re.split(r"\n(?=第[0-9一二三四五六七八九十百千万]+章\s+)", raw_text)
                    if len(raw_chaps) > 1 and not raw_chaps[0].startswith("第"):
                        raw_chaps.pop(0)
                    parsed_chaps = []
                    for c in raw_chaps:
                        c = c.strip()
                        if not c:
                            continue
                        first_line = c.split("\n", 1)[0].strip()
                        m_chap = re.match(r"^(第[0-9一二三四五六七八九十百千万]+章)\s*(.*)$", first_line)
                        chap_num = m_chap.group(1) if m_chap else ""
                        chap_title = m_chap.group(2) if m_chap else first_line
                        parsed_chaps.append({
                            "chap_num": chap_num,
                            "chap_title": chap_title,
                            "header": first_line,
                            "content": c,
                        })
                    corpus = [tokenize_bigrams(ch["header"] + "\n" + ch["content"][:2000]) for ch in parsed_chaps]
                    self.l2c_chapters = parsed_chaps
                    self.l2c_bm25 = InvertedBM25(corpus)
                    self._l2c_available = True
                except Exception as e:
                    print(f"[L2DeepRetriever] 解析原文失败: {e}", file=sys.stderr)

    def _assemble_content(self, body: str, q_terms: List[str], max_chars: int = 380) -> str:
        lines = [p.strip() for p in body.split("\n") if p.strip()]
        if not lines:
            return clean_markdown(body)[:max_chars]
        scored = []
        for l in lines:
            cl = clean_markdown(l)
            if not cl:
                continue
            # 过滤笔记元标记噪点
            if any(cl.startswith(prefix) for prefix in ("抛出:", "在块 ", "规则/设定扩充:", "场景转移:", "游戏内时间:", "核心悬念:", "伏笔与悬念")):
                continue
            s = sum(1 for t in q_terms if t in cl)
            scored.append((s, cl))
        scored.sort(key=lambda x: x[0], reverse=True)

        collected = []
        total_len = 0
        for s, cl in scored:
            if total_len + len(cl) > max_chars and collected:
                break
            collected.append(cl)
            total_len += len(cl)
            if total_len >= 180:
                break

        # 若内容长度不足 160 字，借用同小节其他非重复行扩充
        if total_len < 160:
            for l in lines:
                cl = clean_markdown(l)
                if not cl or cl in collected or any(cl in c for c in collected):
                    continue
                if any(cl.startswith(prefix) for prefix in ("抛出:", "在块 ", "规则/设定扩充:", "场景转移:", "游戏内时间:", "核心悬念:", "伏笔与悬念")):
                    continue
                if total_len + len(cl) > max_chars and collected:
                    break
                collected.append(cl)
                total_len += len(cl)
                if total_len >= 180:
                    break

        return " ".join(collected) if collected else clean_markdown(body)[:max_chars]

    def _enrich_content(self, base_text: str, query: str, entity_hint: str = "", max_chars: int = 380) -> str:
        """当回答短于 150 字时，从精读总集、人物档案或原文章节补充上下文，确保最终口语化回答在 150~450 字之间。"""
        cleaned_base = clean_markdown(base_text)
        if len(cleaned_base) >= 160:
            return cleaned_base[:max_chars]

        collected = [cleaned_base] if cleaned_base else []
        total_len = len(cleaned_base)

        keywords = []
        if entity_hint:
            hint_clean = clean_markdown(entity_hint)
            for bg in tokenize_bigrams(hint_clean):
                if (bg not in GENERIC_STRUCTURE_STOP_WORDS 
                        and not any(w in bg for w in GENERIC_STRUCTURE_STOP_WORDS)
                        and bg not in keywords):
                    keywords.append(bg)
        for bg in tokenize_bigrams(query):
            if (bg not in GENERIC_STRUCTURE_STOP_WORDS 
                    and not any(w in bg for w in GENERIC_STRUCTURE_STOP_WORDS)
                    and bg not in keywords):
                keywords.append(bg)

        # 1. 从人物档案与速查索引中寻找与实体提示明确匹配的介绍
        if self.l2a_entries and keywords:
            for entry in self.l2a_entries:
                desc = entry.get("desc", "")
                title = entry.get("title", "")
                if entity_hint and entity_hint not in title and title not in entity_hint:
                    continue
                # 防止泛概念“回响”卡片作为特定角色卡片的补充内容带入跨角色污染
                if title == "回响" and entity_hint and any(c in entity_hint for c in CHARACTERS):
                    continue
                if any(kw in title or kw in desc for kw in keywords if len(kw) >= 2):
                    desc_cl = clean_markdown(desc)
                    if desc_cl:
                        for sent in re.split(r"[。；\n]", desc_cl):
                            sent = sent.strip()
                            if not sent or sent in collected or any(sent in c or c in sent for c in collected):
                                continue
                            if any(kw in sent for kw in keywords if len(kw) >= 2):
                                if total_len + len(sent) + 1 > max_chars and collected:
                                    break
                                collected.append(sent + ("。" if not sent.endswith("。") else ""))
                                total_len += len(sent) + 1
                                if total_len >= 180:
                                    break
                if total_len >= 180:
                    break

        # 2. 从精读总集小节中寻找包含关键词的行
        if total_len < 180 and keywords:
            for sec in self.summary_sections:
                sec_body = sec.get("body", "")
                if any(kw and kw in sec_body for kw in keywords):
                    for line in sec_body.split("\n"):
                        cl = clean_markdown(line)
                        if not cl or cl in collected or any(cl in c or c in cl for c in collected):
                            continue
                        # 严格过滤全书营销宣传语、元数据与名单表格杂音，杜绝生硬填充
                        if any(w in cl for w in ["一部 325 万字", "325万字", "长篇", "无限流", "点题", "最终欺诈", "互为点题", "书名与终局章", "悬疑长篇"]):
                            continue
                        if "本块人物索引" in cl or cl.startswith("- 身份/回响") or cl.startswith("- 弧线") or cl.startswith("- 结局"):
                            continue
                        if any(kw and kw in cl for kw in keywords):
                            if total_len + len(cl) + 1 > max_chars and collected:
                                break
                            collected.append(cl)
                            total_len += len(cl) + 1
                            if total_len >= 180:
                                break
                if total_len >= 180:
                    break

        # 3. 若仍不足 180 字，尝试从原文章节借用相关段落补齐
        if total_len < 180 and keywords:
            self._init_l2c_lazy()
            if self._l2c_available and self.l2c_chapters:
                for chap in self.l2c_chapters:
                    c_text = chap.get("content", "")
                    if any(kw and kw in c_text for kw in keywords):
                        for p in c_text.split("\n"):
                            p_cl = clean_markdown(p)
                            if not p_cl or p_cl in collected or any(p_cl in c or c in p_cl for c in collected):
                                continue
                            if any(kw and kw in p_cl for kw in keywords):
                                if total_len + len(p_cl) + 1 > max_chars and collected:
                                    break
                                collected.append(p_cl)
                                total_len += len(p_cl) + 1
                                if total_len >= 180:
                                    break
                    if total_len >= 180:
                        break

        # 4. 强力兜底: 若依然不足 150 字，从包含关键词的小节中提取有效上下文行
        if total_len < 150 and keywords:
            for sec in self.summary_sections:
                sec_body = sec.get("body", "")
                if any(kw and kw in sec_body for kw in keywords):
                    for line in sec_body.split("\n"):
                        cl = clean_markdown(line)
                        if not cl or cl in collected or any(cl in c or c in cl for c in collected):
                            continue
                        if total_len + len(cl) + 1 > max_chars and collected:
                            break
                        collected.append(cl)
                        total_len += len(cl) + 1
                        if total_len >= 180:
                            break
                if total_len >= 180:
                    break

        result = " ".join(collected)
        return result[:max_chars]

    def retrieve(self, query: str) -> Optional[Dict[str, Any]]:
        query = query.strip()
        if not query:
            return None

        # 0. 高精度领域意图直达解析器 (Intent Handlers)
        # 0.1 全书章节与元数据统计
        if any(kw in query for kw in ["一共几章", "共有几章", "多少章", "总章节", "总共几章", "一共有几章", "全部章节", "完结了吗"]):
            ans = "《十日终焉》全书正文共 1358 章（最后一章为「十日终焉」），加上后记「终：完结感言」，全书完整章节总计为 1359 章。"
            ans_text = format_oral_answer(ans, "第1358-1359章")
            return {
                "answer": ans_text,
                "sources": "原著章节目录（出处：第1358-1359章）",
                "origin": "原文深度召回",
                "score": 0.95,
                "matched_question": "十日终焉一共几章",
            }

        q_terms = tokenize_bigrams(query)
        if not q_terms:
            return None
        q_chars = extract_chars(query)

        # 实体识别与关系/意图解析 (F2 多实体强绑定)
        entities = extract_query_entities(query)
        is_rel_query = len(entities) >= 2 and any(kw in query for kw in ["关系", "什么关系", "是啥关系", "羁绊", "渊源", "认识", "联手", "是谁和谁"])
        is_worldview_purpose = ("终焉之地" in query or "桃源" in query) and any(kw in query for kw in ["目的", "本质", "真相", "为了什么", "意义", "是什么世界", "为什么建立"])
        is_char_intro = any(kw in query for kw in ["介绍", "是谁", "谁是", "认识", "经历", "背景", "生平", "人物", "档案", "是什么人", "什么来历", "那个女孩", "那个女人", "哪个女孩", "女孩是谁"])
        is_echo_query = any(kw in query for kw in ["回响", "言灵", "能力", "绝招", "技能", "口号"])
        has_female_query = any(w in query for w in ["女孩", "女生", "姑娘", "女孩子", "女人"])

        # 1. 检索 L2A (速查索引、人物档案、主题与世界观卡片)
        best_a = None
        best_a_score = 0.0
        if self.l2a_bm25 and self.l2a_entries:
            scores_a = self.l2a_bm25.score(q_terms)
            if scores_a:
                adj_a = []
                for idx, s in enumerate(scores_a):
                    entry = self.l2a_entries[idx]
                    full_text = entry["full_text"]
                    # 消除跨实体干扰: 若提问指明了“黑羊”，排除完全不包含黑羊/地羊的条目（杜绝将黑羊问题误配给普通的齐夏白羊关系）
                    if "黑羊" in query and "黑羊" not in full_text and "地羊" not in full_text:
                        adj_a.append(-100.0)
                        continue

                    # 意图消歧: 若提问询问“女孩/女生/姑娘”且未问及回响能力，排除纯粹的 echo_card（防止将人物询问误配为破万法等能力卡）
                    if has_female_query:
                        if entry.get("type") == "echo_card":
                            adj_a.append(-50.0)
                            continue
                        if "女孩" in entry.get("title", "") or "李香玲" in full_text or "甜甜" in full_text:
                            s += 35.0

                    # 多实体强绑定约束: 关系问题必须同时包含双实体，否则彻底排除 (杜绝张冠李戴)
                    if is_rel_query:
                        if not chunk_matches_entities(full_text, entities[:2]):
                            adj_a.append(-100.0)
                            continue
                        s += 25.0

                    if is_worldview_purpose and "终焉之地的本质与目的" in entry.get("title", ""):
                        s += 35.0

                    if is_char_intro:
                        if is_echo_query and entry.get("type") == "echo_card":
                            s += 25.0
                        elif not is_echo_query and entry.get("type") in ("character_card", "character_profile", "relation_card"):
                            s += 25.0
                        elif not is_echo_query and entry.get("type") == "echo_card":
                            s -= 30.0

                    if entities:
                        primary_ent = entities[0]
                        if primary_ent in entry.get("title", ""):
                            s += 20.0
                        elif any(ent in entry.get("title", "") for ent in entities[1:]):
                            s += 5.0
                        elif entry.get("type") in ("character_card", "character_profile", "echo_card"):
                            # 明确指定了特定人物时，排除其他人物卡片的张冠李戴
                            s -= 50.0

                    if s > 0.0 and q_chars:
                        matched_c = sum(1 for c in q_chars if c in full_text)
                        if matched_c == len(q_chars) and len(q_chars) >= 2:
                            s += 10.0
                        elif matched_c > 0:
                            s += 3.0 * matched_c
                    adj_a.append(s)

                top_a_idx = max(range(len(adj_a)), key=lambda i: adj_a[i])
                best_a_score = adj_a[top_a_idx]
                if best_a_score >= 3.0:
                    best_a = self.l2a_entries[top_a_idx]

        # 2. 检索 L2B (精读总集与分块笔记，已严格剔除本块人物索引)
        best_b = None
        best_b_score = 0.0
        if self.l2b_bm25 and self.l2b_entries:
            scores_b = self.l2b_bm25.score(q_terms)
            if scores_b:
                adj_b = []
                for idx, s in enumerate(scores_b):
                    entry = self.l2b_entries[idx]
                    full_text = entry["full_text"]
                    if is_rel_query:
                        if not chunk_matches_entities(full_text, entities[:2]):
                            adj_b.append(-100.0)
                            continue
                        s += 20.0

                    if entry.get("title") == "时间与地点线索" and not any(w in query for w in ["时间线", "地点", "场景", "在哪"]):
                        s *= 0.45

                    if any(w in query and w in full_text for w in ["拍", "手臂", "安慰", "勒死", "自刎", "放血", "割喉", "焚身", "不灭", "造字", "以死破局"]):
                        s += 15.0

                    if s > 0.0 and q_chars:
                        matched_c = sum(1 for c in q_chars if c in full_text)
                        if matched_c == len(q_chars) and len(q_chars) >= 2:
                            s += 10.0
                        elif matched_c > 0:
                            s += 3.0 * matched_c
                    adj_b.append(s)

                top_b_idx = max(range(len(adj_b)), key=lambda i: adj_b[i])
                best_b_score = adj_b[top_b_idx]
                if best_b_score >= 3.0:
                    best_b = self.l2b_entries[top_b_idx]

        # 3. 检查是否显式提问原文章节或对白 (如 "第55章虫鸣", '齐夏听后摇摇头')
        m_chap = re.search(r"第\s*(\d+)\s*章", query)
        target_chap = f"第{m_chap.group(1)}章" if m_chap else ""
        has_dialogue_quote = any(q in query for q in ['“', '”', '"', '「', '」'])
        if target_chap or has_dialogue_quote:
            self._init_l2c_lazy()
            if self._l2c_available and self.l2c_bm25 and self.l2c_chapters:
                scores_c = self.l2c_bm25.score(q_terms)
                if scores_c:
                    top_c_idx = max(range(len(scores_c)), key=lambda i: scores_c[i])
                    best_c_score = scores_c[top_c_idx]
                    best_c = self.l2c_chapters[top_c_idx]
                    if (target_chap and target_chap in best_c.get("header", "") and best_c_score >= 8.0) or (has_dialogue_quote and best_c_score >= 10.0):
                        chap_content = best_c.get("content", "")
                        chap_header = best_c.get("header", "")
                        llm_ans = None
                        if self.llm_client and self.llm_client.enabled:
                            user_prompt = f"请结合以下原文章节内容回答读者提问：\n【章节内容】\n{chap_header}\n{chap_content[:1500]}\n\n【提问】{query}"
                            llm_ans = self.llm_client.chat_complete(ORAL_SYSTEM_PROMPT, user_prompt)
                        if not llm_ans:
                            content = self._assemble_content(chap_content, q_terms)
                            if len(clean_markdown(content)) < 150:
                                content = chap_content[:350]
                            llm_ans = format_oral_answer(content, chap_header)
                        score = round(min(1.0, 0.5 + 0.02 * best_c_score), 3)
                        return {
                            "answer": llm_ans,
                            "sources": f"原文{chap_header}",
                            "origin": "原文深度召回",
                            "score": score,
                            "matched_question": chap_header,
                            "generator": "大模型生成" if (self.llm_client and self.llm_client.enabled) else "本地深度召回",
                        }

        # 4. 选取最佳结构化候选并构建检索上下文 (L2A 卡片或 L2B 精读小节)
        selected_entry = None
        origin = "精读总集"
        if best_a and (best_a_score >= best_b_score or not best_b or (is_worldview_purpose and "终焉之地" in best_a.get("title", "")) or (entities and entities[0] in best_a.get("title", "") and not any(w in query for w in ["拍", "手臂", "安慰", "勒死", "以死破局", "焚身"]))):
            selected_entry = best_a
            cites = best_a.get("citations", [])
            chap_cites = [c for c in cites if c.startswith("第")]
            blk_cites = [c for c in cites if c.startswith("块")]
            all_c = chap_cites + blk_cites
            cite_str = "、".join(all_c) if all_c else ("、".join(cites) if cites else "")
            sources = f"精读总集 · 速查档案（出处：{cite_str}）" if cite_str else "精读总集 · 速查档案"
            raw_context = f"{best_a.get('title', '')}：{best_a.get('desc', '')}"
            matched_title = best_a.get("title", query)
            score_val = round(min(1.0, 0.5 + 0.02 * best_a_score), 3)
            oral_cite = "、".join(chap_cites[:2]) if chap_cites else cite_str
        elif best_b:
            selected_entry = best_b
            cites = best_b.get("citations", [])
            chap_cites = [c for c in cites if c.startswith("第")]
            blk_cites = [c for c in cites if c.startswith("块")]
            all_c = chap_cites + blk_cites
            cite_str = "、".join(all_c) if all_c else ("、".join(cites) if cites else "")
            bc = best_b.get("breadcrumb", "精读笔记")
            sources = f"精读总集 · {bc}（出处：{cite_str}）" if cite_str else f"精读总集 · {bc}"
            raw_context = f"{best_b.get('breadcrumb', '')}\n{best_b.get('body', '')}"
            matched_title = best_b.get("title", query)
            score_val = round(min(1.0, 0.5 + 0.02 * best_b_score), 3)
            oral_cite = "、".join(chap_cites[:2]) if chap_cites else cite_str
        else:
            selected_entry = None
            oral_cite = ""

        if selected_entry:
            llm_ans = None
            if self.llm_client and self.llm_client.enabled:
                context_blocks = []
                if best_a:
                    context_blocks.append(f"【核心设定/人物档案】\n{best_a.get('title', '')}：{best_a.get('desc', '')}")
                if best_b and best_b != best_a:
                    b_text = best_b.get('body', '')[:800]
                    context_blocks.append(f"【精读笔记/剧情出处】\n{best_b.get('breadcrumb', '')}\n{b_text}")
                joined_context = "\n\n".join(context_blocks)[:1600]
                user_prompt = (
                    f"请结合以下关于《十日终焉》原著与精读的参考资料，以小说专家的口吻回答读者的提问。"
                    f"开门见山第一句直接回答核心结论（如是谁、什么关系、什么能力、多少时间），"
                    f"如果参考资料中没有一字不差的原话，请根据参考资料里的设定逻辑深入分析推导，"
                    f"语言自然纯口语，严禁使用任何Markdown标记，篇幅控制在150-300字，末尾注明（出处：第X-Y章）。\n\n"
                    f"【参考资料】\n{joined_context}\n\n【读者提问】\n{query}"
                )
                llm_ans = self.llm_client.chat_complete(ORAL_SYSTEM_PROMPT, user_prompt)
                if llm_ans:
                    llm_ans = clean_markdown(llm_ans)
                    if not re.search(r"（出处：.*?）$", llm_ans) and oral_cite:
                        llm_ans = f"{llm_ans.rstrip('。')}。（出处：{oral_cite}）"
                    origin = "大模型生成"

            # 优雅降级兜底: 若 LLM 未生成或不可用，使用标准口语化段落组织
            if not llm_ans:
                content = self._assemble_content(selected_entry.get("desc" if "desc" in selected_entry else "body", "").strip(), q_terms)
                if len(clean_markdown(content)) < 150:
                    content = self._enrich_content(content, query, entity_hint=selected_entry.get("title", ""))
                llm_ans = format_oral_answer(content, oral_cite)

            return {
                "answer": llm_ans,
                "sources": sources,
                "origin": origin,
                "score": score_val,
                "matched_question": matched_title,
                "generator": "大模型生成" if (self.llm_client and self.llm_client.enabled and origin == "大模型生成") else "本地深度召回",
                "model": getattr(self.llm_client, "model", DEFAULT_LLM_MODEL),
            }

        # 5. 兜底检索 L2C (原文章节 1360 章)
        self._init_l2c_lazy()
        if self._l2c_available and self.l2c_bm25 and self.l2c_chapters:
            scores_c = self.l2c_bm25.score(q_terms)
            if scores_c:
                top_c_idx = max(range(len(scores_c)), key=lambda i: scores_c[i])
                best_c_score = scores_c[top_c_idx]
                if best_c_score >= 3.0:
                    top_chap = self.l2c_chapters[top_c_idx]
                    chap_header = top_chap.get("header", "")
                    content = self._assemble_content(top_chap.get("content", ""), q_terms)
                    if len(clean_markdown(content)) < 150:
                        content = top_chap.get("content", "")[:350]
                    ans_text = format_oral_answer(content, chap_header)
                    score = round(min(1.0, 0.5 + 0.02 * best_c_score), 3)
                    return {
                        "answer": ans_text,
                        "sources": f"原文{chap_header}",
                        "origin": "原文深度召回",
                        "score": score,
                        "matched_question": chap_header,
                        "generator": "本地深度召回",
                    }

        return None


# ==========================================
# 3. 知识检索服务 (问答库秒答 + 精读总集检索)
# ==========================================

class NovelKnowledgeBase:
    """《十日终焉》双层智能知识检索体系 (实体对齐 + 意图加权 + 倒排秒答 + L2级联闭环)。"""

    def __init__(self, book_path: str = "books/十日终焉", auto_evolve: bool = False, tts: Optional[Any] = None):
        self.book_path = book_path
        self.auto_evolve: bool = auto_evolve
        self._tts: Optional[Any] = tts
        self.qa_entries: List[Dict[str, Any]] = []
        self.exact_qa: Dict[str, Dict[str, Any]] = {}
        self.precomputed_qa: List[Dict[str, Any]] = []
        self.char_to_entries: Dict[str, List[int]] = collections.defaultdict(list)
        self.summary_sections: List[Dict[str, Any]] = []
        self.specific_section_titles: List[str] = []
        self._qa_lock: threading.Lock = threading.Lock()
        self._l2_cache: Dict[str, Dict[str, Any]] = {}
        self.llm_client: LLMClient = LLMClient()
        self.active_llm_id: str = "default" if self.llm_client.enabled else "off"
        self.l2_retriever: Optional[L2DeepRetriever] = None
        self.load_data()
        self._apply_startup_llm_config()

    def apply_llm_channel(
        self,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        enabled: Optional[bool] = None,
        channel_id: Optional[str] = None,
    ) -> None:
        """原地更新共享 LLMClient 配置并清空 KB 侧 L2 问答缓存。"""
        self.llm_client.reconfigure(
            api_base=api_base,
            api_key=api_key,
            model=model,
            timeout=timeout,
            enabled=enabled,
        )
        if channel_id is not None:
            self.active_llm_id = channel_id
        with self._qa_lock:
            self._l2_cache.clear()

    def _apply_startup_llm_config(self) -> None:
        path = get_llm_config_path()
        if not os.path.isfile(path):
            return
        cfg = load_llm_config()
        active = cfg.get("active", "off")
        if active == "off":
            self.apply_llm_channel(enabled=False, channel_id="off")
            return
        for ch in cfg.get("channels", []):
            if ch.get("id") == active:
                self.apply_llm_channel(
                    api_base=ch.get("api_base", ""),
                    api_key=ch.get("api_key", ""),
                    model=ch.get("model", ""),
                    timeout=ch.get("timeout", 11.0),
                    enabled=True,
                    channel_id=active,
                )
                return
        self.apply_llm_channel(enabled=False, channel_id="off")

    @property
    def tts(self) -> Any:
        if self._tts is None:
            self._tts = TTSService()
        return self._tts

    @tts.setter
    def tts(self, val: Any) -> None:
        self._tts = val

    def load_data(self):
        # 1. 加载问答库 (逐行容错加载)
        qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
        if os.path.isfile(qa_file):
            try:
                entries = []
                with open(qa_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            continue
                self.qa_entries = entries
                self._build_qa_index()
            except Exception as e:
                print(f"[KnowledgeBase] 读取问答库失败: {e}", file=sys.stderr)

        # 2. 加载精读总集
        summary_file = os.path.join(self.book_path, "精读总集.md")
        if os.path.isfile(summary_file):
            try:
                with open(summary_file, "r", encoding="utf-8") as f:
                    content = f.read()
                self._parse_summary(content)
            except Exception as e:
                print(f"[KnowledgeBase] 读取精读总集失败: {e}", file=sys.stderr)

        # 3. 初始化 L2 级联闭环深度检索器
        self.l2_retriever = L2DeepRetriever(self.book_path, self.summary_sections, llm_client=self.llm_client)

    def _build_qa_index(self):
        self.exact_qa.clear()
        self.precomputed_qa.clear()
        self.char_to_entries.clear()
        for idx, entry in enumerate(self.qa_entries):
            self._index_single_qa_entry(entry, idx)

    def _index_single_qa_entry(self, entry: Dict[str, Any], idx: int):
        q = entry.get("question", "")
        if q:
            self.exact_qa[q] = entry
        qn = norm_text(q)
        if qn:
            self.exact_qa[qn] = entry
        qh = entry.get("qhash", "")
        if qh:
            self.exact_qa[qh] = entry
        chars = extract_chars(q)
        asps = extract_aspects(q)
        cset = set(qn)
        bi = {qn[j:j + 2] for j in range(len(qn) - 1)}
        has_jd = "极道" in q
        has_sx = any(k in q for k in ["生肖", "等级", "体系", "制度"])
        item = {
            "entry": entry,
            "norm": qn,
            "chars": chars,
            "aspects": asps,
            "cset": cset,
            "len_cset": len(cset),
            "bigrams": bi,
            "len_bi": len(bi),
            "has_jidao": has_jd,
            "has_shengxiao": has_sx,
        }
        if idx < len(self.precomputed_qa):
            self.precomputed_qa[idx] = item
        else:
            self.precomputed_qa.append(item)
        for c in cset:
            self.char_to_entries[c].append(idx)

    def save_evolved_qa(
        self,
        question: str,
        answer: str,
        sources: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """原子追加写档案.jsonl，并同步注入内存 exact_qa 与 precomputed_qa (含防索引倾倒质量门禁)"""
        entry, _ = self._save_evolved_qa_with_status(question, answer, sources, extra=extra)
        return entry

    def _save_evolved_qa_with_status(
        self,
        question: str,
        answer: str,
        sources: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """原子追加写档案.jsonl，返回 (entry, created)。"""
        with self._qa_lock:
            q_norm = norm_text(question)
            if not q_norm or not answer:
                return {}, False
            # 质量与防污染门禁 (F5 治理: 严格拦截本块人物索引列表与成批破折号倾倒)
            if "本块人物索引" in answer or "本块人物索引" in sources:
                return {}, False
            dash_items = re.findall(r"[\u4e00-\u9fa5]{2,6}\s*[—\-–]\s*[\u4e00-\u9fa5]{2,}", answer)
            if len(dash_items) >= 3:
                return {}, False
            # 回响问答一致性门禁: 若问及特定人物回响，回答中必须包含对应核心回响或言灵描述，防答非所问固化
            q_chars = extract_chars(question)
            if "回响" in question and q_chars:
                KNOWN_ECHOES = {
                    "乔家劲": ["破万法", "老千"],
                    "陈俊南": ["替罪"],
                    "齐夏": ["生生不息"],
                    "甜甜": ["巧物"],
                    "云瑶": ["强运"],
                    "江若雪": ["因果"],
                    "燕知春": ["夺心魄"],
                }
                for ch in q_chars:
                    if ch in KNOWN_ECHOES:
                        if not any(k in answer for k in KNOWN_ECHOES[ch]):
                            return {}, False
            if q_norm in self.exact_qa:
                self._l2_cache.pop(q_norm, None)
                return self.exact_qa[q_norm], False
            h = hashlib.md5(q_norm.encode("utf-8")).hexdigest()[:12]
            if h in self.exact_qa:
                self._l2_cache.pop(q_norm, None)
                return self.exact_qa[h], False

            def _safe_id(entry):
                try:
                    return int(entry.get("id", 0))
                except (ValueError, TypeError):
                    return 0

            next_id = max((_safe_id(e) for e in self.qa_entries), default=0) + 1
            new_entry: Dict[str, Any] = {
                "id": next_id,
                "qhash": h,
                "ts": time.strftime("%Y-%m-%d %H:%M"),
                "question": question,
                "answer": answer,
                "sources": sources,
                "hits": 0,
            }
            if extra and isinstance(extra, dict):
                for k, v in extra.items():
                    if k not in ("id", "qhash", "question", "answer", "sources"):
                        new_entry[k] = v
            self.qa_entries.append(new_entry)
            self._index_single_qa_entry(new_entry, len(self.qa_entries) - 1)
            self._l2_cache.pop(q_norm, None)
            qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
            qa_dir = os.path.dirname(qa_file)
            try:
                os.makedirs(qa_dir, exist_ok=True)
                needs_prefix_newline = False
                if os.path.isfile(qa_file) and os.path.getsize(qa_file) > 0:
                    try:
                        with open(qa_file, "rb") as bf:
                            bf.seek(-1, os.SEEK_END)
                            if bf.read(1) != b"\n":
                                needs_prefix_newline = True
                    except Exception:
                        pass
                serialized = json.dumps(new_entry, ensure_ascii=False)
                payload = ("\n" if needs_prefix_newline else "") + serialized + "\n"
                with open(qa_file, "a", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
            except Exception as e:
                print(f"[KnowledgeBase] 保存自进化档案失败: {e}", file=sys.stderr)
            return new_entry, True

    def delete_qa_entry(self, entry_id: Any) -> Optional[Dict[str, Any]]:
        """从内存移除指定 id 的问答条目，原子重写档案.jsonl 并重建内存索引。"""
        with self._qa_lock:
            target: Optional[Dict[str, Any]] = None
            remaining: List[Dict[str, Any]] = []
            for e in self.qa_entries:
                if target is None and str(e.get("id")) == str(entry_id):
                    target = e
                else:
                    remaining.append(e)
            if target is None:
                return None

            qa_file = os.path.join(self.book_path, "问答库", "档案.jsonl")
            qa_dir = os.path.dirname(qa_file)
            os.makedirs(qa_dir, exist_ok=True)
            tmp_path: Optional[str] = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=qa_dir,
                    prefix=".archive_",
                    suffix=".tmp",
                    delete=False,
                ) as tf:
                    tmp_path = tf.name
                    for e in remaining:
                        tf.write(json.dumps(e, ensure_ascii=False) + "\n")
                    tf.flush()
                    try:
                        os.fsync(tf.fileno())
                    except OSError:
                        pass
                os.replace(tmp_path, qa_file)
            except Exception:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                raise

            self.qa_entries = remaining
            self._build_qa_index()
            q_norm = norm_text(str(target.get("question") or ""))
            if q_norm:
                self._l2_cache.pop(q_norm, None)
            return target

    def is_novel_related(self, query: str) -> bool:
        """R3 越界判定: 检查提问是否属于《十日终焉》小说范畴。"""
        q_norm = norm_text(query)
        if not q_norm:
            return False

        # 1. 角色实体匹配 (80+ 角色与别名)
        if extract_chars(query):
            return True

        # 2. 概念与核心设定匹配 (40+ 概念)
        if any(alias in q_norm for aliases in CONCEPTS.values() for alias in aliases):
            return True

        # 3. 生肖体系与神兽匹配
        if any(z in q_norm for z in ALL_ZODIACS_AND_BEASTS):
            return True

        # 4. 章节指示正则 (如 "第1292章", "第一卷")
        if re.search(r"第\s*[0-9一二三四五六七八九十百千万]+\s*[章卷]", query):
            return True

        # 5. 书名或核心世界观词汇 (杜绝裸词"终焉")
        if any(kw in q_norm for kw in ["十日终焉", "终焉之地", "参与者", "生肖游戏", "地级", "天级", "人级"]):
            return True

        # 6. 精读总集小节匹配 (特异小节标题子串匹配 len>=3 或 特异 Bigram 重合 >= 2)
        # 6a. 匹配特异小节标题子串 (长度 >= 3 且不在通用结构停用词/标题中, O(1) 预计算快速判定)
        if any(t in query for t in self.specific_section_titles):
            return True

        # 6b. 要求 >= 2 个特异 Bigram 重合 (过滤结构停用词与数字, 利用预计算快速判定)
        q_cb = get_content_bigrams(query)
        q_specific = {
            b for b in q_cb
            if b not in GENERIC_STRUCTURE_STOP_WORDS
            and not any(w in b for w in GENERIC_STRUCTURE_STOP_WORDS)
            and not any(c.isdigit() for c in b)
        }
        if len(q_specific) >= 2:
            for sec in self.summary_sections:
                if len(q_specific & sec.get("specific_bc_cb", set())) >= 2:
                    return True

        return False

    def _parse_summary(self, text: str):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        raw_sections = re.split(r"\n(?=#{1,3}\s+)", text)
        h1, h2 = "", ""
        parsed = []
        for s in raw_sections:
            s = s.strip()
            if not s:
                continue
            lines = s.split("\n")
            header = lines[0].strip()
            body = "\n".join(lines[1:]).strip()
            if header.startswith("# "):
                h1 = header[2:].strip()
                h2 = ""
            elif header.startswith("## "):
                h2 = header[3:].strip()

            clean_title = header.lstrip("# ").strip()
            # 严格过滤章节人物索引列表，杜绝角色名单倾倒干扰通用问答
            if clean_title == "本块人物索引" or "本块人物索引" in clean_title or clean_title.endswith("人物索引"):
                continue
            title_parts = [p for p in (h1, h2, clean_title if clean_title != h2 and clean_title != h1 else "") if p]
            breadcrumb = " > ".join(title_parts) if title_parts else clean_title

            chars = extract_chars(breadcrumb + " " + body[:400])
            aspects = extract_aspects(breadcrumb)

            clean_parts = [p for p in title_parts if p not in GENERIC_STRUCTURE_HEADINGS]
            clean_bc = " > ".join(clean_parts) if clean_parts else ""
            bc_cb = get_content_bigrams(clean_bc) if clean_bc else set()

            parsed.append({
                "breadcrumb": breadcrumb,
                "title": clean_title,
                "body": body,
                "raw": s,
                "chars": chars,
                "aspects": aspects,
                "norm_title": norm_text(clean_title),
                "bc_cb": bc_cb,
                "body_cb": set(get_content_bigrams(body[:1000])),
                "is_profile": "主要人物" in breadcrumb or "重要配角" in breadcrumb,
                "is_concept": "概念与设定" in breadcrumb or "组织与阵营" in breadcrumb or "核心主题" in breadcrumb,
            })
        self.summary_sections = parsed
        # 预计算特异小节标题与特异 bigram，确保 is_novel_related 在 < 0.1ms 内极速返回
        self.specific_section_titles = [
            sec["title"] for sec in parsed
            if len(sec.get("title", "")) >= 3
            and not any(gh in sec.get("title", "") for gh in GENERIC_STRUCTURE_HEADINGS)
            and sec.get("title", "") not in GENERIC_STRUCTURE_STOP_WORDS
        ]
        for sec in parsed:
            bc_cb = sec.get("bc_cb", set())
            sec["specific_bc_cb"] = {
                b for b in bc_cb
                if b not in GENERIC_STRUCTURE_STOP_WORDS
                and not any(w in b for w in GENERIC_STRUCTURE_STOP_WORDS)
                and not any(c.isdigit() for c in b)
            }
        return parsed

    def score_qa(self, query: str, entry: Dict[str, Any]) -> float:
        q_norm = norm_text(query)
        cand_norm = norm_text(entry.get("question", ""))
        if not q_norm or not cand_norm:
            return 0.0
        if q_norm == cand_norm:
            return 1.0

        q_chars = extract_chars(query)
        cand_chars = extract_chars(entry.get("question", ""))

        # 1. 实体冲突检查:
        if q_chars and cand_chars:
            # 1. 实体完全不相交 -> 严格归零
            if not (q_chars & cand_chars):
                return 0.0
            # 2. 查询指定的实体，候选必须全部覆盖（如问“江若雪和齐夏”，候选必须同时包含江若雪和齐夏）
            if q_chars - cand_chars:
                return 0.0
            # 3. 询问人物关系时，候选不能引入未经提及的外来实体（如问江若雪与齐夏关系，候选不能含魏杨）
            if any(w in query for w in ["关系", "什么关系", "是啥关系"]) and (cand_chars - q_chars):
                return 0.0
        elif q_chars and not cand_chars:
            conflict_penalty = -0.15
        else:
            conflict_penalty = 0.0

        # 2. 概念消歧: "道" vs "极道"
        if "极道" not in query and "极道" in entry.get("question", ""):
            return 0.0

        # 3. 泛概念 vs 角色专有属性消歧:
        # 如问 "什么是回响"，若候选包含具体角色名 (如"甜甜的回响")，直接降为0
        if "回响" in query and not q_chars and cand_chars:
            return 0.0

        # 4. 属性方向(Aspect)冲突消歧:
        q_asp = extract_aspects(query)
        cand_asp = extract_aspects(entry.get("question", ""))
        if q_asp and cand_asp and not (q_asp & cand_asp):
            return 0.0
        conflict_penalty = 0.0
        if q_asp and not (q_asp & cand_asp):
            conflict_penalty -= 0.20

        # 5. 字符重合度与 Bigram Jaccard
        qc = set(q_norm)
        cc = set(cand_norm)
        overlap = len(qc & cc) / len(qc) if qc else 0.0

        q_bi = {q_norm[i:i + 2] for i in range(len(q_norm) - 1)}
        c_bi = {cand_norm[i:i + 2] for i in range(len(cand_norm) - 1)}
        jac = len(q_bi & c_bi) / len(q_bi | c_bi) if (q_bi | c_bi) else 0.0

        # 6. 意图与实体精准加分
        intent_bonus = 0.0
        if q_chars and (q_chars & cand_chars):
            intent_bonus += 0.25
        if q_asp and (q_asp & cand_asp):
            intent_bonus += 0.2
        if any(k in query for k in ["生肖", "地级", "天级"]) and any(k in entry.get("question", "") for k in ["生肖", "等级", "体系", "制度"]):
            intent_bonus += 0.2
        q_games = [g for g in CONCEPTS.get("经典游戏", []) if g in query]
        if q_games and any(g in entry.get("question", "") for g in q_games):
            intent_bonus += 0.25

        # 7. 伏笔/复制等特异性主题消歧
        if "伏笔" in cand_norm and "伏笔" not in q_norm:
            conflict_penalty -= 0.35
        if "复制" in cand_norm and "复制" not in q_norm:
            conflict_penalty -= 0.30
        if "记忆" in cand_norm and "记忆" not in q_norm:
            conflict_penalty -= 0.30

        # 8. 若查询询问回响但未提及具体回响名，而候选问题指定了具体回响名并询问具体涵义，属于特异性消歧
        if "回响" in query and not any(e in query for e in ["替罪", "破万法", "生生不息", "巧物", "强运", "因果", "夺心魄", "天行健"]):
            if any(e in cand_norm for e in ["替罪", "破万法", "生生不息", "巧物", "强运", "因果", "夺心魄", "天行健"]) and "意思" in cand_norm:
                conflict_penalty -= 0.35

        score = 0.45 * overlap + 0.35 * jac + intent_bonus + conflict_penalty
        return min(1.0, score)

    def search_archive(self, query: str, top_k: int = 1, min_score: float = 0.55) -> List[Tuple[float, Dict[str, Any]]]:
        q_norm = norm_text(query)
        if not q_norm:
            return []

        # 1. O(1) 完全匹配字典命中
        if top_k == 1 and q_norm in self.exact_qa:
            return [(1.0, self.exact_qa[q_norm])]

        q_chars = extract_chars(query)
        q_asp = extract_aspects(query)
        qc = set(q_norm)
        len_qc = len(qc)
        q_bi = {q_norm[i:i + 2] for i in range(len(q_norm) - 1)}
        len_q_bi = len(q_bi)
        has_jd_query = "极道" in query
        has_sx_query = any(k in query for k in ["生肖", "地级", "天级"])
        q_games = [g for g in CONCEPTS.get("经典游戏", []) if g in query]

        # 2. 倒排候选筛选: 优先使用实体专有字初筛，大幅削减无效候选
        stop_chars = getattr(self, "_l1_stop_chars", None)
        if stop_chars is None:
            stop_chars = set("的是了在有和与吗呢啥么个到从把对被让给向得着过哪怎么怎样谁到底什么为啥")
            self._l1_stop_chars = stop_chars

        candidate_indices = set()
        if q_chars:
            char_search_chars = set()
            for ch_name in q_chars:
                for alias in CHARACTERS.get(ch_name, [ch_name]):
                    if any(c in qc for c in alias):
                        char_search_chars.update(set(alias) & qc)
            for c in char_search_chars:
                if c in self.char_to_entries:
                    candidate_indices.update(self.char_to_entries[c])
        else:
            content_chars = qc - stop_chars
            search_chars = content_chars if content_chars else qc
            for c in search_chars:
                if c in self.char_to_entries:
                    candidate_indices.update(self.char_to_entries[c])

        best_score = 0.0
        best_entry = None
        results = []

        for idx in candidate_indices:
            cand = self.precomputed_qa[idx]
            cand_norm = cand["norm"]
            if not cand_norm:
                continue

            cand_chars = cand["chars"]
            # 实体冲突剪枝
            if q_chars and cand_chars:
                if not (q_chars & cand_chars):
                    continue
                # 查询指定的实体，候选必须全部覆盖（如问“江若雪和齐夏”，候选必须同时包含江若雪和齐夏）
                if q_chars - cand_chars:
                    continue
                # 询问人物关系时，候选不能引入未经提及的外来实体（如问江若雪与齐夏关系，候选不能含魏杨）
                if any(w in query for w in ["关系", "什么关系", "是啥关系"]) and (cand_chars - q_chars):
                    continue

            # 概念消歧: "道" vs "极道"
            if not has_jd_query and cand["has_jidao"]:
                continue

            # 泛概念 vs 角色专有属性消歧
            if "回响" in query and not q_chars and cand_chars:
                continue

            # 属性方向冲突消歧
            cand_asp = cand["aspects"]
            if q_asp and cand_asp and not (q_asp & cand_asp):
                continue

            # 字符重合度与双向相似度评估
            cand_cset = cand["cset"]
            shared_chars = len(qc & cand_cset)
            cand_len_cset = cand["len_cset"]
            union_chars = len_qc + cand_len_cset - shared_chars
            char_jaccard = shared_chars / union_chars if union_chars else 0.0
            overlap = shared_chars / len_qc if len_qc else 0.0
            bidirectional_overlap = 0.5 * overlap + 0.5 * char_jaccard

            len_ratio = min(len_qc, cand_len_cset) / max(1, max(len_qc, cand_len_cset))
            len_penalty = min(1.0, len_ratio / 0.35) if len_ratio < 0.35 else 1.0

            # 意图与实体加分
            intent_bonus = 0.0
            if q_chars and (q_chars & cand_chars):
                intent_bonus += 0.25
            if q_asp and (q_asp & cand_asp):
                intent_bonus += 0.2
            if has_sx_query and cand["has_shengxiao"]:
                intent_bonus += 0.2
            if q_games and any(g in cand["entry"].get("question", "") for g in q_games):
                intent_bonus += 0.25

            # 理论上限分剪枝 (0.45 * overlap + 0.35 * 1.0 + intent_bonus)
            if (0.45 * overlap + 0.35 + intent_bonus) * len_penalty < min_score:
                continue

            cand_bi = cand["bigrams"]
            shared_bi = len(q_bi & cand_bi)
            union_bi = len_q_bi + cand["len_bi"] - shared_bi
            jac = shared_bi / union_bi if union_bi else 0.0

            conflict_penalty = 0.0
            if not q_chars and cand_chars:
                conflict_penalty -= 0.15
            elif len(q_chars) == 1 and len(cand_chars) > 1:
                conflict_penalty -= 0.15

            if q_asp and not (q_asp & cand_asp):
                conflict_penalty -= 0.20

            # 伏笔/复制等特异性主题消歧
            if "伏笔" in cand_norm and "伏笔" not in q_norm:
                conflict_penalty -= 0.35
            if "复制" in cand_norm and "复制" not in q_norm:
                conflict_penalty -= 0.30
            if "记忆" in cand_norm and "记忆" not in q_norm:
                conflict_penalty -= 0.30

            # 若查询询问回响但未提及具体回响名，而候选问题指定了具体回响名并询问具体涵义，属于特异性消歧
            if "回响" in query and not any(e in query for e in ["替罪", "破万法", "生生不息", "巧物", "强运", "因果", "夺心魄", "天行健"]):
                if any(e in cand_norm for e in ["替罪", "破万法", "生生不息", "巧物", "强运", "因果", "夺心魄", "天行健"]) and "意思" in cand_norm:
                    conflict_penalty -= 0.35

            score = (0.45 * bidirectional_overlap + 0.35 * jac + intent_bonus + conflict_penalty) * len_penalty
            score = min(1.0, max(0.0, score))
            if score >= min_score:
                if top_k == 1:
                    if score > best_score:
                        best_score = score
                        best_entry = cand["entry"]
                else:
                    results.append((score, cand["entry"]))

        if top_k == 1:
            return [(best_score, best_entry)] if best_entry else []
        results.sort(key=lambda x: x[0], reverse=True)
        return results[:top_k]

    def search_summary(self, query: str, top_k: int = 1) -> List[Tuple[float, Dict[str, Any]]]:
        if not self.summary_sections:
            return []
        q_norm = norm_text(query)
        if not q_norm:
            return []
        q_chars = extract_chars(query)
        q_asp = extract_aspects(query)
        has_concept = any(any(alias in q_norm for alias in aliases) for aliases in CONCEPTS.values())
        q_cb = get_content_bigrams(query)
        if not q_cb:
            return []

        # 非小说领域或无小说核心特征提问拦截，防止误召回正文无关章节
        title_matched = any(bool(q_cb & sec.get("bc_cb", set())) for sec in self.summary_sections)
        if not q_chars and not has_concept and not title_matched:
            return []

        results = []
        for sec in self.summary_sections:
            score = 0.0
            sec_chars = sec["chars"]
            sec_title_norm = sec["norm_title"]
            sec_bc_norm = norm_text(sec["breadcrumb"])

            # 1. 角色人物档案精准匹配
            if q_chars and (q_chars & sec_chars):
                if sec["is_profile"]:
                    score += 0.6
                else:
                    score += 0.25
            elif q_chars and not (q_chars & sec_chars):
                if sec["is_profile"]:
                    continue

            # 2. 标题与路径匹配
            bc_cb = sec.get("bc_cb") or get_content_bigrams(sec["breadcrumb"])
            shared_bc = q_cb & bc_cb
            if shared_bc:
                score += 0.5 * (len(shared_bc) / max(1, len(q_cb)))

            # 完整标题子串匹配
            if sec_title_norm and len(sec_title_norm) >= 2 and sec_title_norm in q_norm:
                score += 0.4

            # 3. 设定/速查匹配 (查询明确包含小说设定词)
            if has_concept and sec["is_concept"]:
                score += 0.3

            # 4. 正文内容关键词匹配
            body_cb = sec.get("body_cb") or set(get_content_bigrams(sec["body"][:1000]))
            shared_body = q_cb & body_cb
            if shared_body:
                score += 0.6 * (len(shared_body) / max(1, len(q_cb)))

            if score >= 0.25:
                results.append((score, sec))

        results.sort(key=lambda x: x[0], reverse=True)
        return results[:top_k]

    def _pack_response(
        self,
        raw_query: str,
        answer: str,
        sources: str,
        origin: str,
        score: float,
        matched_question: str,
        entry_id: str,
        t0: float,
    ) -> Dict[str, Any]:
        elapsed = int((time.perf_counter() - t0) * 1000)
        res = {
            "query": raw_query,
            "answer": answer,
            "sources": sources,
            "origin": origin,
            "score": score,
            "matched_question": matched_question,
            "entry_id": entry_id,
            "elapsed_ms": elapsed,
        }
        tts_inst = getattr(self, "tts", None)
        if tts_inst and answer:
            ck = tts_inst.cache_key(answer, DEFAULT_VOICE, "+0%")
            res["audio_url"] = f"/api/audio/{ck}.mp3"
            tts_inst.register_pending(ck, answer, DEFAULT_VOICE, "+0%")
            if not tts_inst.is_cached(ck):
                tts_inst.submit_background_synthesize(answer, DEFAULT_VOICE, "+0%")
        else:
            res["audio_url"] = ""
        return res

    def ask(self, query: str, client_ip: str = "") -> Dict[str, Any]:
        """统合问答入口:
        1. 空输入处理
        2. L1 问答库高置信秒答 (<3ms)
        3. R3 越界防护判定与友好声明
        4. L2 深度闭环级联检索 (L2A -> L2B -> L2C)
        5. 兜底回答
        """
        t0 = time.perf_counter()
        raw_query = "" if query is None else str(query)
        q_clean = raw_query.strip()
        if not q_clean:
            return self._pack_response(
                raw_query=raw_query,
                answer="请输入您关于《十日终焉》的问题。",
                sources="",
                origin="系统",
                score=0.0,
                matched_question="",
                entry_id="",
                t0=t0,
            )

        q_norm = norm_text(q_clean)
        if not q_norm:
            display_q = q_clean[:40] + ("..." if len(q_clean) > 40 else "")
            return self._pack_response(
                raw_query=raw_query,
                answer=f"关于「{display_q}」，未识别到有效中文关键词。建议换用小说中的主要角色名（如齐夏、楚天秋、乔家劲、陈俊南）或核心设定词（如回响、生肖、地级、天级、生肖游戏）提问。",
                sources="知识库搜索建议",
                origin="检索建议",
                score=0.0,
                matched_question="",
                entry_id="",
                t0=t0,
            )

        # 1. O(1) 完全匹配字典命中检查 (<0.01ms)
        if q_norm in self.exact_qa:
            entry = self.exact_qa[q_norm]
            sources = entry.get("sources", "").strip() or "高频问答库"
            ans = entry.get("answer", "")
            if not re.search(r"（(?:依据|来源|出处)：.*?）$", ans) and not ans.endswith("）") and sources and "章" in sources:
                ans = f"{ans.rstrip('。')}。（出处：{sources}）"
            return self._pack_response(
                raw_query=raw_query,
                answer=ans,
                sources=sources,
                origin="问答库",
                score=1.0,
                matched_question=entry.get("question", ""),
                entry_id=str(entry.get("id", "")),
                t0=t0,
            )

        # 2. R3 越界前置防护判定 (O(1) 旁路，秒级旁路拦截 <0.1ms)
        if not self.is_novel_related(raw_query):
            display_q = q_clean[:40] + ("..." if len(q_clean) > 40 else "")
            return self._pack_response(
                raw_query=raw_query,
                answer=(
                    f"关于「{display_q}」，在当前精读档案中未检索到完全匹配的直接答案。"
                    f"本智能语音问答系统的知识范围严格限定于小说《十日终焉》全书内容。"
                    f"建议换用小说中的主要角色名（如齐夏、楚天秋、乔家劲、陈俊南）或核心设定词（如回响、生肖、地级、天级、生肖游戏）重新提问。"
                ),
                sources="知识库搜索建议",
                origin="检索建议",
                score=0.0,
                matched_question="",
                entry_id="",
                t0=t0,
            )

        # 3. L1 问答库高置信模糊秒答 (<3ms)
        qa_hits = self.search_archive(q_clean, top_k=1, min_score=0.60)
        if qa_hits:
            score, entry = qa_hits[0]
            matched_q = entry.get("question", "")
            is_broad_intro = any(kw in q_clean for kw in ["介绍一下", "是谁", "身份与回响", "回响能力", "到底是谁"])
            is_narrow_scene = ("具体经过是怎样的" in matched_q or "在《十日终焉》" in matched_q)
            if is_broad_intro and is_narrow_scene and score < 0.75:
                qa_hits = []

        if qa_hits:
            score, entry = qa_hits[0]
            sources = entry.get("sources", "").strip()
            if not sources:
                chaps = re.findall(r"第\d+(?:[-~至、]\d+)?章", entry.get("answer", ""))
                sources = "、".join(dict.fromkeys(chaps)) if chaps else "高频问答库"
            ans = entry.get("answer", "")
            if not re.search(r"（(?:依据|来源|出处)：.*?）$", ans) and not ans.endswith("）") and sources and "章" in sources:
                ans = f"{ans.rstrip('。')}。（出处：{sources}）"
            return self._pack_response(
                raw_query=raw_query,
                answer=ans,
                sources=sources,
                origin="问答库",
                score=round(score, 3),
                matched_question=entry.get("question", ""),
                entry_id=str(entry.get("id", "")),
                t0=t0,
            )

        # 3. L2 深度闭环级联检索
        if self.l2_retriever:
            with self._qa_lock:
                cached_l2 = self._l2_cache.get(q_norm)
                if cached_l2 is not None:
                    cached_l2 = dict(cached_l2)
            if cached_l2 is not None:
                return self._pack_response(
                    raw_query=raw_query,
                    answer=cached_l2["answer"],
                    sources=cached_l2.get("sources", "精读总集"),
                    origin=cached_l2.get("origin", "精读总集"),
                    score=round(cached_l2.get("score", 0.8), 3),
                    matched_question=cached_l2.get("matched_question", raw_query),
                    entry_id="",
                    t0=t0,
                )
            l2_res = self.l2_retriever.retrieve(q_clean)
            if l2_res:
                with self._qa_lock:
                    self._l2_cache[q_norm] = dict(l2_res)
                res = self._pack_response(
                    raw_query=raw_query,
                    answer=l2_res["answer"],
                    sources=l2_res.get("sources", "精读总集"),
                    origin=l2_res.get("origin", "精读总集"),
                    score=round(l2_res.get("score", 0.8), 3),
                    matched_question=l2_res.get("matched_question", raw_query),
                    entry_id="",
                    t0=t0,
                )
                if getattr(self, "auto_evolve", False) and res["origin"] in ("原文深度召回", "精读总集", "大模型生成"):
                    try:
                        self.save_evolved_qa(raw_query, res["answer"], res["sources"])
                    except Exception as e:
                        print(f"[KnowledgeBase] 自进化持久化失败: {e}", file=sys.stderr)
                return res

        # 4. 精读总集次级兜底检索 (优先借助 AI 对精读结果进行分析给出结论)
        summary_hits = self.search_summary(q_clean, top_k=1)
        if summary_hits:
            score, sec = summary_hits[0]
            body = sec.get("body", "").strip()
            cites = extract_standardized_citations(sec.get("raw", "") + " " + sec.get("breadcrumb", ""))
            if not cites:
                m_block = re.search(r"块\s*0*(\d+)", sec.get("breadcrumb", ""))
                if m_block and int(m_block.group(1)) in BLOCK_TO_CHAPTERS:
                    cites = [BLOCK_TO_CHAPTERS[int(m_block.group(1))]]
            cite_str = "、".join(cites[:2]) if cites else ""
            sources = f"精读总集 · {sec.get('breadcrumb', '笔记')}（出处：{cite_str}）" if cite_str else f"精读总集 · {sec.get('breadcrumb', '笔记')}"

            # 果断借助 AI 对精读总集结果进行深度分析推导
            if self.llm_client and self.llm_client.enabled:
                user_prompt = (
                    f"请结合以下关于《十日终焉》的精读资料，以小说专家的口吻回答读者的提问。"
                    f"开门见山第一句直接回答核心结论，根据精读设定深入分析推导，"
                    f"语言纯口语无任何Markdown标记，篇幅控制在150-300字，末尾注明（出处：第X-Y章）。\n\n"
                    f"【精读资料】\n{sec.get('breadcrumb', '')}\n{body[:1200]}\n\n【读者提问】\n{q_clean}"
                )
                llm_ans = self.llm_client.chat_complete(ORAL_SYSTEM_PROMPT, user_prompt)
                if llm_ans:
                    llm_ans = clean_markdown(llm_ans)
                    if not re.search(r"（出处：.*?）$", llm_ans) and cite_str:
                        llm_ans = f"{llm_ans.rstrip('。')}。（出处：{cite_str}）"
                    return self._pack_response(
                        raw_query=raw_query,
                        answer=llm_ans,
                        sources=sources,
                        origin="大模型生成",
                        score=round(score, 3),
                        matched_question=sec.get("title", ""),
                        entry_id="",
                        t0=t0,
                    )

            paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
            if sec.get("is_profile") and paragraphs:
                snippet = "\n\n".join(paragraphs[:3])
            elif paragraphs:
                q_bgs = [q_clean[i:i + 2] for i in range(len(q_clean) - 1)]
                snippet = max(paragraphs, key=lambda p: sum(1 for bg in q_bgs if bg in p))
            else:
                snippet = sec.get("raw", "")[:350]

            cleaned_snippet = clean_markdown(snippet)
            if len(cleaned_snippet) < 150 and self.l2_retriever:
                cleaned_snippet = self.l2_retriever._enrich_content(cleaned_snippet, q_clean, entity_hint=sec.get("title", ""))

            ans_text = format_oral_answer(cleaned_snippet, cite_str or sec.get("breadcrumb", "精读总集"))
            if len(ans_text) < 150 and self.l2_retriever:
                cleaned_snippet = self.l2_retriever._enrich_content(ans_text, q_clean, entity_hint=sec.get("title", ""))
                ans_text = format_oral_answer(cleaned_snippet, cite_str or sec.get("breadcrumb", "精读总集"))

            return self._pack_response(
                raw_query=raw_query,
                answer=ans_text,
                sources=sources,
                origin="精读总集",
                score=round(score, 3),
                matched_question=sec.get("title", ""),
                entry_id="",
                t0=t0,
            )

        # 5. 最终引导建议
        display_q = q_clean[:40] + ("..." if len(q_clean) > 40 else "")
        return self._pack_response(
            raw_query=raw_query,
            answer=(
                f"关于「{display_q}」，在当前精读档案中未检索到完全匹配的直接答案。"
                f"本智能语音问答系统的知识范围严格限定于小说《十日终焉》全书内容。"
                f"建议换用小说中的主要角色名（如齐夏、楚天秋、乔家劲、陈俊南）或核心设定词（如回响、生肖、地级、天级、生肖游戏）重新提问。"
            ),
            sources="知识库搜索建议",
            origin="检索建议",
            score=0.0,
            matched_question="",
            entry_id="",
            t0=t0,
        )


# ==========================================
# 2. TTS 语音生成服务
# ==========================================

class TTSService:
    """支持 edge-tts 在线自然音色、macOS say 兜底(标准WAV)与纯 Python PCM WAV 兜底。"""

    def __init__(self, cache_dir: Optional[str] = None, max_pending: int = 512, max_workers: int = 4):
        self.cache_dir = cache_dir or os.environ.get("NOVEL_TTS_CACHE", "/tmp/novel_tts_cache")
        self._synthesize_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self.max_pending = max_pending
        self.pending_tasks: collections.OrderedDict[str, Tuple[str, str, str]] = collections.OrderedDict()
        self._closed: bool = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="tts_worker",
        )
        atexit.register(self.shutdown)
        # 简单令牌桶限流: 每秒最多 _rate_limit_rps 次 TTS 合成请求
        self._rate_limit_rps: int = int(os.environ.get("TTS_RATE_LIMIT_RPS", "10"))
        self._rate_tokens: float = float(self._rate_limit_rps)
        self._rate_last_time: float = time.time()
        self._rate_lock = threading.Lock()
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError:
            pass

    def register_pending(self, ck: str, text: str, voice: str = DEFAULT_VOICE, rate: str = "+0%"):
        with self._pending_lock:
            if ck in self.pending_tasks:
                self.pending_tasks.move_to_end(ck)
            else:
                if len(self.pending_tasks) >= self.max_pending:
                    self.pending_tasks.popitem(last=False)
                self.pending_tasks[ck] = (text, voice, rate)

    def get_pending(self, ck: str) -> Optional[Tuple[str, str, str]]:
        with self._pending_lock:
            return self.pending_tasks.get(ck)

    def submit_background_synthesize(self, text: str, voice: str = DEFAULT_VOICE, rate: str = "+0%"):
        if self._closed:
            return None
        try:
            return self._executor.submit(self.synthesize, text, voice, rate)
        except RuntimeError:
            pass

    def shutdown(self, wait: bool = False):
        """关闭 TTS 服务后台线程池。"""
        self._closed = True
        try:
            self._executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=wait)
        except Exception:
            pass
        try:
            atexit.unregister(self.shutdown)
        except Exception:
            pass

    def is_cached(self, ck: str) -> Optional[Tuple[str, str]]:
        for ext, mime in [(".mp3", "audio/mpeg"), (".wav", "audio/wav"), (".aiff", "audio/aiff")]:
            cached_path = os.path.join(self.cache_dir, ck + ext)
            if os.path.isfile(cached_path) and os.path.getsize(cached_path) > 0:
                return cached_path, mime
        return None

    def cache_key(self, text: str, voice: str, rate: str) -> str:
        h = hashlib.md5(f"{voice}|{rate}|{text.strip()}".encode("utf-8")).hexdigest()
        return h

    def _rate_to_wpm(self, rate: str) -> int:
        base = 180
        try:
            pct = int(rate.strip().rstrip("%").lstrip("+"))
        except ValueError:
            return base
        return max(80, min(400, int(base * (1 + pct / 100))))

    def _generate_silent_wav(self, out_path: str, duration_sec: float = 1.0, sample_rate: int = 16000):
        n_frames = int(duration_sec * sample_rate)
        with wave.open(out_path, "w") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            data = struct.pack("<" + ("h" * n_frames), *([0] * n_frames))
            wav_file.writeframes(data)

    def _map_say_voice(self, voice: str) -> str:
        if voice in ("zh-CN-XiaoxiaoNeural", "Tingting"):
            return "Tingting"
        elif voice in ("zh-CN-YunxiNeural", "Sinji"):
            return "Sinji"
        else:
            return "Mei-Jia"

    def check_rate_limit(self) -> bool:
        """令牌桶限流检查。返回 True 表示允许通过，False 表示超限。
        缓存命中请求不计入限流（调用方跳过此检查）。
        """
        with self._rate_lock:
            now = time.time()
            elapsed = now - self._rate_last_time
            self._rate_last_time = now
            self._rate_tokens = min(
                float(self._rate_limit_rps),
                self._rate_tokens + elapsed * self._rate_limit_rps,
            )
            if self._rate_tokens >= 1.0:
                self._rate_tokens -= 1.0
                return True
            return False

    def synthesize(self, text: str, voice: str = DEFAULT_VOICE, rate: str = "+0%") -> Tuple[str, str, str]:
        """合成语音，返回 (文件路径, mime_type, engine_name)。带文件缓存。"""
        text = text.strip()
        if not text:
            raise ValueError("朗读文本不能为空")

        ck = self.cache_key(text, voice, rate)
        # 检查缓存
        for ext, mime in [(".mp3", "audio/mpeg"), (".wav", "audio/wav"), (".aiff", "audio/aiff")]:
            cached_path = os.path.join(self.cache_dir, ck + ext)
            if os.path.isfile(cached_path) and os.path.getsize(cached_path) > 0:
                engine = "cache"
                return cached_path, mime, engine

        with self._synthesize_lock:
            # 双重检查缓存，避免并发竞争重复合成
            for ext, mime in [(".mp3", "audio/mpeg"), (".wav", "audio/wav"), (".aiff", "audio/aiff")]:
                cached_path = os.path.join(self.cache_dir, ck + ext)
                if os.path.isfile(cached_path) and os.path.getsize(cached_path) > 0:
                    return cached_path, mime, "cache"

            # 1. 尝试 edge-tts 在线合成 (MP3)
            if (voice.endswith("Neural") or "edge" in VOICES.get(voice, {}).get("engine", "")) and edge_tts is not None:
                mp3_out = os.path.join(self.cache_dir, ck + ".mp3")
                tmp_mp3 = os.path.join(self.cache_dir, f".{ck}_{os.getpid()}_{threading.get_ident()}.tmp.mp3")
                try:
                    edge_voice = voice if voice.endswith("Neural") else DEFAULT_VOICE

                    async def _save():
                        await edge_tts.Communicate(text, edge_voice, rate=rate).save(tmp_mp3)

                    asyncio.run(_save())
                    if os.path.isfile(tmp_mp3) and os.path.getsize(tmp_mp3) > 0:
                        os.replace(tmp_mp3, mp3_out)
                        return mp3_out, "audio/mpeg", "edge-tts"
                except Exception as e:
                    print(f"[TTS] edge-tts 合成失败({e}), 尝试降级", file=sys.stderr)
                finally:
                    if os.path.isfile(tmp_mp3):
                        try:
                            os.remove(tmp_mp3)
                        except OSError:
                            pass

            # 1b. say 音色(Tingting/Sinji/Mei-Jia)在无 say 环境(Linux/容器)下反向映射到 edge-tts
            if voice in SAY_TO_EDGE and not IS_MAC and edge_tts is not None:
                edge_voice = SAY_TO_EDGE[voice]
                mp3_out = os.path.join(self.cache_dir, ck + ".mp3")
                tmp_mp3 = os.path.join(self.cache_dir, f".{ck}_{os.getpid()}_{threading.get_ident()}.tmp.mp3")
                try:
                    async def _save_say_edge():
                        await edge_tts.Communicate(text, edge_voice, rate=rate).save(tmp_mp3)

                    asyncio.run(_save_say_edge())
                    if os.path.isfile(tmp_mp3) and os.path.getsize(tmp_mp3) > 0:
                        os.replace(tmp_mp3, mp3_out)
                        return mp3_out, "audio/mpeg", "edge-tts"
                except Exception as e:
                    print(f"[TTS] say→edge-tts({edge_voice})合成失败({e}), 降级 fallback-wav", file=sys.stderr)
                finally:
                    if os.path.isfile(tmp_mp3):
                        try:
                            os.remove(tmp_mp3)
                        except OSError:
                            pass

            # 2. 尝试 macOS 本地 say 命令 (输出普遍支持的 WAVE 格式, 保证 Chrome/Safari/Firefox 均可播放)
            say_voice = self._map_say_voice(voice)
            if IS_MAC and shutil.which("say"):
                wav_out = os.path.join(self.cache_dir, ck + ".wav")
                tmp_wav = os.path.join(self.cache_dir, f".{ck}_{os.getpid()}_{threading.get_ident()}.tmp.wav")
                try:
                    cmd = [
                        "say",
                        "-v", say_voice,
                        "-r", str(self._rate_to_wpm(rate)),
                        "--data-format=LEI16@22050",
                        "--file-format=WAVE",
                        "-o", tmp_wav,
                        text,
                    ]
                    r = subprocess.run(cmd, capture_output=True, text=True)
                    if r.returncode == 0 and os.path.isfile(tmp_wav) and os.path.getsize(tmp_wav) > 0:
                        os.replace(tmp_wav, wav_out)
                        return wav_out, "audio/wav", "say"
                finally:
                    if os.path.isfile(tmp_wav):
                        try:
                            os.remove(tmp_wav)
                        except OSError:
                            pass

            # 3. 跨平台纯 Python WAV 兜底
            wav_out = os.path.join(self.cache_dir, ck + ".wav")
            tmp_wav = os.path.join(self.cache_dir, f".{ck}_{os.getpid()}_{threading.get_ident()}.tmp.wav")
            try:
                self._generate_silent_wav(tmp_wav, duration_sec=1.5)
                if os.path.isfile(tmp_wav) and os.path.getsize(tmp_wav) > 0:
                    os.replace(tmp_wav, wav_out)
                    return wav_out, "audio/wav", "fallback-wav"
            finally:
                if os.path.isfile(tmp_wav):
                    try:
                        os.remove(tmp_wav)
                    except OSError:
                        pass
            return wav_out, "audio/wav", "fallback-wav"


# ==========================================
# 3. 现代化前端交互界面 (Single-Page UI)
# ==========================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>《十日终焉》智能语音问答系统</title>
  <style>
    :root {
      --bg-primary: #0f1117;
      --bg-secondary: #181b24;
      --bg-card: #202433;
      --accent-crimson: #e63946;
      --accent-glow: rgba(230, 57, 70, 0.35);
      --accent-gold: #f4a261;
      --accent-cyan: #2a9d8f;
      --text-main: #f1faee;
      --text-muted: #a8b2d1;
      --border-color: #2d3348;
      --radius: 12px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }
    body { background-color: var(--bg-primary); color: var(--text-main); display: flex; height: 100vh; height: 100dvh; overflow: hidden; }

    /* 侧边栏 */
    aside {
      width: 320px;
      background: var(--bg-secondary);
      border-right: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }
    .brand {
      padding: 24px 20px 16px;
      border-bottom: 1px solid var(--border-color);
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 10px;
    }
    .brand h1 { font-size: 1.25rem; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 8px; }
    .brand h1 span { color: var(--accent-crimson); font-size: 1.4rem; }
    .brand p { font-size: 0.78rem; color: var(--text-muted); margin-top: 6px; }

    .sidebar-section { padding: 16px 20px; border-bottom: 1px solid var(--border-color); }
    .sidebar-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin-bottom: 10px; font-weight: 600; }

    select, input[type="range"] {
      width: 100%;
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 10px 12px;
      border-radius: 8px;
      font-size: 0.85rem;
      outline: none;
      transition: border-color 0.2s;
    }
    select:focus { border-color: var(--accent-crimson); }

    .toggle-group { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; font-size: 0.85rem; }
    .toggle { position: relative; width: 44px; height: 24px; cursor: pointer; }
    .toggle input { opacity: 0; width: 0; height: 0; }
    .slider { position: absolute; top:0; left:0; right:0; bottom:0; background: #374151; border-radius: 24px; transition: 0.3s; }
    .slider:before { content:""; position: absolute; height: 18px; width: 18px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: 0.3s; }
    input:checked + .slider { background: var(--accent-crimson); }
    input:checked + .slider:before { transform: translateX(20px); }

    /* 快捷问题库 */
    .prompts-list { overflow-y: auto; max-height: 210px; flex-shrink: 0; padding: 10px 20px; border-bottom: 1px solid var(--border-color); }
    .prompt-chip {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      padding: 8px 11px;
      border-radius: 8px;
      font-size: 0.8rem;
      margin-bottom: 7px;
      cursor: pointer;
      color: var(--text-muted);
      transition: all 0.2s;
      display: block;
      text-align: left;
      width: 100%;
    }
    .prompt-chip:hover { border-color: var(--accent-gold); color: #fff; background: rgba(244, 162, 97, 0.08); transform: translateX(2px); }

    /* 历史记录 */
    .history-header {
      padding: 12px 20px 6px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .history-clear-btn {
      background: transparent;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      font-size: 0.72rem;
      padding: 2px 8px;
      border-radius: 6px;
      cursor: pointer;
    }
    .history-clear-btn:hover { border-color: var(--accent-crimson); color: #ff808b; }
    .history-list { overflow-y: auto; flex: 1; padding: 6px 20px 12px; }
    .history-empty { font-size: 0.78rem; color: #6b7280; padding: 8px 0; }
    .history-item {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 8px 10px;
      margin-bottom: 7px;
      cursor: pointer;
      transition: border-color 0.2s;
      position: relative;
    }
    .history-item:hover { border-color: var(--accent-cyan); }
    .history-q {
      font-size: 0.8rem;
      color: var(--text-main);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      padding-right: 20px;
    }
    .history-meta {
      margin-top: 4px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.7rem;
      color: var(--text-muted);
    }
    .history-origin {
      padding: 1px 6px;
      border-radius: 4px;
      background: rgba(255,255,255,0.06);
    }
    .history-del-btn {
      position: absolute;
      top: 6px;
      right: 7px;
      background: transparent;
      border: none;
      color: #6b7280;
      cursor: pointer;
      font-size: 0.85rem;
      line-height: 1;
      padding: 2px 4px;
    }
    .history-del-btn:hover { color: #ff808b; }

    /* 加入问答库按钮 */
    .archive-action-row {
      margin-top: 10px;
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .add-archive-btn {
      background: rgba(42, 157, 143, 0.16);
      border: 1px solid var(--accent-cyan);
      color: #7ae5d6;
      padding: 5px 12px;
      border-radius: 6px;
      font-size: 0.78rem;
      cursor: pointer;
      transition: all 0.2s;
    }
    .add-archive-btn:hover:not(:disabled) { background: rgba(42, 157, 143, 0.3); }
    .add-archive-btn:disabled, .add-archive-btn.added {
      opacity: 0.75;
      cursor: default;
      border-color: #52b788;
      color: #52b788;
      background: rgba(82, 183, 136, 0.12);
    }
    .add-archive-msg { font-size: 0.76rem; color: #ff808b; }

    /* 状态栏底部 */
    .sidebar-footer { padding: 12px 20px; font-size: 0.72rem; color: #6b7280; border-top: 1px solid var(--border-color); background: rgba(0,0,0,0.2); }

    /* 主聊天区 */
    main { flex: 1; display: flex; flex-direction: column; background: var(--bg-primary); position: relative; min-width: 0; min-height: 0; }
    header.chat-header {
      padding: 16px 24px;
      background: var(--bg-secondary);
      border-bottom: 1px solid var(--border-color);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    .header-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .chat-title { font-weight: 600; font-size: 1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .menu-btn, .drawer-close-btn {
      display: none;
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      border-radius: 8px;
      cursor: pointer;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .menu-btn { width: 34px; height: 34px; font-size: 1.1rem; line-height: 1; }
    .drawer-close-btn { width: 32px; height: 32px; font-size: 1.2rem; line-height: 1; }
    .menu-btn:hover, .drawer-close-btn:hover { border-color: var(--accent-gold); color: #fff; }
    .drawer-backdrop { display: none; }
    .badge-bar { display: flex; gap: 8px; }
    .badge {
      font-size: 0.72rem;
      padding: 4px 10px;
      border-radius: 20px;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.1);
      color: var(--text-muted);
      white-space: nowrap;
      flex-shrink: 0;
    }
    .badge.green { background: rgba(42, 157, 143, 0.15); border-color: var(--accent-cyan); color: #48cae4; }
    .badge.gold { background: rgba(244, 162, 97, 0.15); border-color: var(--accent-gold); color: #ffd166; }

    .chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }

    .msg { display: flex; gap: 14px; max-width: 820px; animation: fadeIn 0.3s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
    .msg.user { align-self: flex-end; flex-direction: row-reverse; }
    .msg.bot { align-self: flex-start; }

    .avatar {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: bold;
      font-size: 0.95rem;
      flex-shrink: 0;
    }
    .msg.bot .avatar { background: linear-gradient(135deg, #e63946, #9b2226); color: #fff; box-shadow: 0 0 12px var(--accent-glow); }
    .msg.user .avatar { background: #3a506b; color: #fff; }

    .msg-body {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      padding: 14px 18px;
      line-height: 1.65;
      font-size: 0.92rem;
      position: relative;
    }
    .msg.user .msg-body { background: #1f3b58; border-color: #2c527c; }

    .meta-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
      padding-top: 8px;
      border-top: 1px solid rgba(255,255,255,0.08);
      font-size: 0.75rem;
      align-items: center;
      color: var(--text-muted);
    }
    .tag { padding: 2px 8px; border-radius: 4px; font-weight: 500; }
    .tag-origin { background: rgba(230, 57, 70, 0.2); color: #ff758f; }
    .tag-sources { background: rgba(244, 162, 97, 0.18); color: #f4a261; }
    .tag-latency { color: #52b788; }

    /* 音频播放条 */
    .audio-player-box {
      margin-top: 10px;
      display: flex;
      align-items: center;
      gap: 10px;
      background: rgba(0,0,0,0.25);
      padding: 8px 12px;
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.06);
    }
    .play-btn {
      background: var(--accent-crimson);
      color: #fff;
      border: none;
      border-radius: 50%;
      width: 32px;
      height: 32px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.85rem;
      transition: transform 0.15s, background-color 0.2s;
    }
    .play-btn:hover { transform: scale(1.08); background: #f04856; }
    .audio-status { font-size: 0.78rem; color: var(--text-muted); }

    /* 输入区域 */
    .chat-input-area {
      padding: 18px 24px;
      background: var(--bg-secondary);
      border-top: 1px solid var(--border-color);
      display: flex;
      gap: 12px;
      align-items: flex-end;
    }
    .input-wrapper {
      flex: 1;
      min-height: 42px;
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      display: flex;
      align-items: center;
      padding: 0 14px;
      transition: border-color 0.2s;
    }
    .input-wrapper:focus-within { border-color: var(--accent-crimson); box-shadow: 0 0 8px var(--accent-glow); }
    textarea {
      width: 100%;
      background: transparent;
      border: none;
      outline: none;
      color: #fff;
      font-size: 0.95rem;
      resize: none;
      padding: 8px 0;
      height: 40px;
      min-height: 40px;
      max-height: 120px;
      line-height: 24px;
      overflow-y: hidden;
      display: block;
    }
    textarea::placeholder { color: var(--text-muted); }
    .send-btn {
      background: var(--accent-crimson);
      border: none;
      color: #fff;
      padding: 10px 22px;
      border-radius: var(--radius);
      font-weight: 600;
      cursor: pointer;
      font-size: 0.95rem;
      transition: all 0.2s;
      height: 42px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .send-btn:hover { background: #f04856; box-shadow: 0 0 10px var(--accent-glow); }
    .send-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    /* 响应式调整 */
    @media (max-width: 768px) {
      aside {
        position: fixed;
        top: 0;
        left: 0;
        bottom: 0;
        width: min(84vw, 320px);
        height: 100vh;
        height: 100dvh;
        z-index: 200;
        transform: translateX(-100%);
        transition: transform 0.25s ease;
        box-shadow: 4px 0 24px rgba(0, 0, 0, 0.5);
        padding-top: env(safe-area-inset-top);
        padding-bottom: env(safe-area-inset-bottom);
      }
      aside.open { transform: translateX(0); }
      .drawer-backdrop.open {
        display: block;
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.55);
        z-index: 150;
      }
      .menu-btn, .drawer-close-btn { display: inline-flex; }
      .prompts-list { max-height: 32vh; }
      header.chat-header { padding: calc(10px + env(safe-area-inset-top)) 12px 10px; gap: 8px; }
      .chat-title { font-size: 0.92rem; }
      .badge-bar { gap: 5px; flex-shrink: 0; }
      .badge { font-size: 0.68rem; padding: 3px 7px; }
      .chat-messages { padding: 14px 12px; gap: 14px; -webkit-overflow-scrolling: touch; }
      .msg { gap: 8px; max-width: 100%; }
      .avatar { width: 32px; height: 32px; font-size: 0.82rem; }
      .msg-body { padding: 11px 13px; max-width: calc(100% - 40px); word-break: break-word; overflow-wrap: anywhere; }
      .chat-input-area { padding: 10px 12px calc(10px + env(safe-area-inset-bottom)); gap: 8px; }
      textarea { font-size: 16px; }
      .send-btn { padding: 0 16px; white-space: nowrap; flex-shrink: 0; }
    }
    @media (max-width: 520px) {
      #statusBadge { display: none; }
    }
  </style>
</head>
<body>
  <div class="drawer-backdrop" id="drawerBackdrop" onclick="closeDrawer()"></div>
  <!-- 侧边栏配置 -->
  <aside id="sidebar">
    <div class="brand">
      <div>
        <h1><span>十日</span>终焉 · 语音问答</h1>
        <p>智能回响检索 · 微软自然音色朗读</p>
      </div>
      <button class="drawer-close-btn" type="button" onclick="closeDrawer()" aria-label="关闭侧边栏">×</button>
    </div>

    <div class="sidebar-section">
      <div class="sidebar-title">TTS 语音音色</div>
      <select id="voiceSelect">
        <option value="zh-CN-XiaoxiaoNeural">微软 晓晓 (女声·推荐)</option>
      </select>

      <div class="toggle-group">
        <span>自动朗读回答</span>
        <label class="toggle">
          <input type="checkbox" id="autoPlayToggle" checked>
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="sidebar-section" style="padding-bottom: 8px;">
      <div class="sidebar-title">高频精选问答 (点击直接提问)</div>
    </div>
    <div class="prompts-list">
      <button class="prompt-chip" onclick="askPreset('齐夏的回响是什么')">⚡ 齐夏的回响是什么？</button>
      <button class="prompt-chip" onclick="askPreset('余念安到底存不存在')">⚡ 余念安到底存不存在？</button>
      <button class="prompt-chip" onclick="askPreset('十二生肖体系怎么运作,如何晋升')">⚡ 十二生肖体系怎么运作，如何晋升？</button>
      <button class="prompt-chip" onclick="askPreset('楚天秋的成神计划是什么')">⚡ 楚天秋的成神计划是什么？</button>
      <button class="prompt-chip" onclick="askPreset('什么是回响')">⚡ 什么是回响？本质是什么？</button>
      <button class="prompt-chip" onclick="askPreset('全书结局是什么,新世界怎么诞生的,齐夏去哪了')">⚡ 全书结局与新世界是怎么诞生的？</button>
      <button class="prompt-chip" onclick="askPreset('乔家劲的回响是什么')">⚡ 乔家劲的回响是什么？</button>
      <button class="prompt-chip" onclick="askPreset('陈俊南的回响是什么')">⚡ 陈俊南的回响是什么？</button>
      <button class="prompt-chip" onclick="askPreset('终焉之地的目的是什么')">⚡ 终焉之地的目的是什么？</button>
    </div>

    <div class="history-header">
      <div class="sidebar-title" style="margin-bottom: 0;">历史记录</div>
      <button class="history-clear-btn" id="clearHistoryBtn" onclick="clearHistory()">清空</button>
    </div>
    <div class="history-list" id="historyList">
      <div class="history-empty">暂无提问历史</div>
    </div>

    <div class="sidebar-footer">
      <div id="kbStatsFooter">问答库加载中...</div>
      <div style="margin-top: 4px; display: flex; justify-content: space-between; align-items: center;">
        <span>本地检索优先 · 大模型补充</span>
        <a href="/admin" style="color: #6b7280; text-decoration: none;" title="大模型渠道管理">管理</a>
      </div>
    </div>
  </aside>

  <!-- 主交互区 -->
  <main>
    <header class="chat-header">
      <div class="header-left">
        <button class="menu-btn" type="button" onclick="openDrawer()" aria-label="打开菜单">☰</button>
        <div class="chat-title">终焉之信 · 记忆对话</div>
      </div>
      <div class="badge-bar">
        <span class="badge green" id="statusBadge">● 知识库就绪</span>
        <span class="badge" id="llmBadge">AI 状态加载中</span>
        <span class="badge gold" id="voiceBadge">晓晓</span>
      </div>
    </header>

    <div class="chat-messages" id="chatArea">
      <div class="msg bot">
        <div class="avatar">齐</div>
        <div class="msg-body">
          你好，我是《十日终焉》智能问答助手。<span id="welcomeStats">正在加载知识库统计...</span>你可以向我询问任何关于小说的人物动机、生肖游戏机制、回响本质、伏笔反转及最终骗局的问题，我将给出确切出处并以自然语音为你解答。
        </div>
      </div>
    </div>

    <div class="chat-input-area">
      <div class="input-wrapper">
        <textarea id="questionInput" placeholder="输入关于《十日终焉》的问题 (按 Enter 发送)..." rows="1"></textarea>
      </div>
      <button class="send-btn" id="sendBtn" onclick="submitQuestion()">
        <span>提问</span>
      </button>
    </div>
  </main>

  <script>
    const HISTORY_KEY = 'shiri_qa_history_v1';
    const MAX_HISTORY = 100;

    const chatArea = document.getElementById('chatArea');
    const questionInput = document.getElementById('questionInput');
    const sendBtn = document.getElementById('sendBtn');
    const voiceSelect = document.getElementById('voiceSelect');
    const autoPlayToggle = document.getElementById('autoPlayToggle');
    const voiceBadge = document.getElementById('voiceBadge');
    const llmBadge = document.getElementById('llmBadge');
    const kbStatsFooter = document.getElementById('kbStatsFooter');
    const welcomeStats = document.getElementById('welcomeStats');
    const historyListEl = document.getElementById('historyList');
    const sidebarEl = document.getElementById('sidebar');
    const drawerBackdrop = document.getElementById('drawerBackdrop');

    let currentAudio = null;

    function openDrawer() {
      if (sidebarEl) sidebarEl.classList.add('open');
      if (drawerBackdrop) drawerBackdrop.classList.add('open');
    }

    function closeDrawer() {
      if (sidebarEl) sidebarEl.classList.remove('open');
      if (drawerBackdrop) drawerBackdrop.classList.remove('open');
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeDrawer();
    });

    function mapOriginLabel(origin) {
      if (origin === '问答库') return '问答库(已校验)';
      if (origin === '大模型生成') return 'AI 生成';
      if (origin === '原文深度召回' || origin === '精读总集') return '原文检索';
      return origin || '';
    }

    function updateVoiceBadge() {
      if (!voiceSelect.options.length || voiceSelect.selectedIndex < 0) return;
      const selected = voiceSelect.options[voiceSelect.selectedIndex].text;
      voiceBadge.textContent = selected.split(' ')[1] || selected;
    }

    voiceSelect.addEventListener('change', updateVoiceBadge);

    function autoResize() {
      questionInput.style.height = 'auto';
      const h = Math.min(questionInput.scrollHeight, 120);
      questionInput.style.height = Math.max(h, 40) + 'px';
      questionInput.style.overflowY = questionInput.scrollHeight > 120 ? 'auto' : 'hidden';
    }

    questionInput.addEventListener('input', autoResize);

    questionInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitQuestion();
      }
    });

    function askPreset(text) {
      closeDrawer();
      questionInput.value = text;
      autoResize();
      submitQuestion();
    }

    function loadHistory() {
      try {
        const raw = localStorage.getItem(HISTORY_KEY);
        if (!raw) return [];
        const arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
      } catch (e) {
        return [];
      }
    }

    function saveHistory(list) {
      try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, MAX_HISTORY)));
      } catch (e) {
        console.warn('saveHistory failed:', e);
      }
    }

    function formatShortTime(ts) {
      if (!ts) return '';
      const d = new Date(ts);
      if (isNaN(d.getTime())) return '';
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      return `${mm}-${dd} ${hh}:${mi}`;
    }

    function addHistoryItem(item) {
      const list = loadHistory();
      list.unshift(item);
      saveHistory(list);
      renderHistory();
    }

    function markHistoryAdded(itemId) {
      if (!itemId) return;
      const list = loadHistory();
      let changed = false;
      for (let i = 0; i < list.length; i++) {
        if (list[i].id === itemId) {
          list[i].added = true;
          changed = true;
          break;
        }
      }
      if (changed) {
        saveHistory(list);
        renderHistory();
      }
    }

    function deleteHistoryItem(itemId) {
      const list = loadHistory().filter(x => x.id !== itemId);
      saveHistory(list);
      renderHistory();
    }

    function clearHistory() {
      localStorage.removeItem(HISTORY_KEY);
      renderHistory();
    }

    function replayHistoryItem(item) {
      closeDrawer();
      appendMessage('user', item.question);
      appendMessage('bot', item.answer || '', item, item);
    }

    function renderHistory() {
      const list = loadHistory();
      historyListEl.innerHTML = '';
      if (!list.length) {
        const empty = document.createElement('div');
        empty.className = 'history-empty';
        empty.textContent = '暂无提问历史';
        historyListEl.appendChild(empty);
        return;
      }
      list.forEach(item => {
        const row = document.createElement('div');
        row.className = 'history-item';
        row.title = item.question || '';
        row.addEventListener('click', () => replayHistoryItem(item));

        const qEl = document.createElement('div');
        qEl.className = 'history-q';
        qEl.textContent = item.question || '';

        const delBtn = document.createElement('button');
        delBtn.className = 'history-del-btn';
        delBtn.title = '删除此条记录';
        delBtn.textContent = '×';
        delBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          deleteHistoryItem(item.id);
        });

        const metaEl = document.createElement('div');
        metaEl.className = 'history-meta';
        const timeSpan = document.createElement('span');
        timeSpan.textContent = formatShortTime(item.ts);
        const originSpan = document.createElement('span');
        originSpan.className = 'history-origin';
        originSpan.textContent = mapOriginLabel(item.origin);
        metaEl.appendChild(timeSpan);
        metaEl.appendChild(originSpan);

        row.appendChild(qEl);
        row.appendChild(delBtn);
        row.appendChild(metaEl);
        historyListEl.appendChild(row);
      });
    }

    async function addToArchive(question, answer, sources, sig, btn, msgSpan, historyId) {
      btn.disabled = true;
      btn.textContent = '加入中...';
      msgSpan.textContent = '';
      try {
        const res = await fetch('/api/archive/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question, answer, sources: sources || '', sig })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          btn.disabled = false;
          btn.textContent = '＋ 加入问答库';
          msgSpan.textContent = data.error || ('加入失败 (HTTP ' + res.status + ')');
          return;
        }
        btn.textContent = '✓ 已在问答库';
        btn.classList.add('added');
        btn.disabled = true;
        if (historyId) {
          markHistoryAdded(historyId);
        }
      } catch (err) {
        btn.disabled = false;
        btn.textContent = '＋ 加入问答库';
        msgSpan.textContent = '请求失败: ' + err.message;
      }
    }

    function appendMessage(role, text, meta = null, historyContext = null) {
      const msgDiv = document.createElement('div');
      msgDiv.className = `msg ${role}`;

      const avatar = document.createElement('div');
      avatar.className = 'avatar';
      avatar.textContent = role === 'bot' ? '齐' : '我';

      const body = document.createElement('div');
      body.className = 'msg-body';
      body.textContent = text;

      if (meta) {
        const metaDiv = document.createElement('div');
        metaDiv.className = 'meta-tags';

        if (meta.origin) {
          const originSpan = document.createElement('span');
          originSpan.className = 'tag tag-origin';
          originSpan.textContent = `检索来源: ${mapOriginLabel(meta.origin)}`;
          metaDiv.appendChild(originSpan);
        }

        if (meta.sources) {
          const sourcesSpan = document.createElement('span');
          sourcesSpan.className = 'tag tag-sources';
          sourcesSpan.textContent = `出处: ${meta.sources}`;
          metaDiv.appendChild(sourcesSpan);
        }

        if (meta.elapsed_ms !== undefined && meta.elapsed_ms !== null) {
          const latencySpan = document.createElement('span');
          latencySpan.className = 'tag-latency';
          if (meta.origin === '大模型生成') {
            latencySpan.textContent = `耗时: ${meta.elapsed_ms}ms`;
          } else {
            const scorePct = Math.min(100, Math.round((meta.score || 0) * 100));
            latencySpan.textContent = `耗时: ${meta.elapsed_ms}ms (置信度: ${scorePct}%)`;
          }
          metaDiv.appendChild(latencySpan);
        }

        if (metaDiv.childNodes.length > 0) {
          body.appendChild(metaDiv);
        }

        if (meta.audio_url) {
          const audioBox = document.createElement('div');
          audioBox.className = 'audio-player-box';

          const playBtn = document.createElement('button');
          playBtn.className = 'play-btn';
          playBtn.textContent = '▶';
          const audioTargetUrl = meta.audio_url;
          playBtn.addEventListener('click', () => {
            playAudio(audioTargetUrl, playBtn);
          });
          audioBox.appendChild(playBtn);

          const audioStatus = document.createElement('span');
          audioStatus.className = 'audio-status';
          audioStatus.textContent = `朗读音频 (${voiceSelect.value})`;
          audioBox.appendChild(audioStatus);

          body.appendChild(audioBox);
        }

        if (role === 'bot' && meta.sig && historyContext && historyContext.question) {
          const actionRow = document.createElement('div');
          actionRow.className = 'archive-action-row';

          const addBtn = document.createElement('button');
          addBtn.className = 'add-archive-btn';
          const msgSpan = document.createElement('span');
          msgSpan.className = 'add-archive-msg';

          if (historyContext.added) {
            addBtn.textContent = '✓ 已在问答库';
            addBtn.classList.add('added');
            addBtn.disabled = true;
          } else {
            addBtn.textContent = '＋ 加入问答库';
            addBtn.addEventListener('click', () => {
              addToArchive(
                historyContext.question,
                text,
                meta.sources || '',
                meta.sig,
                addBtn,
                msgSpan,
                historyContext.id
              );
            });
          }

          actionRow.appendChild(addBtn);
          actionRow.appendChild(msgSpan);
          body.appendChild(actionRow);
        }
      }

      msgDiv.appendChild(avatar);
      msgDiv.appendChild(body);
      chatArea.appendChild(msgDiv);
      chatArea.scrollTop = chatArea.scrollHeight;
      return body;
    }

    function playAudio(url, btn) {
      if (currentAudio) {
        currentAudio.pause();
        document.querySelectorAll('.play-btn').forEach(b => b.textContent = '▶');
      }
      const audio = new Audio(url);
      currentAudio = audio;
      btn.textContent = '❚❚';
      audio.onended = () => { btn.textContent = '▶'; };
      audio.onerror = () => { btn.textContent = '▶'; alert('语音加载失败'); };
      audio.play().catch(e => { console.warn('Autoplay prevented:', e); btn.textContent = '▶'; });
    }

    async function submitQuestion() {
      const q = questionInput.value.trim();
      if (!q) return;

      appendMessage('user', q);
      questionInput.value = '';
      autoResize();
      sendBtn.disabled = true;

      // 临时加载状态
      const loadingBody = appendMessage('bot', '思考检索中...');

      try {
        const voice = voiceSelect.value;
        const res = await fetch('/api/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q, voice: voice })
        });
        const data = await res.json();

        // 移除加载占位
        chatArea.removeChild(loadingBody.parentElement);

        const histItem = {
          id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          ts: Date.now(),
          question: q,
          answer: data.answer || '',
          origin: data.origin || '',
          sources: data.sources || '',
          score: data.score || 0,
          audio_url: data.audio_url || '',
          sig: data.sig || '',
          added: false
        };
        addHistoryItem(histItem);
        appendMessage('bot', data.answer, data, histItem);

        if (autoPlayToggle.checked && data.audio_url) {
          const audio = new Audio(data.audio_url);
          if (currentAudio) currentAudio.pause();
          currentAudio = audio;
          audio.play().catch(e => console.log('Autoplay policy caught:', e));
        }
      } catch (err) {
        chatArea.removeChild(loadingBody.parentElement);
        const errText = '请求出错: ' + err.message;
        const histItem = {
          id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          ts: Date.now(),
          question: q,
          answer: errText,
          origin: '请求出错',
          sources: '',
          score: 0,
          audio_url: '',
          sig: '',
          added: false
        };
        addHistoryItem(histItem);
        appendMessage('bot', errText);
      } finally {
        sendBtn.disabled = false;
        questionInput.focus();
      }
    }

    async function initPage() {
      renderHistory();
      try {
        const res = await fetch('/api/status');
        if (res.ok) {
          const data = await res.json();
          const statsText = `问答库 ${data.qa_count} 条 · 精读 ${data.summary_sections} 块`;
          kbStatsFooter.textContent = statsText;
          welcomeStats.textContent = `已加载${statsText}。`;
          if (data.llm && data.llm.enabled) {
            llmBadge.textContent = `AI: ${data.llm.model || '已开启'}`;
            llmBadge.className = 'badge green';
          } else {
            llmBadge.textContent = 'AI 已关闭';
            llmBadge.className = 'badge';
          }
        }
      } catch (e) {
        kbStatsFooter.textContent = '知识库状态加载失败';
      }
      try {
        const vRes = await fetch('/api/voices');
        if (vRes.ok) {
          const vData = await vRes.json();
          const items = vData.items || [];
          if (items.length) {
            const prev = voiceSelect.value;
            voiceSelect.innerHTML = '';
            items.forEach(v => {
              const opt = document.createElement('option');
              opt.value = v.id;
              opt.textContent = v.name || v.id;
              voiceSelect.appendChild(opt);
            });
            const target = items.some(v => v.id === prev) ? prev : (vData.default || items[0].id);
            voiceSelect.value = target;
            updateVoiceBadge();
          }
        }
      } catch (e) {
        updateVoiceBadge();
      }
    }

    autoResize();
    initPage();
  </script>
</body>
</html>
"""

ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>大模型渠道管理 · 《十日终焉》语音问答</title>
  <style>
    :root {
      --bg-primary: #0f1117;
      --bg-secondary: #181b24;
      --bg-card: #202433;
      --accent-crimson: #e63946;
      --accent-gold: #f4a261;
      --accent-cyan: #2a9d8f;
      --text-main: #f1faee;
      --text-muted: #a8b2d1;
      --border-color: #2d3348;
      --radius: 12px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }
    body { background: var(--bg-primary); color: var(--text-main); min-height: 100vh; padding: 28px 20px; }
    .container { max-width: 960px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px; }
    header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; border-bottom: 1px solid var(--border-color); padding-bottom: 16px; }
    header h1 { font-size: 1.35rem; font-weight: 700; }
    header h1 span { color: var(--accent-crimson); }
    a.back-link { color: var(--text-muted); text-decoration: none; font-size: 0.9rem; border: 1px solid var(--border-color); padding: 6px 12px; border-radius: 8px; }
    a.back-link:hover { color: #fff; border-color: var(--accent-gold); }
    .card { background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: var(--radius); padding: 18px 20px; }
    .card h2 { font-size: 1rem; margin-bottom: 12px; color: #fff; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    input, select { background: var(--bg-card); border: 1px solid var(--border-color); color: var(--text-main); padding: 9px 12px; border-radius: 8px; font-size: 0.9rem; outline: none; }
    input:focus { border-color: var(--accent-crimson); }
    button { background: var(--bg-card); color: var(--text-main); border: 1px solid var(--border-color); padding: 8px 14px; border-radius: 8px; cursor: pointer; font-size: 0.85rem; transition: all 0.15s; }
    button:hover { border-color: var(--accent-gold); }
    button.primary { background: var(--accent-crimson); border-color: var(--accent-crimson); color: #fff; font-weight: 600; }
    button.primary:hover { background: #f04856; }
    button.cyan { background: rgba(42, 157, 143, 0.2); border-color: var(--accent-cyan); color: #7ae5d6; }
    button.danger { color: #ff808b; border-color: rgba(230, 57, 70, 0.4); }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    .status-bar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
    .pill { font-size: 0.8rem; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border-color); color: var(--text-muted); }
    .pill.on { background: rgba(42, 157, 143, 0.15); border-color: var(--accent-cyan); color: #48cae4; }
    .pill.off { background: rgba(230, 57, 70, 0.15); border-color: var(--accent-crimson); color: #ff808b; }
    .channel-list { display: flex; flex-direction: column; gap: 12px; }
    .channel-item { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 10px; padding: 14px 16px; display: flex; justify-content: space-between; align-items: center; gap: 14px; flex-wrap: wrap; }
    .channel-item.active { border-color: var(--accent-cyan); }
    .ch-meta { display: flex; flex-direction: column; gap: 4px; font-size: 0.85rem; color: var(--text-muted); }
    .ch-title { font-size: 0.98rem; color: #fff; font-weight: 600; display: flex; align-items: center; gap: 8px; }
    .ch-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 0.82rem; color: var(--text-muted); }
    .msg-box { margin-top: 10px; font-size: 0.85rem; padding: 10px 12px; border-radius: 8px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); display: none; white-space: pre-wrap; word-break: break-word; }
    .msg-box.show { display: block; }
    .msg-box.ok { border-color: var(--accent-cyan); color: #7ae5d6; }
    .msg-box.err { border-color: var(--accent-crimson); color: #ff808b; }
    @media (max-width: 640px) {
      body { padding: 16px 12px calc(20px + env(safe-area-inset-bottom)); }
      header h1 { font-size: 1.15rem; }
      .card { padding: 14px; }
      input, select { font-size: 16px; }
      .field input, .field select, #tokenInput { width: 100%; min-width: 0 !important; }
      .model-input-row input { width: auto; flex: 1; }
      .channel-item { flex-direction: column; align-items: stretch; }
      .ch-meta { word-break: break-all; }
      .form-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1><span>十日</span>终焉 · 大模型渠道管理</h1>
      <a class="back-link" href="/">← 返回问答首页</a>
    </header>

    <div class="card">
      <h2>管理员 Token</h2>
      <div class="row">
        <input id="tokenInput" type="password" placeholder="输入 X-Admin-Token" style="flex:1; min-width:220px;" />
        <button class="primary" onclick="saveToken()">保存并加载</button>
        <button onclick="clearToken()">清除</button>
      </div>
      <div id="authMsg" class="msg-box"></div>
    </div>

    <div class="card">
      <div class="status-bar">
        <div>
          <h2 style="margin-bottom:6px;">当前大模型状态</h2>
          <div id="currentInfo" style="font-size:0.88rem; color:var(--text-muted);">未加载</div>
        </div>
        <div class="row">
          <span id="statusPill" class="pill">--</span>
          <button class="danger" onclick="activateChannel('off')">关闭大模型</button>
        </div>
      </div>
      <div id="opMsg" class="msg-box"></div>
    </div>

    <div class="card">
      <h2>渠道列表</h2>
      <div id="channelList" class="channel-list"></div>
    </div>

    <div class="card">
      <div class="status-bar" style="margin-bottom:12px;">
        <h2 style="margin-bottom:0;">用户加入的问答</h2>
        <button onclick="loadUserArchive()">刷新列表</button>
      </div>
      <div id="userArchiveList" class="channel-list"></div>
      <div id="archiveMsg" class="msg-box"></div>
    </div>

    <div class="card">
      <h2 id="formTitle">新增 / 编辑渠道</h2>
      <div class="form-grid">
        <div class="field">
          <label>渠道 ID（[a-z0-9_-]{1,32}）</label>
          <input id="fId" placeholder="例如 openai-compat" />
        </div>
        <div class="field">
          <label>显示名称</label>
          <input id="fName" placeholder="例如 自定义大模型渠道" />
        </div>
        <div class="field">
          <label>API Base URL</label>
          <input id="fBase" placeholder="https://api.example.com/v1" />
        </div>
        <div class="field">
          <label>API Key（编辑时留空保留原 Key）</label>
          <input id="fKey" type="password" placeholder="请输入 API Key" />
        </div>
        <div class="field">
          <label>模型名称（可自动获取或手动输入）</label>
          <div class="model-input-row" style="display:flex; gap:8px; align-items:center;">
            <input id="fModel" placeholder="点击获取或手动输入模型名" style="flex:1; min-width:0;" />
            <button type="button" class="cyan" id="btnFetchModels" onclick="fetchChannelModels()" style="white-space:nowrap; flex-shrink:0;">获取模型</button>
          </div>
          <select id="fModelSelect" style="display:none; margin-top:6px;" onchange="onSelectFetchedModel()"></select>
          <div id="modelFetchHint" style="font-size:0.78rem; color:var(--text-muted); margin-top:4px;"></div>
        </div>
        <div class="field">
          <label>超时（秒）</label>
          <input id="fTimeout" type="number" step="0.5" value="15" />
        </div>
      </div>
      <div class="row" style="margin-top:14px;">
        <button class="primary" onclick="submitChannel()">保存渠道</button>
        <button onclick="resetForm()">清空表单</button>
      </div>
    </div>
  </div>

  <script>
    const TOKEN_KEY = 'novel_admin_token';
    let cachedChannels = [];

    function getToken() {
      return localStorage.getItem(TOKEN_KEY) || '';
    }

    function showBox(id, text, isOk) {
      const el = document.getElementById(id);
      el.textContent = text;
      el.className = 'msg-box show ' + (isOk ? 'ok' : 'err');
    }

    function hideBox(id) {
      document.getElementById(id).className = 'msg-box';
    }

    async function apiFetch(url, options = {}) {
      const tok = getToken();
      const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
      if (tok) headers['X-Admin-Token'] = tok;
      const res = await fetch(url, Object.assign({}, options, { headers }));
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || ('HTTP ' + res.status));
      }
      return data;
    }

    function saveToken() {
      const v = document.getElementById('tokenInput').value.trim();
      localStorage.setItem(TOKEN_KEY, v);
      loadChannels();
    }

    function clearToken() {
      localStorage.removeItem(TOKEN_KEY);
      document.getElementById('tokenInput').value = '';
      showBox('authMsg', '已清除本地 Token', true);
    }

    function renderState(data) {
      cachedChannels = data.channels || [];
      const pill = document.getElementById('statusPill');
      const info = document.getElementById('currentInfo');
      if (data.enabled && data.active !== 'off') {
        pill.textContent = '已开启: ' + data.active;
        pill.className = 'pill on';
        const cur = data.current || {};
        info.textContent = (cur.name || data.active) + ' | 模型: ' + (cur.model || '未设置') + ' | Base: ' + (cur.api_base || '');
      } else {
        pill.textContent = '已关闭大模型';
        pill.className = 'pill off';
        info.textContent = '仅使用本地检索（问答库 + 精读总集）';
      }

      const list = document.getElementById('channelList');
      list.innerHTML = '';
      cachedChannels.forEach(ch => {
        const isActive = data.active === ch.id && data.enabled;
        const item = document.createElement('div');
        item.className = 'channel-item' + (isActive ? ' active' : '');

        const meta = document.createElement('div');
        meta.className = 'ch-meta';

        const title = document.createElement('div');
        title.className = 'ch-title';
        title.textContent = (ch.name || ch.id) + ' (' + ch.id + ')';
        if (isActive) {
          const badge = document.createElement('span');
          badge.className = 'pill on';
          badge.textContent = '当前使用';
          title.appendChild(badge);
        }

        const line1 = document.createElement('div');
        line1.textContent = 'Base: ' + (ch.api_base || '-') + ' | 模型: ' + (ch.model || '未设置');
        const line2 = document.createElement('div');
        line2.textContent = 'Key: ' + (ch.api_key || '未配置') + ' | 超时: ' + (ch.timeout || 11) + 's';

        meta.appendChild(title);
        meta.appendChild(line1);
        meta.appendChild(line2);

        const actions = document.createElement('div');
        actions.className = 'ch-actions';

        const btnUse = document.createElement('button');
        btnUse.className = 'cyan';
        btnUse.textContent = '切换到此';
        btnUse.disabled = isActive;
        btnUse.onclick = () => activateChannel(ch.id);

        const btnTest = document.createElement('button');
        btnTest.textContent = '测试';
        btnTest.onclick = () => testChannel(ch.id, btnTest);

        const btnEdit = document.createElement('button');
        btnEdit.textContent = '编辑';
        btnEdit.onclick = () => fillForm(ch);

        const btnDel = document.createElement('button');
        btnDel.className = 'danger';
        btnDel.textContent = '删除';
        btnDel.disabled = data.active === ch.id;
        btnDel.onclick = () => deleteChannel(ch.id);

        actions.appendChild(btnUse);
        actions.appendChild(btnTest);
        actions.appendChild(btnEdit);
        actions.appendChild(btnDel);

        item.appendChild(meta);
        item.appendChild(actions);
        list.appendChild(item);
      });
    }

    async function loadChannels() {
      hideBox('authMsg');
      try {
        const data = await apiFetch('/api/admin/llm');
        renderState(data);
        showBox('authMsg', 'Token 验证成功，已加载配置', true);
        loadUserArchive();
      } catch (err) {
        showBox('authMsg', '加载失败: ' + err.message, false);
      }
    }

    async function activateChannel(id) {
      try {
        const data = await apiFetch('/api/admin/llm', {
          method: 'POST',
          body: JSON.stringify({ action: 'activate', id })
        });
        renderState(data);
        showBox('opMsg', id === 'off' ? '已关闭大模型，仅使用本地检索' : '已切换到渠道: ' + id, true);
      } catch (err) {
        showBox('opMsg', '切换失败: ' + err.message, false);
      }
    }

    async function testChannel(id, btn) {
      const oldText = btn.textContent;
      btn.disabled = true;
      btn.textContent = '测试中...';
      try {
        const res = await apiFetch('/api/admin/llm/test', {
          method: 'POST',
          body: JSON.stringify({ id })
        });
        if (res.ok) {
          showBox('opMsg', '测试成功 [' + id + '] (' + res.latency_ms + 'ms): ' + (res.sample || ''), true);
        } else {
          showBox('opMsg', '测试失败 [' + id + '] (' + res.latency_ms + 'ms): ' + (res.error || ''), false);
        }
      } catch (err) {
        showBox('opMsg', '测试请求异常: ' + err.message, false);
      } finally {
        btn.disabled = false;
        btn.textContent = oldText;
      }
    }

    function resetModelPicker() {
      const sel = document.getElementById('fModelSelect');
      const hint = document.getElementById('modelFetchHint');
      if (sel) {
        sel.innerHTML = '';
        sel.style.display = 'none';
      }
      if (hint) {
        hint.textContent = '';
        hint.style.color = 'var(--text-muted)';
      }
    }

    function onSelectFetchedModel() {
      const sel = document.getElementById('fModelSelect');
      if (sel && sel.value) {
        document.getElementById('fModel').value = sel.value;
      }
    }

    async function fetchChannelModels() {
      const apiBase = document.getElementById('fBase').value.trim();
      const apiKey = document.getElementById('fKey').value.trim();
      const cid = document.getElementById('fId').value.trim();
      const btn = document.getElementById('btnFetchModels');
      const sel = document.getElementById('fModelSelect');
      const hint = document.getElementById('modelFetchHint');

      if (!apiBase && !cid) {
        hint.textContent = '请先填写 API Base URL';
        hint.style.color = '#ff808b';
        return;
      }
      btn.disabled = true;
      btn.textContent = '获取中...';
      hint.textContent = '正在请求 /models ...';
      hint.style.color = 'var(--text-muted)';
      try {
        const res = await apiFetch('/api/admin/llm/models', {
          method: 'POST',
          body: JSON.stringify({ id: cid, api_base: apiBase, api_key: apiKey })
        });
        if (!res.ok) {
          sel.style.display = 'none';
          hint.textContent = '获取失败: ' + (res.error || '未知错误') + '（可直接手动输入模型名）';
          hint.style.color = '#ff808b';
          return;
        }
        const models = res.models || [];
        sel.innerHTML = '';
        const placeholderOpt = document.createElement('option');
        placeholderOpt.value = '';
        placeholderOpt.textContent = '请选择模型（共 ' + models.length + ' 个，也可直接在上方手动输入）';
        sel.appendChild(placeholderOpt);
        const curModel = document.getElementById('fModel').value.trim();
        models.forEach(m => {
          const opt = document.createElement('option');
          opt.value = m;
          opt.textContent = m;
          sel.appendChild(opt);
        });
        if (curModel && models.includes(curModel)) {
          sel.value = curModel;
        }
        sel.style.display = 'block';
        hint.textContent = '已获取 ' + models.length + ' 个模型（耗时 ' + res.latency_ms + 'ms），选择即填入，也可继续手动修改';
        hint.style.color = '#7ae5d6';
      } catch (err) {
        sel.style.display = 'none';
        hint.textContent = '获取失败: ' + err.message + '（可直接手动输入模型名）';
        hint.style.color = '#ff808b';
      } finally {
        btn.disabled = false;
        btn.textContent = '获取模型';
      }
    }

    function fillForm(ch) {
      document.getElementById('fId').value = ch.id || '';
      document.getElementById('fName').value = ch.name || '';
      document.getElementById('fBase').value = ch.api_base || '';
      document.getElementById('fKey').value = '';
      document.getElementById('fModel').value = ch.model || '';
      document.getElementById('fTimeout').value = ch.timeout || 11;
      document.getElementById('formTitle').textContent = '编辑渠道: ' + ch.id;
      resetModelPicker();
    }

    function resetForm() {
      document.getElementById('fId').value = '';
      document.getElementById('fName').value = '';
      document.getElementById('fBase').value = '';
      document.getElementById('fKey').value = '';
      document.getElementById('fModel').value = '';
      document.getElementById('fTimeout').value = '15';
      document.getElementById('formTitle').textContent = '新增 / 编辑渠道';
      resetModelPicker();
    }

    async function submitChannel() {
      const channel = {
        id: document.getElementById('fId').value.trim(),
        name: document.getElementById('fName').value.trim(),
        api_base: document.getElementById('fBase').value.trim(),
        api_key: document.getElementById('fKey').value.trim(),
        model: document.getElementById('fModel').value.trim(),
        timeout: parseFloat(document.getElementById('fTimeout').value || '11')
      };
      try {
        const data = await apiFetch('/api/admin/llm', {
          method: 'POST',
          body: JSON.stringify({ action: 'upsert', channel })
        });
        renderState(data);
        showBox('opMsg', '已保存渠道: ' + channel.id, true);
        document.getElementById('fKey').value = '';
      } catch (err) {
        showBox('opMsg', '保存失败: ' + err.message, false);
      }
    }

    async function deleteChannel(id) {
      if (!confirm('确定删除渠道 ' + id + ' 吗？')) return;
      try {
        const data = await apiFetch('/api/admin/llm', {
          method: 'POST',
          body: JSON.stringify({ action: 'delete', id })
        });
        renderState(data);
        showBox('opMsg', '已删除渠道: ' + id, true);
      } catch (err) {
        showBox('opMsg', '删除失败: ' + err.message, false);
      }
    }

    async function loadUserArchive() {
      const list = document.getElementById('userArchiveList');
      try {
        const data = await apiFetch('/api/admin/archive?added_by=user&limit=50');
        const items = data.items || [];
        list.innerHTML = '';
        if (!items.length) {
          const empty = document.createElement('div');
          empty.style.cssText = 'font-size:0.85rem; color:var(--text-muted);';
          empty.textContent = '暂无用户加入的问答条目';
          list.appendChild(empty);
          return;
        }
        items.forEach(item => {
          const row = document.createElement('div');
          row.className = 'channel-item';

          const meta = document.createElement('div');
          meta.className = 'ch-meta';
          meta.style.flex = '1';

          const title = document.createElement('div');
          title.className = 'ch-title';
          title.textContent = `#${item.id} ${item.question || ''}`;

          const line1 = document.createElement('div');
          line1.textContent = item.answer || '';

          const line2 = document.createElement('div');
          line2.textContent = `时间: ${item.ts || '-'} | 出处: ${item.sources || '-'}`;

          meta.appendChild(title);
          meta.appendChild(line1);
          meta.appendChild(line2);

          const actions = document.createElement('div');
          actions.className = 'ch-actions';
          const btnDel = document.createElement('button');
          btnDel.className = 'danger';
          btnDel.textContent = '删除';
          btnDel.onclick = () => deleteUserArchive(item.id);
          actions.appendChild(btnDel);

          row.appendChild(meta);
          row.appendChild(actions);
          list.appendChild(row);
        });
      } catch (err) {
        showBox('archiveMsg', '加载用户问答失败: ' + err.message, false);
      }
    }

    async function deleteUserArchive(id) {
      if (!confirm('确定从问答库删除条目 #' + id + ' 吗？')) return;
      try {
        await apiFetch('/api/admin/archive/delete', {
          method: 'POST',
          body: JSON.stringify({ id })
        });
        showBox('archiveMsg', '已删除条目 #' + id, true);
        loadUserArchive();
      } catch (err) {
        showBox('archiveMsg', '删除失败: ' + err.message, false);
      }
    }

    (function init() {
      const saved = getToken();
      if (saved) {
        document.getElementById('tokenInput').value = saved;
        loadChannels();
      }
    })();
  </script>
</body>
</html>
"""


# ==========================================
# 4. HTTP 请求处理服务 (原生多线程)
# ==========================================

_ARCHIVE_SIGNABLE_ORIGINS = ("大模型生成", "原文深度召回", "精读总集")


def _sign_qa(secret: str, question: str, answer: str, sources: str) -> str:
    """使用服务端 admin_token 对问答结果签名，防止公网伪造注入问答库。"""
    if not secret:
        return ""
    payload = f"{question}\x1f{answer}\x1f{sources}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class NovelQAHandler(http.server.BaseHTTPRequestHandler):
    kb: NovelKnowledgeBase
    tts: TTSService
    admin_token: str = ""
    llm_lock: threading.Lock = threading.Lock()
    archive_rate_lock: threading.Lock = threading.Lock()
    archive_rate_hits: Dict[str, List[float]] = collections.defaultdict(list)

    def _check_admin_auth(self) -> bool:
        provided = (self.headers.get("X-Admin-Token") or "").strip()
        expected = getattr(self, "admin_token", "") or ""
        if not expected or not hmac.compare_digest(provided, expected):
            self._send_json({"error": "Unauthorized: invalid or missing X-Admin-Token"}, code=401)
            return False
        return True

    def _read_json_body(self, allow_empty: bool = False) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            return None, "Invalid Content-Length"
        if length <= 0:
            if allow_empty:
                return {}, None
            return None, "Empty body"
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw_body)
        except Exception:
            return None, "Invalid JSON"
        if not isinstance(data, dict):
            return None, "Expected JSON object"
        return data, None

    def _build_llm_admin_view(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        active = cfg.get("active", "off")
        enabled = active != "off" and bool(self.kb.llm_client.enabled)
        masked_channels = []
        current_info: Dict[str, Any] = {
            "id": "off",
            "name": "已关闭大模型",
            "api_base": "",
            "model": "",
            "timeout": 0,
        }
        for ch in cfg.get("channels", []):
            item = {
                "id": str(ch.get("id") or ""),
                "name": str(ch.get("name") or ""),
                "api_base": str(ch.get("api_base") or ""),
                "api_key": mask_api_key(ch.get("api_key")),
                "model": str(ch.get("model") or ""),
                "timeout": float(ch.get("timeout") or 11.0),
            }
            masked_channels.append(item)
            if item["id"] == active:
                current_info = {
                    "id": item["id"],
                    "name": item["name"],
                    "api_base": item["api_base"],
                    "model": item["model"],
                    "timeout": item["timeout"],
                }
        return {
            "active": active,
            "enabled": enabled,
            "current": current_info,
            "channels": masked_channels,
        }

    def _send_json(self, data: Any, code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, file_path: str, mime_type: str):
        if not os.path.isfile(file_path):
            self.send_error(404, "File Not Found")
            return
        size = os.path.getsize(file_path)
        try:
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        # 首页
        if path == "/" or path == "/index.html":
            body = HTML_TEMPLATE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 管理后台页面 (不返回任何秘密)
        if path == "/admin":
            body = ADMIN_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 管理接口: 获取大模型渠道配置
        if path == "/api/admin/llm":
            if not self._check_admin_auth():
                return
            with self.llm_lock:
                cfg = load_llm_config()
            self._send_json(self._build_llm_admin_view(cfg))
            return

        # 管理接口: 查询用户加入的问答条目
        if path == "/api/admin/archive":
            if not self._check_admin_auth():
                return
            added_by = params.get("added_by", [""])[0].strip()
            try:
                limit = min(200, max(1, int(params.get("limit", [50])[0])))
            except (ValueError, TypeError):
                limit = 50
            with self.kb._qa_lock:
                entries_snapshot = list(self.kb.qa_entries)
            if added_by:
                filtered = [e for e in entries_snapshot if str(e.get("added_by") or "") == added_by]
            else:
                filtered = entries_snapshot
            filtered.reverse()
            items = []
            for e in filtered[:limit]:
                ans_text = str(e.get("answer") or "")
                items.append({
                    "id": e.get("id"),
                    "ts": e.get("ts", ""),
                    "question": e.get("question", ""),
                    "answer": ans_text[:120],
                    "sources": e.get("sources", ""),
                    "added_by": e.get("added_by", ""),
                })
            self._send_json({"total": len(filtered), "items": items})
            return

        # 状态检查
        if path == "/api/status" or path == "/health":
            self._send_json({
                "status": "ok",
                "book": "十日终焉",
                "archive_count": len(self.kb.qa_entries),
                "qa_count": len(self.kb.qa_entries),
                "summary_sections": len(self.kb.summary_sections),
                "voices": VOICES,
                "default_voice": DEFAULT_VOICE,
                "llm": {
                    "active": getattr(self.kb, "active_llm_id", "off"),
                    "model": self.kb.llm_client.model,
                    "enabled": self.kb.llm_client.enabled,
                },
            })
            return

        # 音色列表接口: /api/voices
        if path == "/api/voices":
            self._send_json({
                "status": "ok",
                "default": DEFAULT_VOICE,
                "voices": VOICES,
                "items": [
                    {"id": k, **v} for k, v in VOICES.items()
                ],
            })
            return

        # 问答库索引浏览
        if path == "/api/archive":
            q = params.get("q", [""])[0]
            try:
                limit = min(50, max(1, int(params.get("limit", [20])[0])))
            except (ValueError, TypeError):
                limit = 20
            if q:
                hits = self.kb.search_archive(q, top_k=limit, min_score=0.3)
                items = [h[1] for h in hits]
            else:
                items = self.kb.qa_entries[:limit]
            self._send_json({"total": len(self.kb.qa_entries), "items": items})
            return

        # 预存音频文件输出: /api/audio/<hash>
        if path.startswith("/api/audio/"):
            filename = os.path.basename(path)
            m = re.match(r"^([a-f0-9]{32})(?:\.(mp3|wav|aiff))?$", filename)
            if not m:
                self.send_error(404, "File Not Found")
                return

            hash_part = m.group(1)
            ext_part = m.group(2)

            # 1. 优先按请求扩展名检查磁盘有效缓存 (>0 字节)
            if ext_part:
                req_path = os.path.join(self.tts.cache_dir, f"{hash_part}.{ext_part}")
                if os.path.isfile(req_path) and os.path.getsize(req_path) > 0:
                    req_mime = "audio/mpeg" if ext_part == "mp3" else ("audio/aiff" if ext_part == "aiff" else "audio/wav")
                    self._send_file(req_path, req_mime)
                    return

            # 回退：当请求扩展名不匹配已有缓存时，查找同哈希任意格式有效缓存并正确回包
            fallback_types = [(".mp3", "audio/mpeg"), (".wav", "audio/wav"), (".aiff", "audio/aiff")]
            for cand_ext, cand_mime in fallback_types:
                cand_path = os.path.join(self.tts.cache_dir, f"{hash_part}{cand_ext}")
                if os.path.isfile(cand_path) and os.path.getsize(cand_path) > 0:
                    self._send_file(cand_path, cand_mime)
                    return

            # 2. 若在磁盘缓存未直接命中，但属于已注册待合成的任务，则按需同步/等待合成并直接输出
            pending_item = self.tts.get_pending(hash_part)
            if pending_item:
                pending_text, pending_voice, pending_rate = pending_item
                try:
                    file_path, mime, _ = self.tts.synthesize(pending_text, pending_voice, pending_rate)
                    if os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                        self._send_file(file_path, mime)
                        return
                except Exception as e:
                    print(f"[Handler] 按需 TTS 合成失败: {e}", file=sys.stderr)

            self.send_error(404, "File Not Found")
            return

        # 实时 TTS 接口: /api/tts?text=...&voice=...
        if path == "/api/tts":
            text = params.get("text", [""])[0].strip()
            voice = params.get("voice", [DEFAULT_VOICE])[0]
            rate = params.get("rate", ["+0%"])[0]
            if not text:
                self._send_json({"error": "缺少 text 参数"}, code=400)
                return
            # 500 字上限
            if len(text) > 500:
                self._send_json({"error": "text 超过 500 字限制"}, code=400)
                return
            # 令牌桶限流 (缓存命中不计)
            ck = self.tts.cache_key(text, voice, rate)
            if not self.tts.is_cached(ck) and not self.tts.check_rate_limit():
                self._send_json({"error": "请求过于频繁，请稍后重试"}, code=429)
                return
            try:
                audio_path, mime, _ = self.tts.synthesize(text, voice, rate)
                self._send_file(audio_path, mime)
            except Exception as e:
                self._send_json({"error": f"TTS 合成失败: {e}"}, code=500)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/admin/llm":
            if not self._check_admin_auth():
                return
            data, err = self._read_json_body(allow_empty=False)
            if err or data is None:
                self._send_json({"error": err or "Invalid request"}, code=400)
                return

            action = str(data.get("action") or "").strip()
            if action not in ("activate", "upsert", "delete"):
                self._send_json({"error": "Unsupported action"}, code=400)
                return

            with self.llm_lock:
                cfg = load_llm_config()
                channels = cfg.get("channels", [])

                if action == "activate":
                    cid = str(data.get("id") or "").strip()
                    if cid == "off":
                        cfg["active"] = "off"
                        save_llm_config(cfg)
                        self.kb.apply_llm_channel(enabled=False, channel_id="off")
                    else:
                        target = next((c for c in channels if c.get("id") == cid), None)
                        if target is None:
                            self._send_json({"error": f"Channel not found: {cid}"}, code=404)
                            return
                        cfg["active"] = cid
                        save_llm_config(cfg)
                        self.kb.apply_llm_channel(
                            api_base=target.get("api_base", ""),
                            api_key=target.get("api_key", ""),
                            model=target.get("model", ""),
                            timeout=target.get("timeout", 11.0),
                            enabled=True,
                            channel_id=cid,
                        )
                    self._send_json(self._build_llm_admin_view(cfg))
                    return

                if action == "upsert":
                    ch_in = data.get("channel")
                    if not isinstance(ch_in, dict):
                        self._send_json({"error": "channel must be an object"}, code=400)
                        return
                    cid = str(ch_in.get("id") or "").strip()
                    if not _CHANNEL_ID_RE.fullmatch(cid) or cid == "off":
                        self._send_json({"error": "id 必须匹配 [a-z0-9_-]{1,32} 且不能为 off"}, code=400)
                        return
                    api_base = str(ch_in.get("api_base") or "").strip().rstrip("/")
                    if not re.match(r"^https?://", api_base, re.IGNORECASE):
                        self._send_json({"error": "api_base 必须以 http:// 或 https:// 开头"}, code=400)
                        return
                    try:
                        timeout_val = float(ch_in.get("timeout", 11.0))
                        if timeout_val <= 0:
                            raise ValueError("timeout must be positive")
                    except (ValueError, TypeError):
                        self._send_json({"error": "timeout 必须为正数"}, code=400)
                        return

                    name = str(ch_in.get("name") or cid).strip() or cid
                    model = str(ch_in.get("model") or "").strip()
                    raw_key = ch_in.get("api_key")
                    key_str = str(raw_key).strip() if raw_key is not None else ""

                    existing = next((c for c in channels if c.get("id") == cid), None)
                    if existing is not None:
                        existing["name"] = name
                        existing["api_base"] = api_base
                        existing["model"] = model
                        existing["timeout"] = timeout_val
                        if key_str:
                            existing["api_key"] = key_str
                        updated_ch = existing
                    else:
                        updated_ch = {
                            "id": cid,
                            "name": name,
                            "api_base": api_base,
                            "api_key": key_str,
                            "model": model,
                            "timeout": timeout_val,
                        }
                        channels.append(updated_ch)

                    cfg["channels"] = channels
                    save_llm_config(cfg)
                    if cfg.get("active") == cid:
                        self.kb.apply_llm_channel(
                            api_base=updated_ch["api_base"],
                            api_key=updated_ch["api_key"],
                            model=updated_ch["model"],
                            timeout=updated_ch["timeout"],
                            enabled=True,
                            channel_id=cid,
                        )
                    self._send_json(self._build_llm_admin_view(cfg))
                    return

                if action == "delete":
                    cid = str(data.get("id") or "").strip()
                    if cid == cfg.get("active"):
                        self._send_json({"error": "不能删除当前正在使用的渠道"}, code=400)
                        return
                    new_channels = [c for c in channels if c.get("id") != cid]
                    if len(new_channels) == len(channels):
                        self._send_json({"error": f"Channel not found: {cid}"}, code=404)
                        return
                    cfg["channels"] = new_channels
                    save_llm_config(cfg)
                    self._send_json(self._build_llm_admin_view(cfg))
                    return

        if path == "/api/admin/llm/test":
            if not self._check_admin_auth():
                return
            data, err = self._read_json_body(allow_empty=True)
            if err or data is None:
                self._send_json({"error": err or "Invalid request"}, code=400)
                return

            cid = str(data.get("id") or "").strip()
            with self.llm_lock:
                cfg = load_llm_config()
            channels = cfg.get("channels", [])
            if not cid:
                cid = str(cfg.get("active") or "")
            if not cid or cid == "off":
                self._send_json({"ok": False, "latency_ms": 0, "error": "当前未激活大模型渠道"})
                return
            target = next((c for c in channels if c.get("id") == cid), None)
            if target is None:
                self._send_json({"error": f"Channel not found: {cid}"}, code=404)
                return

            test_timeout = min(float(target.get("timeout") or 11.0), 15.0)
            tmp_client = LLMClient(
                api_key=str(target.get("api_key") or ""),
                api_base=str(target.get("api_base") or ""),
                model=str(target.get("model") or ""),
                timeout=test_timeout,
            )
            result = tmp_client.test_connection()
            self._send_json(result)
            return

        if path == "/api/admin/llm/models":
            if not self._check_admin_auth():
                return
            data, err = self._read_json_body(allow_empty=True)
            if err or data is None:
                self._send_json({"error": err or "Invalid request"}, code=400)
                return

            cid = str(data.get("id") or "").strip()
            api_base = str(data.get("api_base") or "").strip().rstrip("/")
            api_key = str(data.get("api_key") or "").strip()

            if cid and (not api_key or not api_base):
                with self.llm_lock:
                    cfg = load_llm_config()
                target = next((c for c in cfg.get("channels", []) if c.get("id") == cid), None)
                if target is not None:
                    if not api_base:
                        api_base = str(target.get("api_base") or "").strip().rstrip("/")
                    if not api_key:
                        api_key = str(target.get("api_key") or "").strip()

            if not re.match(r"^https?://", api_base, re.IGNORECASE):
                self._send_json({"error": "api_base 必须以 http:// 或 https:// 开头"}, code=400)
                return

            tmp_client = LLMClient(
                api_key=api_key,
                api_base=api_base,
                model="",
                timeout=10.0,
            )
            result = tmp_client.list_models()
            self._send_json(result)
            return

        if path == "/api/admin/archive/delete":
            if not self._check_admin_auth():
                return
            data, err = self._read_json_body(allow_empty=False)
            if err or data is None:
                self._send_json({"error": err or "Invalid request"}, code=400)
                return
            entry_id = data.get("id")
            if entry_id is None or str(entry_id).strip() == "":
                self._send_json({"error": "Missing id"}, code=400)
                return
            removed = self.kb.delete_qa_entry(entry_id)
            if removed is None:
                self._send_json({"error": f"Entry not found: {entry_id}"}, code=404)
                return
            self._send_json({"status": "deleted", "id": removed.get("id")})
            return

        if path == "/api/archive/add":
            data, err = self._read_json_body(allow_empty=False)
            if err or data is None:
                self._send_json({"error": err or "Invalid request"}, code=400)
                return
            question = str(data.get("question") or "")
            answer = str(data.get("answer") or "")
            sources = str(data.get("sources") or "")
            sig = str(data.get("sig") or "")

            secret = getattr(self, "admin_token", "") or ""
            expected_sig = _sign_qa(secret, question, answer, sources)
            if not sig or not expected_sig or not hmac.compare_digest(sig, expected_sig):
                self._send_json({"error": "签名校验失败"}, code=403)
                return

            client_ip = self.client_address[0] if self.client_address else "unknown"
            now = time.time()
            cutoff = now - 3600.0
            with self.archive_rate_lock:
                hits = [t for t in self.archive_rate_hits.get(client_ip, []) if t > cutoff]
                if len(hits) >= 20:
                    self.archive_rate_hits[client_ip] = hits
                    self._send_json({"error": "操作过于频繁，每小时最多加入 20 条"}, code=429)
                    return
                hits.append(now)
                self.archive_rate_hits[client_ip] = hits

            if not self.kb.is_novel_related(question):
                self._send_json({"error": "问题与《十日终焉》无关，无法加入问答库"}, code=400)
                return

            entry, created = self.kb._save_evolved_qa_with_status(
                question, answer, sources, extra={"added_by": "user"}
            )
            if not entry:
                self._send_json({"error": "未通过质量校验"}, code=422)
                return
            status = "added" if created else "exists"
            self._send_json({"status": status, "id": entry.get("id")})
            return

        if path == "/api/ask":
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                self._send_json({"error": "Empty body"}, code=400)
                return
            raw_body = self.rfile.read(length).decode("utf-8")
            try:
                data = json.loads(raw_body)
            except Exception:
                self._send_json({"error": "Invalid JSON"}, code=400)
                return

            if not isinstance(data, dict):
                self._send_json({"error": "Expected JSON object"}, code=400)
                return

            question = str(data.get("question") or "")
            voice = str(data.get("voice") or DEFAULT_VOICE)
            rate = str(data.get("rate") or "+0%")
            client_ip = self.client_address[0] if self.client_address else ""

            # 1. 知识库检索
            ans = self.kb.ask(question, client_ip=client_ip)

            # 自动自进化: 当 self.kb.auto_evolve 为 True (或请求体携带 auto_evolve=True) 且属于限定深度召回结果时
            auto_evolve_enabled = getattr(self.kb, "auto_evolve", False) or bool(data.get("auto_evolve", False))
            if auto_evolve_enabled and ans.get("origin") in ("原文深度召回", "精读总集", "大模型生成"):
                try:
                    self.kb.save_evolved_qa(question, ans.get("answer", ""), ans.get("sources", ""))
                    self.tts.submit_background_synthesize(ans.get("answer", ""))
                except Exception as e:
                    print(f"[Web] 自进化持久化或TTS预热失败: {e}", file=sys.stderr)

            # 2. 生成语音 (音频哈希)
            answer_text = ans.get("answer", "")
            ck = self.tts.cache_key(answer_text, voice, rate)
            ans["audio_url"] = f"/api/audio/{ck}.mp3"

            # 3. 为可入库的结果生成防伪签名
            if ans.get("origin") in _ARCHIVE_SIGNABLE_ORIGINS:
                ans["sig"] = _sign_qa(
                    getattr(self, "admin_token", "") or "",
                    question,
                    str(answer_text),
                    str(ans.get("sources") or ""),
                )
            else:
                ans["sig"] = ""

            # 注册挂起任务，已缓存直接复用，未缓存则在后台线程预热合成，杜绝阻塞主问答请求
            self.tts.register_pending(ck, answer_text, voice, rate)
            if not self.tts.is_cached(ck):
                self.tts.submit_background_synthesize(answer_text, voice, rate)

            self._send_json(ans)
            return

        self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        # 简化日志输出
        pass


class ThreadingNovelServer(http.server.ThreadingHTTPServer):
    request_queue_size = 64
    daemon_threads = True

    def server_close(self):
        super().server_close()
        tts = getattr(self.RequestHandlerClass, "tts", None)
        if tts is not None and hasattr(tts, "shutdown"):
            try:
                tts.shutdown(wait=False)
            except Exception:
                pass

    def handle_error(self, request, client_address):
        exc_type, _, _ = sys.exc_info()
        if exc_type in (BrokenPipeError, ConnectionResetError):
            return
        super().handle_error(request, client_address)


def resolve_port(port_val: Any = None, default: int = 8080) -> int:
    """解析服务器端口，优先使用入参，其次读取环境变量 PORT，异常时回退到默认端口。"""
    if port_val is not None:
        try:
            p = int(port_val)
            if 0 <= p <= 65535:
                return p
        except (ValueError, TypeError):
            pass
    env_val = os.environ.get("PORT")
    if env_val is not None:
        try:
            p = int(env_val)
            if 0 <= p <= 65535:
                return p
        except (ValueError, TypeError):
            pass
    return default


def resolve_host(host_val: Optional[str] = None, default: str = "0.0.0.0") -> str:
    """解析服务器主机地址，优先使用入参，其次读取环境变量 HOST，回退到默认地址。"""
    if host_val:
        return host_val
    return os.environ.get("HOST", default)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    default_host = os.environ.get("HOST", "0.0.0.0")
    default_auto_evolve = os.environ.get("NOVEL_AUTO_EVOLVE", "").strip().lower() in ("1", "true", "yes")
    parser = argparse.ArgumentParser(description="《十日终焉》语音问答 Web 服务")
    parser.add_argument("--host", default=default_host, help=f"监听地址 (默认 {default_host}, 支持 HOST 环境变量)")
    parser.add_argument("--port", type=int, default=resolve_port(), help="监听端口 (默认 8080, 支持 PORT 环境变量)")
    parser.add_argument("--book", default="books/十日终焉", help="书籍目录 (默认 books/十日终焉)")
    parser.add_argument("--auto-evolve", action="store_true", default=default_auto_evolve, help="启用 L2 检索结果自进化持久化到问答库 (默认关闭)")
    return parser.parse_args(argv)


def make_server(host: str = "0.0.0.0", port: int = 8080, book_path: str = "books/十日终焉", auto_evolve: bool = False):
    if host == "0.0.0.0" and "HOST" in os.environ:
        host = os.environ.get("HOST", "0.0.0.0")
    port = resolve_port(port)
    tts = TTSService()
    kb = NovelKnowledgeBase(book_path, auto_evolve=auto_evolve, tts=tts)

    class BoundHandler(NovelQAHandler):
        pass

    BoundHandler.kb = kb
    BoundHandler.tts = tts
    BoundHandler.admin_token = resolve_admin_token()
    BoundHandler.llm_lock = threading.Lock()
    BoundHandler.archive_rate_lock = threading.Lock()
    BoundHandler.archive_rate_hits = collections.defaultdict(list)

    server = ThreadingNovelServer((host, port), BoundHandler)
    return server


def main():
    args = parse_args()

    server = make_server(host=args.host, port=args.port, book_path=args.book, auto_evolve=args.auto_evolve)
    print(f"==================================================")
    print(f" 《十日终焉》智能语音问答服务已启动")
    print(f" 访问地址: http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}")
    print(f" 问答条目: {len(server.RequestHandlerClass.kb.qa_entries)} 条")
    print(f" 精读分块: {len(server.RequestHandlerClass.kb.summary_sections)} 块")
    print(f" 自进化缓存: {'已开启' if args.auto_evolve else '已关闭'}")
    print(f" 管理后台 Token: {server.RequestHandlerClass.admin_token}")
    print(f"==================================================")

    def shutdown_handler(signum, frame):
        sig_name = "SIGTERM" if signum == signal.SIGTERM else f"信号 {signum}"
        print(f"\n[Server] 接收到 {sig_name}，正在优雅关闭服务...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, shutdown_handler)
    except (ValueError, AttributeError):
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
