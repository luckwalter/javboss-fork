#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""9_verify_playback.py —— 全量验证：遍历视频列表，逐个测 /videos/<id>/streams。

用途：升级/重编镜像、改播放相关代码、动挂载之后，一条命令确认「每个视频都能播」。
比抽查可靠得多——本仓库踩过两次「抽测正常但全量仍有坏点」的坑。

关键判据（见 docs/maintenance-zh.md 第 9 节）：
  /videos/<id>/stream  = 206  → 磁盘文件可达（直连流正常）
  /videos/<id>/streams = 200  → 播放探测正常（ffprobe 在位）
  若前者 206 而后者 404 → 就是 ffprobe 路径失配，跑 8_fix_ffprobe.py。

依赖：仅标准库（本机直连 NAS 的 JavBoss HTTP 端口，不需要 paramiko）。
用法：
  export NAS_HOST=192.168.1.10          # QNAP 地址
  export JAVBOSS_PASS=admin             # 可选
  python3 9_verify_playback.py
  python3 9_verify_playback.py --deep   # 额外抽测前 20 个的 /stream 与 m3u8
"""
import concurrent.futures as cf
import json
import os
import sys
import urllib.error
import urllib.request

NAS = os.environ.get("NAS_HOST", "192.168.1.10")
PORT = os.environ.get("JAVBOSS_PORT", "8655")
BASE = "http://%s:%s" % (NAS, PORT)
PW = os.environ.get("JAVBOSS_PASS", "admin")

# 绕开本机代理（沙箱/公司网络常注入 HTTP_PROXY，会破坏内网直连）
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
opener.addheaders = [("User-Agent", "javboss-verify-playback")]


def login():
    body = json.dumps({"password": PW}).encode()
    req = urllib.request.Request(BASE + "/auth/login", data=body,
                                 headers={"Content-Type": "application/json"})
    resp = opener.open(req, timeout=20)
    ck = resp.headers.get_all("Set-Cookie") or []
    resp.read()
    return "; ".join(c.split(";")[0] for c in ck)


def get(url, cookie, timeout=30, read=200):
    req = urllib.request.Request(url, headers={"Cookie": cookie})
    try:
        r = opener.open(req, timeout=timeout)
        return r.status, r.read(read)
    except urllib.error.HTTPError as e:
        return e.code, e.read(read)
    except Exception as e:
        return -1, str(e).encode()


def fetch_ids(cookie):
    req = urllib.request.Request(BASE + "/videos?limit=100000", headers={"Cookie": cookie})
    obj = json.loads(opener.open(req, timeout=120).read())
    items = obj if isinstance(obj, list) else (obj.get("items") or obj.get("videos") or obj.get("data") or [])
    return [it["id"] for it in items if isinstance(it, dict) and it.get("id")]


def main():
    ck = login()
    ids = fetch_ids(ck)
    print("列表视频数: %d" % len(ids))

    bad = []

    def probe(vid):
        s, b = get("%s/videos/%s/streams" % (BASE, vid), ck)
        return vid, s, b

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for vid, s, b in ex.map(probe, ids):
            if s != 200:
                bad.append((vid, s, b[:150].decode("utf-8", "replace")))

    print("=" * 62)
    print("可播放(streams=200): %d / %d" % (len(ids) - len(bad), len(ids)))
    print("异常: %d" % len(bad))
    for vid, s, msg in bad[:40]:
        print("  video=%-8s code=%-4s %s" % (vid, s, msg))

    if "--deep" in sys.argv and ids:
        print("\n--deep：抽测前 20 个的 /stream 与 m3u8")
        for vid in ids[:20]:
            a = get("%s/videos/%s/stream" % (BASE, vid), ck, read=2048)[0]
            m = get("%s/videos/%s/stream.m3u8" % (BASE, vid), ck, read=64)[0]
            # 206=Range 响应；200=整文件/无 Range。两者都算「文件可达」
            flag = "" if (a in (200, 206) and m in (200, 206)) else "  <-- 注意"
            print("  video=%-8s stream=%-4s m3u8=%-4s%s" % (vid, a, m, flag))

    if bad:
        print("\n有异常：先看容器日志 `docker logs --tail 200 javboss | grep -i probe`；"
              "若为 ffprobe 缺失，跑 8_fix_ffprobe.py。")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
