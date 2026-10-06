# -*- coding: utf-8 -*-
"""步骤 6：用 Wikipedia 跨语言链接补中文名（ja 条目 → zh 条目标题）+ 本地置信度过滤。

批量接口一次查 50 个标题；~1150 人只需 24 次请求。
产物：
  artifacts/wiki_zh.json       原始结果
  artifacts/wiki_zh_plan.json  过滤后计划（clean=填 chinese_name / alias=疑似改名进别名表）

过滤规则要点（实测教训）：
  - 含「光之美少女/科/屬/动画/专辑…」等关键词 → 疑似非人名，剔除
  - 双方都是汉字但几乎不重叠 → 不直接当中文名，进别名表（如 由愛可奈→水川潤）
  - 全假名日文名（如 みづなれい）不要按"日文无汉字"误杀——维基实体本身是权威匹配
"""
import json
import os
import re
import sqlite3
import unicodedata

from nas_env import ART, WORK, connect, docker_python, put_json, put_script, sh

FETCH = r'''# -*- coding: utf-8 -*-
import gzip, io, json, os, random, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

PROXY = os.environ.get("PROXY_URL", "")
UA = "javboss-maintenance/1.0 (personal media library metadata)"
BATCHES = json.load(open("/data/_probe/wiki_batches.json", encoding="utf-8"))
OP = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def get(url, tries=3):
    for k in range(tries):
        try:
            time.sleep(random.uniform(0.3, 0.8))
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with OP.open(req, timeout=30) as r:
                raw = r.read(3000000)
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return json.loads(raw.decode("utf-8", "replace"))
        except Exception as e:
            if k == tries - 1:
                return {"__err": str(e)[:80]}
            time.sleep(1.5)
    return {}


def work(batch):
    titles = "|".join(b["q"] for b in batch)
    url = ("https://ja.wikipedia.org/w/api.php?action=query&prop=langlinks&lllang=zh"
           "&redirects=1&format=json&formatversion=2&titles=" + urllib.parse.quote(titles))
    j = get(url)
    out = {}
    q = j.get("query", {})
    norm = {}
    for n in q.get("normalized", []) + q.get("redirects", []):
        norm[n["from"]] = n["to"]
    for p in q.get("pages", []):
        ll = p.get("langlinks") or []
        out[p.get("title", "")] = ll[0]["title"] if ll else ""
    res = []
    for b in batch:
        t = norm.get(b["q"], b["q"])
        res.append(dict(b, zh=out.get(t, ""), ja_title=t))
    return res


res = []
with ThreadPoolExecutor(max_workers=3) as ex:
    for r in ex.map(work, BATCHES):
        res += r

hit = [r for r in res if r["zh"]]
print(f"查询 {len(res)} 个名字，命中中文条目 {len(hit)}")
json.dump(res, open("/data/_probe/wiki_zh.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
'''

# ---------- 本地过滤 ----------
EQ = {"穗": "穂", "亞": "亚", "亜": "亚", "澤": "泽", "瀨": "濑", "瀬": "濑", "櫻": "樱", "結": "结",
      "葉": "叶", "繪": "绘", "絵": "绘", "廣": "广", "嶋": "岛", "濵": "滨", "濱": "滨", "邊": "边",
      "邉": "边", "髙": "高", "﨑": "崎", "恵": "惠", "優": "优", "愛": "爱", "麗": "丽", "華": "华",
      "蓮": "莲", "倉": "仓", "眞": "真", "來": "来", "歩": "步", "雙": "双", "樹": "树", "純": "纯",
      "紗": "纱", "綾": "绫", "德": "德", "徳": "德"}
NON_PERSON = ("光之美少女", "動畫", "动画", "漫画", "專輯", "专辑", "音樂", "音乐", "公司", "團體", "团体",
              "科", "屬", "属", "門", "學院", "学院", "組合", "品牌", "遊戲", "游戏", "電影", "电影")


