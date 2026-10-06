#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8_fix_ffprobe.py —— 修复「视频文件或所在目录不存在」播放故障（ffprobe 路径失配）。

现象：网页点开视频报「视频文件或所在目录不存在」，但文件/挂载/DB 路径都正常。

原因：JavBoss 容器模式下 ffprobe 路径 **硬编码** 为 /app/internal/bin/ffprobe
（internal/util/video.go: ContainerFFBinaryDir，JAVBOSS_CONTAINER=1 时忽略
FFPROBE_PATH/FFMPEG_PATH 环境变量）。而官方镜像布局随版本变过：早期版本把
ffmpeg/ffprobe 放 /usr/local/bin/，v2.1.1 的 Dockerfile 才改成 ./internal/bin/。
若 fork 镜像是「新版源码二进制 + 旧版官方底座」，就会找不到 ffprobe，
ProbePlaybackSupport 抛 os.ErrNotExist，又被 respondPlaybackError 优先命中
errors.Is(err, os.ErrNotExist) 分支 → 误报 404「视频文件或所在目录不存在」。
详见 docs/maintenance-zh.md 第 9 节。

铁证：GET /videos/<id>/stream = 206（直连出数据），GET /videos/<id>/streams = 404。

用法：
  export NAS_PASS=xxxx
  python3 8_fix_ffprobe.py            # 检测 → 缺失则补齐 → 重启 → 验证 → commit 固化
  python3 8_fix_ffprobe.py --check    # 只检测现状，不改动
可选环境变量：
  JAVBOSS_CONTAINER   容器名（默认 javboss）
  JAVBOSS_BASE_IMAGE  底座镜像（默认 ghcr.io/solr159/javboss:v2.1.0）
  JAVBOSS_FIX_TAG     固化出的新镜像 tag（默认 javboss-fork:2.1.2）
