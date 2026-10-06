# -*- coding: utf-8 -*-
"""步骤 2：抓 Will 集团 7 家厂牌官网的演员索引（日文名/罗马名/官方艺术照 URL）。

实测背景（2026-10-06）：这是唯一稳定可用的"官方艺术照"来源，覆盖库内 ~57% 演员。
xslist / minnano-av / javlibrary 被 Cloudflare 403；SOD/Prestige 等站 squid 上游不承载。

产物：artifacts/actress_index.json  →  步骤 3（下载头像）、步骤 7（补罗马名）共用。
耗时约 3-6 分钟（4 并发抓几百页）。
"""
import os

from nas_env import ART, DATA, WORK, connect, docker_python, put_script, sh

CRAWL = r'''# -*- coding: utf-8 -*-
import gzip, io, json, os, random, re, struct, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

PROXY = os.environ.get("PROXY_URL", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT = "/data/_probe/actress_index.json"
IMG_DIR = "/data/_probe/sample_imgs"
os.makedirs(IMG_DIR, exist_ok=True)

SITES = [
    ("S1",         "https://s1s1s1.com"),
    ("MOODYZ",     "https://moodyz.com"),
    ("ATTACKERS",  "https://attackers.net"),
    ("MADONNA",    "https://www.madonna-av.com"),
    ("EBODY",      "https://www.av-e-body.com"),
    ("OPPAI",      "https://www.oppai-av.com"),
    ("WANZ",       "https://www.wanz-factory.com"),
]
OP = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def get(url, tries=3):
    for k in range(tries):
        try:
            time.sleep(random.uniform(0.25, 0.7))
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                "Accept-Language": "ja,en;q=0.8", "Referer": "https://example.com/",
            })
            with OP.open(req, timeout=25) as r:
                raw = r.read(2000000)
                if r.headers.get("Content-Encoding") == "gzip":
                    try:
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    except Exception:
                        pass
                return raw.decode("utf-8", "replace")
        except Exception:
            if k == tries - 1:
                return ""
            time.sleep(1.0)
    return ""


CARD = re.compile(
    r'<a class="img"[^>]*href="[^"]*/actress/detail/(\d+)"[^>]*>\s*<img[^>]*data-src="([^"]+)"'
    r'.*?</a>\s*<p class="name">([^<]+)</p>\s*<p class="en[^"]*">([^<]*)</p>',
    re.S)


def parse(html):
    out = []
    for m in CARD.finditer(html):
        out.append({"id": m.group(1), "img": m.group(2),
                    "jp": m.group(3).strip(), "romaji": m.group(4).strip()})
    return out


def rows_of(base):
    h = get(base + "/actress")
    rows = sorted(set(re.findall(r'actress/([a-z]{1,3})"', h)))
    return [r for r in rows if r not in ("detail",)]


def maxpage(html):
    ps = [int(x) for x in re.findall(r'\?page=(\d+)', html)]
    return max(ps) if ps else 1


def crawl_site(args):
    site, base = args
    rows = rows_of(base)
    if not rows:
        return [], f"{site}: 未取到行分类"
    items = []
    log = [f"{site}: rows={rows}"]
    for row in rows:
        h1 = get(f"{base}/actress/{row}")
        if not h1:
            log.append(f"  {row}: 抓取失败")
            continue
        n = maxpage(h1)
        got = parse(h1)
        items += [dict(x, site=site, row=row) for x in got]
        for p in range(2, n + 1):
            hp = get(f"{base}/actress/{row}?page={p}")
            items += [dict(x, site=site, row=row, page=p) for x in parse(hp)]
        log.append(f"  {row}: pages={n} cards={len(got)}")
    return items, "\n".join(log)


all_items = []
with ThreadPoolExecutor(max_workers=4) as ex:
    for items, log in ex.map(crawl_site, SITES):
        print(log)
        all_items += items

for it in all_items:
    it["file"] = os.path.basename(urllib.parse.urlparse(it["img"]).path)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(all_items, f, ensure_ascii=False)
print(f"\n总计 {len(all_items)} 条，唯一 actress id {len({(i['site'], i['id']) for i in all_items})}")
'''


def main():
    os.makedirs(ART, exist_ok=True)
    cli = connect()
    sh(cli, f"mkdir -p {WORK}")
    sftp = cli.open_sftp()
    put_script(sftp, CRAWL, f"{WORK}/crawl.py")
    sftp.close()

    print("=== 抓取厂牌演员索引（约 3-6 分钟）===")
    out, err = docker_python(cli, "/data/_probe/crawl.py", t=1800)
    print(out)
    if err.strip():
        print("--- stderr ---\n", err[-800:])

    sftp = cli.open_sftp()
    dst = os.path.join(ART, "actress_index.json")
    try:
        sftp.get(f"{WORK}/actress_index.json", dst)
        print(f"已保存 {dst}  {os.path.getsize(dst) / 1024:.1f} KB")
    except IOError as e:
        print("拉取失败:", e)
    sftp.close()
    cli.close()


if __name__ == "__main__":
    main()
