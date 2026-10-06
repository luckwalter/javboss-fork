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

资源观测（2026-10-07 起，只读不改判据）：
  验证期间每 60s 采样一次 GET /system/resources（上游 #369 资源监控接口），
  结束时输出 CPU/RSS/goroutine 摘要与 data_disk 磁盘水位（>90% 标红）。
  接口不可用（旧镜像）时自动跳过，不影响验证本身。
  用途：建立「验证期间的资源基线」，部署/升级后对比可发现性能退化。
"""
import concurrent.futures as cf
import json
import os
import sys
import threading
import time
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


# ---------- 资源观测（只读，接口不可用时自动跳过） ----------

def sample_resources(cookie):
    try:
        req = urllib.request.Request(BASE + "/system/resources", headers={"Cookie": cookie})
        obj = json.loads(opener.open(req, timeout=15).read())
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def start_resource_sampler(cookie, interval=60):
    """后台线程周期采样；返回 (samples 列表, stop 函数)。接口不可用则返回 ([], noop)。"""
    samples = []
    if sample_resources(cookie) is None:
        print("资源观测: /system/resources 不可用（旧镜像?），跳过")
        return samples, lambda: None

    def run():
        while not stop.is_set():
            s = sample_resources(cookie)
            if s:
                samples.append(s)
            stop.wait(interval)

    stop = threading.Event()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return samples, stop.set


def print_resource_summary(samples):
    if not samples:
        return
    proc = [s.get("process") or {} for s in samples]
    rss = [p.get("rss_bytes") for p in proc if p.get("rss_bytes")]
    gor = [p.get("goroutines") for p in proc if p.get("goroutines")]
    disks = [s.get("data_disk") or {} for s in samples if s.get("data_disk")]
    print("-" * 62)
    print("资源观测（%d 次采样，仅服务端进程）:" % len(samples))
    if rss:
        print("  RSS 内存 : %.1f ~ %.1f MB" % (min(rss) / 1048576, max(rss) / 1048576))
    if gor:
        print("  goroutine: %d ~ %d" % (min(gor), max(gor)))
    for d in disks[-1:]:
        pct = d.get("used_percent") or 0
        flag = "  <-- ⚠️ 磁盘水位告警(>90%)" if pct > 90 else ""
        print("  data_disk: %.1f%% used (%.1f / %.0f GB)%s"
              % (pct, d.get("used_bytes", 0) / 2**30, d.get("total_bytes", 0) / 2**30, flag))
    # 首尾对比看增长趋势（验证是只读负载，RSS 不应持续上涨）
    if len(rss) >= 3 and rss[-1] > rss[0] * 1.5:
        print("  ⚠️ RSS 较验证开始上涨超过 50%%（%.1f -> %.1f MB），建议 docker logs 查异常"
              % (rss[0] / 1048576, rss[-1] / 1048576))


def main():
    ck = login()
    ids = fetch_ids(ck)
    print("列表视频数: %d" % len(ids))
    samples, stop_sampler = start_resource_sampler(ck)

    bad = []

    def probe(vid):
        s, b = get("%s/videos/%s/streams" % (BASE, vid), ck)
        return vid, s, b

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for vid, s, b in ex.map(probe, ids):
            if s != 200:
                bad.append((vid, s, b[:150].decode("utf-8", "replace")))
    stop_sampler()
    print_resource_summary(samples)

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
