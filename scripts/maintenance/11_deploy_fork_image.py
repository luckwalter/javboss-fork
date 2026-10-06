#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11_deploy_fork_image.py —— 把本地构建好的 fork 镜像推到 NAS 并安全重建容器。

为什么需要它：fork 镜像只在本地构建（不推 registry），而「推到 NAS 并重建」有
四个必须对齐、漏一个就出事的环节：

  1) docker save/load 传镜像（NAS 上不重新编译）
  2) 改库前先备份 + 先停容器（防 sqlite WAL 竞态）
  3) 清理 goose_db_version 里与上游撞号的历史版本行
     —— fork 迁移用 2099 保留号段（见 migrations.go 里 WithAllowMissing 的注释）。
        但 ≤2.1.3 的旧库里，fork 的头像迁移当时注册成 202610040001，与上游
        #375 的 202610040001_add_watched_time 撞号；那一行会让 goose 把上游迁移
        误判为「已执行」而跳过，watched_ms 列建不出来，上游 watch-time 功能直接报
        SQL 错误。本脚本会在**确认 watched_ms 确实缺失**时才删掉该行，
        删完由 goose 的 allowMissing 自动补跑（迁移本身幂等）。
  4) 用「当前运行容器的实际配置」重建（不凭记忆写参数），再全量验证播放

设计原则：**重建参数一律从 `docker inspect` 现读**，只有 image 换成新的。
这样永远不会因为漏了某个环境变量而踩 `docs/maintenance-zh.md` §10 的坑。

环境变量（同 nas_env.py）：
  NAS_PASS    必填
  NAS_HOST / NAS_USER / NAS_DATA / NAS_BACKUP

用法：
  export NAS_PASS=xxxx
  python3 11_deploy_fork_image.py --image javboss-fork:2.2.1
  python3 11_deploy_fork_image.py --image javboss-fork:2.2.1 --dry-run

