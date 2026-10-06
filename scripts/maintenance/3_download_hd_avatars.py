# -*- coding: utf-8 -*-
"""步骤 3：生成头像替换计划 → 在 NAS 容器内下载厂牌官方艺术照到 /data/_newavatars/。

依赖（先跑 0/1/2）：artifacts/javboss.db、artifacts/avatar_sizes.json、artifacts/actress_index.json
产物：artifacts/newavatar_result.json  →  步骤 4 按它执行替换。
本脚本只下载，不动现有头像。
"""
import json
import os
import sqlite3

from match_util import norm_jp, norm_rom
from nas_env import ART, DATA, WORK, connect, docker_python, put_json, put_script, sh

DL = r'''# -*- coding: utf-8 -*-
import json, os, struct, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

PROXY = os.environ.get("PROXY_URL", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REF = {"S1": "https://s1s1s1.com/", "MOODYZ": "https://moodyz.com/",
       "ATTACKERS": "https://attackers.net/", "MADONNA": "https://www.madonna-av.com/",
       "EBODY": "https://www.av-e-body.com/", "OPPAI": "https://www.oppai-av.com/",
       "WANZ": "https://www.wanz-factory.com/"}
OUT = "/data/_newavatars"
os.makedirs(OUT, exist_ok=True)
OP = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
plan = json.load(open("/data/_probe/dl_plan.json", encoding="utf-8"))


def jsize(b):
    if b[:2] == b"\xff\xd8":
        i = 2
        while i < len(b) - 9:
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            ln = struct.unpack(">H", b[i + 2:i + 4])[0]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", b[i + 5:i + 9])
                return (w, h)
            i += 2 + ln
        return None
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return (0, 0)
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24])
    return None


def fetch(u, site):
    for k in range(2):
        try:
            time.sleep(0.15)
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "image/*,*/*;q=0.8",
                                                     "Referer": REF.get(site, "https://example.com/")})
            with OP.open(req, timeout=25) as r:
                return r.read(2500000)
        except Exception:
            if k:
                return None
    return None


def work(p):
    best = None
    for c in p["cands"]:
        b = fetch(c["url"], c["site"])
        if not b:
            continue
        s = jsize(b)
        if not s or s[0] < 100:
            continue
        area = s[0] * s[1]
        if best is None or area > best[3]:
            best = (b, s, c, area)
    rec = {"id": p["id"], "name": p["name"], "old": p["old"], "ok": False}
    if best:
        fn = f"{OUT}/{p['id']}.jpg"
        with open(fn, "wb") as f:
            f.write(best[0])
        rec.update({"ok": True, "new": [best[1][0], best[1][1]], "site": best[2]["site"],
                    "url": best[2]["url"], "bytes": len(best[0])})
    return rec


res = []
with ThreadPoolExecutor(max_workers=6) as ex:
    for r in ex.map(work, plan):
        res.append(r)

with open("/data/_probe/newavatar_result.json", "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False)
ok = [r for r in res if r["ok"]]
print(f"下载完成 ok={len(ok)}/{len(res)}")
for r in ok[:8]:
    print(f'  {r["name"]:<14} {r["old"]} -> {r["new"]}  {r["site"]}')
'''


def main():
    os.makedirs(ART, exist_ok=True)
    idx = json.load(open(os.path.join(ART, "actress_index.json"), encoding="utf-8"))
    sizes = json.load(open(os.path.join(ART, "avatar_sizes.json"), encoding="utf-8"))

    by_jp, by_rom = {}, {}
    for i in idx:
        nj, nr = norm_jp(i["jp"]), norm_rom(i["romaji"])
        if nj:
            by_jp.setdefault(nj, []).append(i)
        if nr:
            by_rom.setdefault(nr, []).append(i)

    con = sqlite3.connect(os.path.join(ART, "javboss.db"))
    rows = con.execute(
        "SELECT id, name, COALESCE(roman_name,''), COALESCE(avatar_file,'') FROM jav_idol").fetchall()
    con.close()

    plan, matched = [], 0
    for rid, name, rom, avfile in rows:
        nj, nr = norm_jp(name), norm_rom(rom)
        cands = list(by_jp.get(nj, []))
        if nr:
            for c in by_rom.get(nr, []):
                if c not in cands:
                    cands.append(c)
        if not cands:
            continue
        matched += 1
        seen, cl = set(), []
        for c in cands:
            if c["img"] in seen:
                continue
            seen.add(c["img"])
            cl.append({"url": c["img"], "site": c["site"], "jp": c["jp"]})
        old = sizes.get(str(rid))
        plan.append({"id": rid, "name": name, "old": old, "cands": cl})

    print(f"库内 {len(rows)} 人，匹配到厂牌素材 {matched} 人（计划下载 {sum(len(p['cands']) for p in plan)} 张图）")
    lowcnt = sum(1 for p in plan if not p["old"] or min(p["old"][0], p["old"][1]) < 700)
    print(f"其中当前短边<700（值得替换）: {lowcnt}，无头像: {sum(1 for p in plan if not p['old'])}")

    cli = connect()
    sh(cli, f"mkdir -p {WORK}")
    sftp = cli.open_sftp()
    put_json(sftp, plan, f"{WORK}/dl_plan.json")
    put_script(sftp, DL, f"{WORK}/dl.py")
    sftp.close()

    print("=== 下载中（约 1200 张，6 并发）===")
    out, err = docker_python(cli, "/data/_probe/dl.py", t=2400)
    print(out)
    if err.strip():
        print("--- stderr ---\n", err[-600:])

    sftp = cli.open_sftp()
    dst = os.path.join(ART, "newavatar_result.json")
    sftp.get(f"{WORK}/newavatar_result.json", dst)
    sftp.close()
    cli.close()
    print(f"已保存 {dst}  {os.path.getsize(dst) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
