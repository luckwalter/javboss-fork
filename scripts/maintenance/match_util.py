# -*- coding: utf-8 -*-
"""演员名归一化与匹配工具：把厂牌官网/资料站的日文名·罗马名，匹配到库里的 jav_idol。

要点：
- 日文旧字体/异体字统一（﨑→崎、髙→高、濵→浜…）
- 全角半角、空格、中点的统一
- 罗马名顺序不固定（"Yua Mikami" vs "Mikami Yua"）→ 按词片段排序后比较
"""
import re
import unicodedata

# 日文旧字体 / 异体字 → 常用字
VARIANT = {
    "﨑": "崎", "髙": "高", "濵": "浜", "邉": "辺", "邊": "辺", "神": "神",
    "齋": "斎", "齊": "斉", "𠮷": "吉", "瀨": "瀬", "塚": "塚", "德": "徳",
    "惠": "恵", "遲": "遅", "晝": "昼", "竈": "竃", "藪": "薮", "禰": "祢",
    "彌": "弥", "榮": "栄", "衞": "衛", "驍": "驍", "步": "歩", "穗": "穂",
    "茉": "茉", "來": "来", "萬": "万", "敎": "教", "藝": "芸", "萠": "萌",
    "駒": "駒", "鶴": "鶴", "壽": "寿", "會": "会", "樂": "楽", "澤": "沢",
    "國": "国", "觸": "触", "龍": "竜", "靑": "青", "黑": "黒", "橫": "横",
}
_VT = str.maketrans(VARIANT)


def norm_jp(s):
    """日文名归一化：异体字统一 + NFKC + 去空格/中点/括号内容"""
    if not s:
        return ""
    s = s.translate(_VT)
    s = unicodedata.normalize("NFKC", s)
    s = s.split("（")[0].split("(")[0]           # 去掉「読み」括号
    s = re.sub(r"[\s　・･·．\.\-–—_,，、]+", "", s)
    return s.strip().lower()


def norm_rom(s):
    """罗马名归一化：只留字母，词片段排序（解决姓名顺序不一致）"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    parts = [p for p in re.split(r"[^a-z]+", s) if p]
    if not parts:
        return ""
    # 单字母中间名/连字符情况：整体排序后再拼，容忍顺序差异
    return "".join(sorted(parts))


def match_score(db_name, db_roman, cand_jp, cand_romaji):
    """返回匹配得分：2=日文名精确, 1=罗马名一致, 0=不匹配"""
    if norm_jp(db_name) and norm_jp(db_name) == norm_jp(cand_jp):
        return 2
    if norm_rom(db_roman) and norm_rom(db_roman) == norm_rom(cand_romaji):
        return 1
    return 0


def pick_chinese_name(names, db_name):
    """从 javdb 给的多个名字里挑中文译名（排除与日文名同形的）"""
    nj = norm_jp(db_name)
    out = []
    for n in names:
        n = n.strip()
        if not n or norm_jp(n) == nj:
            continue
        # 排除纯假名/纯罗马
        if re.fullmatch(r"[\w\s\-]+", n) and not re.search(r"[\u4e00-\u9fff]", n):
            continue
        out.append(n)
    return out


if __name__ == "__main__":
    # 自测
    cases = [
        ("三上悠亜", "Yua Mikami", "三上悠亜", "MIKAMI YUA"),
        ("藤森里穂", "Riho Fujimori", "藤森里穂", "FUJIMORI RIHO"),
        ("奥田咲", "Saki Okuda", "奥田咲", "OKUDA SAKI"),
        ("﨑", "", "崎", ""),
        ("山手梨愛", "Ria Yamate", "山手梨愛", "YAMATE RIA"),
    ]
    for a, b, c, d in cases:
        print(f"{a:<10} vs {c:<10} -> score={match_score(a, b, c, d)}")
    print(pick_chinese_name(["三上悠亜", "三上悠亞", "鬼头桃菜"], "三上悠亜"))
