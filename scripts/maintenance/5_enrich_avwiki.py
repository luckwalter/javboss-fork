# -*- coding: utf-8 -*-
"""步骤 5：全量抓取 av-wiki 女优资料（生年月日/身高/三围/别名），低并发友好。

依赖：artifacts/javboss.db（步骤 0）
产物：artifacts/avwiki_full.json  →  步骤 7 落库。
坑：资料在 <meta og:description> 的 content 里——正则必须打在原始 HTML 上，不能先去标签。
实测 917 人 4 并发约 33 分钟（部分请求 30s 超时重试），不是 10 分钟。
"""
import json
import os
import sqlite3

from nas_env import ART, WORK, connect, docker_python, put_json, put_script, sh

FETCH = r'''# -*- coding: utf-8 -*-
import gzip, html, io, json, os, random, re, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

PROXY = os.environ.get("PROXY_URL", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OP = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
TARGETS = json.load(open("/data/_probe/avwiki_targets.json", encoding="utf-8"))
VARIANT = str.maketrans({"﨑": "崎", "髙": "高", "濵": "浜", "邉": "辺", "邊": "辺",
                         "齋": "斎", "齊": "斉", "瀨": "瀬", "德": "徳", "惠": "恵", "澤": "沢"})


def nj(s):
    if not s:
        return ""
    s = s.translate(VARIANT).split("（")[0].split("(")[0]
    return re.sub(r"[\s　・･·．\.\-–—_,，、]+", "", s).strip().lower()


def get(url, tries=3):
    for k in range(tries):
        try:
            time.sleep(random.uniform(0.8, 2.0))
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "ja"})
            with OP.open(req, timeout=30) as r:
                raw = r.read(1200000)
                if r.headers.get("Content-Encoding") == "gzip":
                    try:
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    except Exception:
                        pass
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return ""
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return ""


def work(t):
    q = urllib.parse.quote(t["name"])
    h = get("https://av-wiki.net/?s=" + q)
    rec = dict(t, url="", birth_date="", height="", bust="", waist="", hips="", alias="", kana="", ok=False)
    if not h:
        return rec
    links = re.findall(r'href="(https://av-wiki\.net/av-actress/[a-z0-9\-]+/)"', h)
    if not links:
        return rec
    # 优先选与目标 roman 名匹配的链接（各词都在 slug 里）
    best = None
    rom_parts = [x for x in re.split(r"[^a-z]+", (t.get("roman") or "").lower()) if x][:2]
    for u in links:
        slug = u.rstrip("/").split("/")[-1]
        if rom_parts and all(p in slug for p in rom_parts):
            best = u
            break
    url = best or links[0]
    d = get(url)
    if not d:
        return rec
    rec["url"] = url
    bd = re.search(r"生年月日[:：]\s*(\d{4})年(\d{1,2})月(\d{1,2})日", d)
    sz = re.search(r"サイズ[:：]\s*T(\d+)-B(\d+)-W(\d+)-H(\d+)", d)
    nm = re.search(r"AV女優名[:：]\s*([^\s<（(]{2,20})", d)
    kana = re.search(r"AV女優名[:：][^（(]*[（(]([^）)]{1,20})[）)]", d)
    al = re.search(r"別名義[:：](.{0,120}?)(?:生年月日|サイズ|SNS|$)", re.sub(r"<[^>]+>", " ", html.unescape(d)))
    if bd:
        rec["birth_date"] = f"{bd.group(1)}-{int(bd.group(2)):02d}-{int(bd.group(3)):02d}"
    if sz:
        rec.update(height=sz.group(1), bust=sz.group(2), waist=sz.group(3), hips=sz.group(4))
    if nm:
        rec["page_name"] = nm.group(1).strip()
    if kana:
        rec["kana"] = kana.group(1).strip()
    if al:
        rec["alias"] = re.sub(r"\s+", " ", al.group(1)).strip()[:100]
    rec["ok"] = bool(rec["birth_date"] or rec["height"])
    return rec


out = []
with ThreadPoolExecutor(max_workers=4) as ex:
    for i, r in enumerate(ex.map(work, TARGETS), 1):
        out.append(r)
        if i % 50 == 0:
            print(f"  进度 {i}/{len(TARGETS)}  命中 {sum(1 for x in out if x['ok'])}", flush=True)

json.dump(out, open("/data/_probe/avwiki_full.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n完成：命中 {sum(1 for x in out if x['ok'])}/{len(out)}")
'''


def main():
    con = sqlite3.connect(os.path.join(ART, "javboss.db"))
    rows = con.execute("""
        SELECT id, name, COALESCE(roman_name,'')
        FROM jav_idol
        WHERE height_cm IS NULL OR bust IS NULL OR waist IS NULL OR hips IS NULL
           OR birth_date IS NULL OR TRIM(CAST(birth_date AS TEXT))=''
           OR roman_name IS NULL OR TRIM(roman_name)=''
        ORDER BY id
    """).fetchall()
    con.close()
    targets = [{"id": r[0], "name": r[1], "roman": r[2]} for r in rows]
    print(f"待补资料 {len(targets)} 人（缺生日/身高/三围/罗马名任一）")

    cli = connect()
    sh(cli, f"mkdir -p {WORK}")
    sftp = cli.open_sftp()
    put_script(sftp, FETCH, f"{WORK}/avwiki_full.py")
    put_json(sftp, targets, f"{WORK}/avwiki_targets.json")
    sftp.close()

    print("=== 抓取中（4 并发，可能 20-40 分钟，耐心等）===")
    out, err = docker_python(cli, "/data/_probe/avwiki_full.py", t=7200)
    print(out[-3000:])
    if err.strip():
        print("--- stderr ---\n", err[-600:])

    sftp = cli.open_sftp()
    dst = os.path.join(ART, "avwiki_full.json")
    try:
        sftp.get(f"{WORK}/avwiki_full.json", dst)
        sz = os.path.getsize(dst)
        if sz < 100:
            print(f"警告：{dst} 只有 {sz} 字节，疑似远端没生成（sftp.get 的 0 字节坑）")
        else:
            print(f"已保存 {dst}  {sz / 1024:.1f} KB")
    except IOError as e:
        print("拉取失败", e)
    sftp.close()
    cli.close()


if __name__ == "__main__":
    import urllib.error  # noqa: F401  (远端脚本用到，此处仅为提示)
    main()