"""
import os
import sys
import time

import nas_env as E

D = E.DOCKER
TMP = E.DATA + "/_ffprobe_fix"
CONTAINER = os.environ.get("JAVBOSS_CONTAINER", "javboss")
BASE_IMAGE = os.environ.get("JAVBOSS_BASE_IMAGE", "ghcr.io/solr159/javboss:v2.1.0")
FIX_TAG = os.environ.get("JAVBOSS_FIX_TAG", "javboss-fork:2.1.2")
BINS = ("ffprobe", "ffmpeg")
CHECK_ONLY = "--check" in sys.argv
# 容器名不能以下划线开头（Docker 限制）
SRC_CT = "jbfixsrc"


def run(c, cmd, t=600):
    return E.sh(c, cmd, t)


def size_of(c, path):
    """远端文件大小（0 表示不存在）"""
    out, _ = run(c, "s=$(wc -c < '%s' 2>/dev/null || echo 0); echo $s" % path)
    try:
        return int(out.strip().splitlines()[-1])
    except Exception:
        return 0


def probe_container_bin(c, name):
    """容器内 /app/internal/bin/<name> 是否存在且非空"""
    p = "%s/_probe_%s" % (TMP, name)
    run(c, "rm -f '%s'" % p)
    out, _ = run(c, "%s cp %s:/app/internal/bin/%s '%s' 2>&1" % (D, CONTAINER, name, p))
    sz = size_of(c, p)
    run(c, "rm -f '%s'" % p)
    return sz, out.strip()


def api_probe(c):
    """返回 (streams_code, stream_code)；streams=200 表示已修好"""
    cj = "%s/_cj.txt" % TMP
    run(c, "rm -f '%s'" % cj)
    run(c, "curl -s -c '%s' -X POST -d '{\"password\":\"%s\"}' http://127.0.0.1:8655/auth/login -o /dev/null"
        % (cj, E.JAVBOSS_PASS))
    vid = run(c, "curl -s -b '%s' http://127.0.0.1:8655/videos | tr ',' '\\n' | grep -m1 '\"id\":' | tr -dc '0-9'" % cj)[0].strip()
    if not vid:
        return None, None, None
    s1 = run(c, "curl -s -b '%s' -o /dev/null -w '%%{http_code}' http://127.0.0.1:8655/videos/%s/streams" % (cj, vid))[0].strip()
    s2 = run(c, "curl -s -b '%s' -o /dev/null -w '%%{http_code}' -r 0-2048 http://127.0.0.1:8655/videos/%s/stream" % (cj, vid))[0].strip()
    return vid, s1, s2


def main():
    c = E.connect()
    try:
        run(c, "mkdir -p '%s'" % TMP)
        print("== 1. 现状检测 ==")
        sizes = {}
        for b in BINS:
            sz, raw = probe_container_bin(c, b)
            sizes[b] = sz
            print("  容器 /app/internal/bin/%-8s size=%-10d %s" % (b, sz, "OK" if sz > 1000000 else "缺失!"))
            if sz <= 1000000 and raw:
                print("    raw:", raw[:200])
        vid, s1, s2 = api_probe(c)
        print("  API: video_id=%s  /streams=%s  /stream=%s" % (vid, s1, s2))

        if all(v > 1000000 for v in sizes.values()):
            print("\n[结论] ffprobe/ffmpeg 齐全。若仍不能播放，看容器日志里的 probe playback support error。")
            return 0
        if CHECK_ONLY:
            print("\n[结论] 缺失（见上）。去掉 --check 即可修复。")
            return 1

        print("\n== 2. 从底座镜像 %s 提取 ==" % BASE_IMAGE)
        run(c, "%s rm -f %s >/dev/null 2>&1" % (D, SRC_CT))
        out, err = run(c, "%s create --name %s %s; echo rc=$?" % (D, SRC_CT, BASE_IMAGE))
        print("  create:", (out + err).strip()[:300])
        run(c, "mkdir -p %s/internal/bin" % TMP)
        for b in BINS:
            got = False
            for src in ("/app/internal/bin/" + b, "/usr/local/bin/" + b):
                dst = "%s/internal/bin/%s" % (TMP, b)
                run(c, "rm -f '%s'" % dst)
                run(c, "%s cp %s:%s '%s' 2>&1" % (D, SRC_CT, src, dst))
                sz = size_of(c, dst)
                print("  %-8s <- %-26s size=%d %s" % (b, src, sz, "OK" if sz > 1000000 else ""))
                if sz > 1000000:
                    run(c, "chmod 755 '%s'" % dst)
                    got = True
                    break
            if not got:
                print("  [FAIL] 镜像里找不到 %s，请人工确认底座布局。" % b)
                return 2
        run(c, "%s rm -f %s >/dev/null 2>&1" % (D, SRC_CT))

        print("\n== 3. 补进容器 ==")
        probe_dir = TMP + "/_has_internal"
        run(c, "rm -rf '%s'" % probe_dir)
        out, _ = run(c, "%s cp %s:/app/internal '%s' 2>&1" % (D, CONTAINER, probe_dir))
        exists = size_of(c, probe_dir + "/bin/ffmpeg") > 0
        if exists:
            for b in BINS:
                run(c, "%s cp '%s/internal/bin/%s' %s:/app/internal/bin/%s" % (D, TMP, b, CONTAINER, b))
            print("  /app/internal 已存在 → 逐文件覆盖")
        else:
            run(c, "%s cp '%s/internal' %s:/app/" % (D, TMP, CONTAINER))
            print("  /app/internal 不存在 → 整目录复制（docker cp 会连父目录一起建）")
        for b in BINS:
            sz, _ = probe_container_bin(c, b)
            print("  校验 /app/internal/bin/%-8s size=%d %s" % (b, sz, "OK" if sz > 1000000 else "仍缺失!"))

        print("\n== 4. 重启容器（ResolveFFprobePath 用 sync.Once 缓存，不重启不生效）==")
        run(c, "%s restart %s" % (D, CONTAINER))
        for i in range(15):
            time.sleep(3)
            code = run(c, "curl -s -o /dev/null -w '%%{http_code}' -m 3 http://127.0.0.1:8655/")[0].strip()
            print("  wait %2ds -> / = %s" % ((i + 1) * 3, code))
            if code == "200":
                break

        print("\n== 5. 验证 ==")
        vid, s1, s2 = api_probe(c)
        print("  video_id=%s  /streams=%s  /stream=%s" % (vid, s1, s2))
        if s1 != "200":
            print("  [FAIL] streams 仍非 200，查容器日志：")
            print(run(c, "%s logs --tail 30 %s 2>&1 | grep -i probe" % (D, CONTAINER))[0])
            return 3

        print("\n== 6. 固化：commit -> %s ==" % FIX_TAG)
        out, err = run(c, "%s commit %s %s" % (D, CONTAINER, FIX_TAG), t=900)
        print("  ", (out + err).strip()[:200])
        out, _ = run(c, "%s images --format '{{.Repository}}:{{.Tag}} | {{.Size}}' | grep -i javboss" % D)
        print(out)
        print("\n[完成] 修复已生效并固化。将来重建容器请用 %s（或按手册第 9 节的根治原则换同版本底座）。" % FIX_TAG)
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