重建完成后请跑：python3 scripts/maintenance/9_verify_playback.py --deep
"""
import argparse
import json
import os
import posixpath
import sys
import time

import nas_env

# fork 早期迁移误用过、与上游撞号的版本号。
# 只有当它对应的列（watched_ms）确实缺失时，这一行才是「历史残留」而非「真已执行」。
COLLIDED_VERSION = 202610040001
COLLIDED_SENTINEL = ("video", "watched_ms")  # 上游 #375 该迁移应建出的列


def fix_goose_versions(cli, db_path, stale, dry):
    """按需清理与上游撞号的历史版本行。

    判据（自我判定，不用人肉记）：
      · `video.watched_ms` **缺失** → goose_db_version 里的 202610040001 是
        ≤2.1.3 时代 fork 头像迁移留下的残留行，删掉它，让上游同名迁移能跑；
      · `video.watched_ms` **存在** → 该行是上游迁移的真实执行记录，动它没意义。

    做法：停容器后把 db（含 -wal/-shm）拉到本地，用本机 sqlite3 改，再传回。
    为什么不直接在 NAS 上改：`nas_env.docker_python` 走的是
    `alpine + apk add python3`，**apk 失败时会被 `&&` 短路且输出被吞**，
    看起来成功其实什么都没干（2026-10-06 真实踩过：watched_ms 列没建出来）。
    本地改库不依赖 NAS 联网，失败一定会抛异常，更可靠。
    """
    import shutil
    import sqlite3
    import tempfile

    tmp = tempfile.mkdtemp(prefix="goosefix_")
    local = os.path.join(tmp, "javboss.db")
    sftp = cli.open_sftp()
    for suf in ("", "-wal", "-shm"):
        try:
            sftp.get(db_path + suf, local + suf)
        except IOError:
            pass
    sftp.close()

    db = sqlite3.connect(local)
    try:
        cur = db.cursor()
        has = cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='goose_db_version'").fetchone()
        if not has:
            print("    没有 goose_db_version 表，跳过")
            return

        # --- 判据：上游 #375 该建出的列在不在 ---
        table, column = COLLIDED_SENTINEL
        cols = [r[1] for r in cur.execute("PRAGMA table_info(%s)" % table).fetchall()]
        row_exists = cur.execute("SELECT 1 FROM goose_db_version WHERE version_id=?",
                                 (COLLIDED_VERSION,)).fetchone() is not None
        print("    判据: %s.%s %s / goose_db_version 含 %d %s"
              % (table, column, "存在" if column in cols else "缺失",
                 COLLIDED_VERSION, "有" if row_exists else "无"))

        if not row_exists:
            print("    无撞号残留行，跳过")
        elif column in cols:
            print("    列已存在 ⇒ 该行是真实执行记录，保留（不删）")
        else:
            before = [r[0] for r in cur.execute(
                "SELECT version_id FROM goose_db_version ORDER BY version_id")]
            cur.execute("DELETE FROM goose_db_version WHERE version_id=?", (COLLIDED_VERSION,))
            removed = cur.rowcount
            db.commit()
            after = [r[0] for r in cur.execute(
                "SELECT version_id FROM goose_db_version ORDER BY version_id")]
            print("    %s.%s 缺失 ⇒ 删除残留行 %d 条" % (table, column, removed))
            print("    改前尾部: %s" % before[-4:])
            print("    改后尾部: %s" % after[-4:])

        # 把 WAL 内容并回主库并截断，这样只需上传 .db 一个文件
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.commit()
    finally:
        db.close()

    if dry:
        shutil.rmtree(tmp, ignore_errors=True)
        return

    sftp = cli.open_sftp()
    sftp.put(local, db_path)
    sftp.close()
    # 旧的 WAL/SHM 必须清掉，否则与新主库不一致
    nas_env.sh(cli, "rm -f %s-wal %s-shm" % (db_path, db_path))
    shutil.rmtree(tmp, ignore_errors=True)


def die(msg):
    sys.exit("[!] " + msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="要部署的本地镜像 tag，例如 javboss-fork:2.2.1")
    ap.add_argument("--container", default="javboss", help="容器名（默认 javboss）")
    ap.add_argument("--keep-tar", action="store_true", help="保留 NAS 上的镜像 tar（默认保留）")
    ap.add_argument("--skip-goose-fix", action="store_true", help="跳过 goose 版本行清理（不推荐）")
    ap.add_argument("--skip-push", action="store_true",
                    help="跳过 save/上传/load（镜像已在 NAS 上时用，可省几分钟）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    args = ap.parse_args()

    D = nas_env.DOCKER
    DATA = nas_env.DATA
    BK = nas_env.BKROOT
    IMG = args.image
    C = args.container
    db_path = posixpath.join(DATA, "javboss.db")
    stamp = time.strftime("%Y%m%d-%H%M%S")

    print("=" * 68)
    print("镜像     : %s" % IMG)
    print("容器     : %s" % C)
    print("数据目录 : %s" % DATA)
    print("备份目录 : %s" % BK)
    print("模式     : %s" % ("DRY-RUN" if args.dry_run else "实际执行"))
    print("=" * 68)

    # ---------- 0) 本地前置检查 ----------
    import subprocess
    rc = subprocess.run(["docker", "image", "inspect", IMG],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        die("本地没有镜像 %s，请先跑 10_build_fork_image.py 或 docker build" % IMG)
    size = json.loads(rc.stdout)[0].get("Size", 0)
    print("[0] 本地镜像存在，%.0f MB" % (size / 1024 / 1024))

    cli = nas_env.connect()

    # ---------- 1) 读当前容器真实配置 ----------
    out, err = nas_env.sh(cli, "%s inspect %s" % (D, C))
    if not out.strip().startswith("["):
        die("读取现有容器配置失败：%s" % (err or out)[:300])
    insp = json.loads(out)[0]
    cfg, hc = insp["Config"], insp["HostConfig"]
    old_image = cfg["Image"]
    envs = [e for e in (cfg.get("Env") or []) if not e.startswith("PATH=")]
    binds = hc.get("Binds") or []
    network = hc.get("NetworkMode") or "default"
    restart = (hc.get("RestartPolicy") or {}).get("Name") or "no"
    cmd = cfg.get("Cmd") or []
    print("[1] 当前容器镜像: %s" % old_image)
    print("    环境变量 %d 个 / 挂载 %d 条 / 网络 %s / 重启策略 %s" % (len(envs), len(binds), network, restart))

    # 新版镜像把 ffprobe/ffmpeg 放在 /app/internal/bin（官方 Dockerfile 布局），
    # 旧容器里可能还写着 /usr/local/bin（v2.1.0 底座布局）。这里顺手纠正，
    # 避免依赖代码里的候选链兜底（能兜住，但显式正确更好）。
    fixed_envs = []
    for e in envs:
        if e.startswith("FFPROBE_PATH=/usr/local/bin"):
            e = "FFPROBE_PATH=/app/internal/bin/ffprobe"
        elif e.startswith("FFMPEG_PATH=/usr/local/bin"):
            e = "FFMPEG_PATH=/app/internal/bin/ffmpeg"
        fixed_envs.append(e)
    envs = fixed_envs

    # ---------- 2) docker save + 上传 ----------
    # 用 tempfile.gettempdir()：Windows 上写死 /tmp 会被 Docker Desktop 与 Python 解释成不同路径
    import tempfile
    tar_local = os.path.join(tempfile.gettempdir(),
                             "%s-%s.tar" % (IMG.replace(":", "-").replace("/", "_"), stamp))
    tar_remote = posixpath.join(BK, os.path.basename(tar_local))
    if args.skip_push:
        print("[2][3] 已跳过 save/上传/load（--skip-push），假定 NAS 上已有 %s" % IMG)
    else:
        print("[2] docker save -> %s（约 %.0f MB 未压缩）" % (tar_local, size / 1024 / 1024))
        if not args.dry_run:
            r = subprocess.run(["docker", "save", "-o", tar_local, IMG], capture_output=True, text=True)
            if r.returncode != 0:
                die("docker save 失败: %s" % r.stderr[:300])
            sftp = cli.open_sftp()
            t0 = time.time()
            sftp.put(tar_local, tar_remote)
            sftp.close()
            print("    上传完成 -> %s（%.0fs）" % (tar_remote, time.time() - t0))

        # ---------- 3) NAS docker load ----------
        print("[3] NAS 上 docker load")
        if not args.dry_run:
            o, e = nas_env.sh(cli, "%s load -i %s" % (D, tar_remote), t=900)
            print("    " + (o.strip().splitlines() or ["?"])[-1])
            if e.strip():
                print("    ERR: " + e.strip()[:200])

    # ---------- 3b) 确认 NAS 上镜像就绪 ----------
    if not args.dry_run:
        o, _ = nas_env.sh(cli, "%s image inspect %s --format '{{.Id}} {{.Size}}' | head -1" % (D, IMG))
        if not o.strip():
            die("NAS 上找不到镜像 %s（去掉 --skip-push 重跑）" % IMG)
        print("[3b] NAS 上镜像已就绪: %s" % o.strip())
        if not args.keep_tar and not args.skip_push:
            nas_env.sh(cli, "rm -f %s" % tar_remote)

    # ---------- 4) 停容器 + 删容器 ----------
    # 必须【先停容器再备份】：WAL 模式下未并回主库的写入不在 javboss.db 里，
    # 容器运行时直接 cp 出来的备份可能缺数据。
    print("[4] 停并删除旧容器（服务中断约 15 秒）")
    if not args.dry_run:
        nas_env.sh(cli, "%s stop %s && %s rm %s" % (D, C, D, C))

    # ---------- 5) 备份 DB ----------
    bak = posixpath.join(DATA, "javboss.db.bak-upgrade-%s" % stamp)
    print("[5] 备份 DB -> %s" % bak)
    if not args.dry_run:
        o, e = nas_env.sh(cli, "cp -p %s %s && ls -l %s" % (db_path, bak, bak))
        print("    " + (o.strip() or e.strip())[:160])

    # ---------- 6) 按需清理 goose 撞号版本行 ----------
    if args.skip_goose_fix:
        print("[6] 已跳过 goose 版本行清理（--skip-goose-fix）")
    else:
        print("[6] 检查 goose_db_version 中与上游撞号的历史行（判据见 fix_goose_versions 注释）")
        fix_goose_versions(cli, db_path, COLLIDED_VERSION, args.dry_run)

    # ---------- 7) 用新镜像 + 原配置重建 ----------
    parts = ["%s run -d --name %s" % (D, C)]
    if network == "host":
        parts.append("--network host")
    else:
        parts.append("--network %s" % network)
    parts.append("--restart %s" % restart)
    for e in envs:
        parts.append("-e '%s'" % e)
    for b in binds:
        # Binds 条目本身已含 :ro 之类后缀，原样透传
        parts.append("-v '%s'" % b)
    parts.append(IMG)
    for a in cmd:
        parts.append(a)
    run_cmd = " ".join(parts)
    print("[7] 重建容器：")
    print("    " + run_cmd)
    if not args.dry_run:
        o, e = nas_env.sh(cli, run_cmd)
        print("    %s" % (o.strip() or e.strip())[:160])
        time.sleep(6)
        o, _ = nas_env.sh(cli, "%s ps --filter name=%s --format '{{.Image}} | {{.Status}}'" % (D, C))
        print("    当前状态: %s" % o.strip())

    cli.close()
    print()
    print("完成。请接着跑：")
    print("  NAS_HOST=%s python3 scripts/maintenance/9_verify_playback.py --deep" % nas_env.NAS_HOST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