def canon(s):
    s = unicodedata.normalize("NFKC", s).split("（")[0].split("(")[0].strip()
    return "".join(EQ.get(c, c) for c in s)


def kanji(s):
    return {c for c in s if re.match(r"[\u4e00-\u9fff]", c)}


def has_cjk(s):
    return bool(re.search(r"[\u4e00-\u9fff]", s))


def filter_plan(res):
    clean, alias_only, rejected = [], [], []
    for r in res:
        zh = (r.get("zh") or "").strip()
        if not zh:
            continue
        ja = r["q"]
        if any(k in zh for k in NON_PERSON):
            rejected.append((ja, zh, "疑似非人名"))
            continue
        if not has_cjk(zh):
            rejected.append((ja, zh, "无中文（纯外文）"))
            continue
        cj, cz = canon(ja), canon(zh)
        kj, kz = kanji(cj), kanji(cz)
        ov = len(kj & kz)
        ratio = ov / min(len(kj), len(kz)) if (kj and kz) else 0
        if ov >= 2 and ratio >= 0.5:
            clean.append({"id": r["id"], "ja": ja, "zh": zh, "ov": ov, "conf": "high"})
        elif (kj and kz and ratio >= 0.34) or (not kj or not kz):
            clean.append({"id": r["id"], "ja": ja, "zh": zh, "ov": ov, "conf": "low"})
        else:
            alias_only.append({"id": r["id"], "ja": ja, "zh": zh, "ov": ov})
    return clean, alias_only, rejected


def main():
    con = sqlite3.connect(os.path.join(ART, "javboss.db"))
    rows = con.execute("""
        SELECT id, name, COALESCE(roman_name,'') FROM jav_idol
        WHERE chinese_name IS NULL OR TRIM(chinese_name)='' ORDER BY id
    """).fetchall()
    con.close()

    batches, cur, seen = [], [], set()
    for rid, name, rom in rows:
        key = name.split("（")[0].split("(")[0].strip()
        if not key or key in seen:
            continue
        seen.add(key)
        cur.append({"id": rid, "q": key, "roman": rom})
        if len(cur) == 50:
            batches.append(cur)
            cur = []
    if cur:
        batches.append(cur)
    print(f"缺中文名 {len(rows)} 人 → 去重 {len(seen)} 个查询名 → {len(batches)} 批")

    cli = connect()
    sh(cli, f"mkdir -p {WORK}")
    sftp = cli.open_sftp()
    put_script(sftp, FETCH, f"{WORK}/wiki_zh.py")
    put_json(sftp, batches, f"{WORK}/wiki_batches.json")
    sftp.close()

    print("=== 抓取维基跨语言链接 ===")
    out, err = docker_python(cli, "/data/_probe/wiki_zh.py", t=1800)
    print(out)
    if err.strip():
        print("--- stderr ---\n", err[-400:])

    sftp = cli.open_sftp()
    dst = os.path.join(ART, "wiki_zh.json")
    sftp.get(f"{WORK}/wiki_zh.json", dst)
    sftp.close()
    cli.close()
    if os.path.getsize(dst) < 50:
        print("警告：wiki_zh.json 疑似空文件")
        return

    res = json.load(open(dst, encoding="utf-8"))
    clean, alias_only, rejected = filter_plan(res)
    print(f"采纳填 chinese_name：{len(clean)}（high {sum(1 for c in clean if c['conf'] == 'high')} / "
          f"low {sum(1 for c in clean if c['conf'] == 'low')}）｜仅别名 {len(alias_only)}｜剔除 {len(rejected)}")
    for ja, zh, why in rejected[:8]:
        print(f"  剔除: {ja} -> {zh}  ({why})")

    json.dump({"clean": clean, "alias": alias_only},
              open(os.path.join(ART, "wiki_zh_plan.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("已保存 wiki_zh_plan.json")


if __name__ == "__main__":
    main()
