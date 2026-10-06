# -*- coding: utf-8 -*-
"""步骤 4：执行头像替换（写操作！会停容器）。

流程：备份 DB → 停容器 → 容器内替换/新增 + 更新 DB → 启容器 → 备份归档 → HTTP 复核。
判据：新图有效像素 ≥ 旧图 1.15 倍才替换（现有图更大则保留，绝不降级）。
新增头像（原无）写 avatar_code='HD-<id>' + avatar_file。
"""
import json
import os
import time

from nas_env import ART, BKROOT, DATA, JAVBOSS_PASS, WORK, connect, docker_python, put_json, put_script, sh

RATIO = 0.6989  # 前端裁切窗宽高比：(800*IDOL_COVER_VISIBLE_RATIO)/538


def eff(w, h):
    """前端实际利用的像素量：图按高度撑满、横向裁切到 RATIO，横图约一半宽度被浪费。"""
    return h * min(w, h * RATIO)


APPLY = r'''# -*- coding: utf-8 -*-
import json, os, shutil, sqlite3, datetime

DATA = "/data"
NEWDIR = "/data/_newavatars"
AVDIR = "/data/gfriends_avatars"
BK = "/data/_avatar_backup_hd"
plan = json.load(open("/data/_probe/apply_plan.json", encoding="utf-8"))
os.makedirs(BK, exist_ok=True)

log = {"time": datetime.datetime.now().isoformat(timespec="seconds"),
       "replaced": [], "added": [], "missing": [], "db_updated": 0}

# 1) 备份将被覆盖的原图
for p in plan["replace"]:
    src = f"{AVDIR}/{p['id']}.jpg"
    if os.path.exists(src):
        shutil.copy2(src, f"{BK}/{p['id']}.jpg")
print(f"已备份原图 {len([f for f in os.listdir(BK)])} 张 -> {BK}")

# 2) 覆盖替换
for p in plan["replace"]:
    src, dst = f"{NEWDIR}/{p['id']}.jpg", f"{AVDIR}/{p['id']}.jpg"
    if not os.path.exists(src):
        log["missing"].append(p["id"])
        continue
    shutil.copy2(src, dst)
    log["replaced"].append({"id": p["id"], "name": p["name"], "old": p["old"], "new": p["new"], "site": p["site"]})

# 3) 新增（原本无头像）：拷文件 + 写 DB
con = sqlite3.connect(f"{DATA}/javboss.db")
cur = con.cursor()
for p in plan["newonly"]:
    src, dst = f"{NEWDIR}/{p['id']}.jpg", f"{AVDIR}/{p['id']}.jpg"
    if not os.path.exists(src):
        log["missing"].append(p["id"])
        continue
    shutil.copy2(src, dst)
    cur.execute("UPDATE jav_idol SET avatar_code=?, avatar_file=?, updated_at=datetime('now') WHERE id=?",
                (f"HD-{p['id']}", f"/app/data/gfriends_avatars/{p['id']}.jpg", p["id"]))
    log["added"].append({"id": p["id"], "name": p["name"], "new": p["new"], "site": p["site"]})
con.commit()
log["db_updated"] = len(log["added"])

n_av = cur.execute("SELECT COUNT(*) FROM jav_idol WHERE avatar_file IS NOT NULL AND TRIM(avatar_file)<>''").fetchone()[0]
n_hd = cur.execute("SELECT COUNT(*) FROM jav_idol WHERE avatar_code LIKE 'HD-%'").fetchone()[0]
con.close()
log["avatar_file_total"] = n_av
log["hd_added_total"] = n_hd

import struct


def jsize(fp):
    with open(fp, "rb") as f:
        b = f.read(200000)
    if b[:2] != b"\xff\xd8":
        return None
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


verify = []
for p in plan["replace"][:5] + plan["newonly"][:3]:
    s = jsize(f"{AVDIR}/{p['id']}.jpg")
    verify.append({"id": p["id"], "name": p["name"], "now": s, "expect": p["new"]})
log["verify"] = verify

json.dump(log, open("/data/_probe/hd_apply_log.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"替换 {len(log['replaced'])}，新增 {len(log['added'])}，缺失 {len(log['missing'])}")
print(f"DB: avatar_file 总数={n_av}，其中 HD- 前缀={n_hd}")
for v in verify:
    print(f"  抽检 {v['name']:<14} 现={v['now']} 期望={v['expect']}")
'''


def main():
    res = [r for r in json.load(open(os.path.join(ART, "newavatar_result.json"), encoding="utf-8")) if r["ok"]]
    replace, newonly, skip = [], [], []
    for r in res:
        nw, nh = r["new"]
        if not r["old"]:
            newonly.append({"id": r["id"], "name": r["name"], "new": r["new"], "site": r["site"]})
            continue
        ow, oh = r["old"][0], r["old"][1]
        ratio = eff(nw, nh) / eff(ow, oh) if eff(ow, oh) else 99
        rec = {"id": r["id"], "name": r["name"], "old": [ow, oh], "new": [nw, nh],
               "site": r["site"], "ratio": round(ratio, 2)}
        (replace if ratio >= 1.15 else skip).append(rec)

    plan = {"replace": replace, "newonly": newonly, "skip": skip}
    print(f"计划：替换 {len(replace)}，新增 {len(newonly)}，保留 {len(skip)}")
    if input("确认执行？(yes/N) ").strip().lower() != "yes":
        print("已取消")
        return

    ts = time.strftime("%Y%m%d")
    cli = connect()
    sftp = cli.open_sftp()
    put_json(sftp, plan, f"{WORK}/apply_plan.json")
    put_script(sftp, APPLY, f"{WORK}/apply.py")
    sftp.close()

    print("\n=== 1) 备份 DB + 停容器 ===")
    out, err = sh(cli, f"cd {DATA} && cp javboss.db javboss.db.bak-hd-{ts} && ls -la javboss.db.bak-hd-{ts}")
    print(out.strip(), err.strip())
    out, err = sh(cli, f"{DOCKER} stop javboss 2>&1 | tail -1")
    print(out)

    print("=== 2) 执行替换（alpine + python3）===")
    out, err = docker_python(cli, "/data/_probe/apply.py", t=1200, network=False)
    print(out)
    if err.strip():
        print("--- stderr ---\n", err[-800:])

    print("=== 3) 启容器 ===")
    out, err = sh(cli, f"{DOCKER} start javboss && sleep 6 && {DOCKER} ps --format '{{{{.Names}}}} {{{{.Status}}}}'")
    print(out, err)

    print("=== 4) 备份归档到 Backup 目录 ===")
    out, err = sh(cli, f"mkdir -p {BKROOT} && mv {DATA}/_avatar_backup_hd {BKROOT}/avatars-before-hd-{ts} && "
                       f"cp {DATA}/_probe/hd_apply_log.json {BKROOT}/hd_apply_log-{ts}.json && "
                       f"du -sh {BKROOT}/avatars-before-hd-{ts}")
    print(out, err)

    print("=== 5) HTTP 复核 ===")
    out, err = sh(cli, f"curl -s -c /tmp/cj -X POST -d '{{\"password\":\"{JAVBOSS_PASS}\"}}' "
                       f"http://127.0.0.1:8655/auth/login >/dev/null")
    for code in ("GFAV-1", "GFAV-40", "MIUM-1372", "/jav/idols"):
        path = f"/jav/{code}/cover" if not code.startswith("/") else code
        out, _ = sh(cli, f"curl -s -b /tmp/cj -o /dev/null -w '{code}: %{{http_code}} %{{size_download}}\\n' "
                         f"http://127.0.0.1:8655{path}")
        print(out.strip())

    sftp = cli.open_sftp()
    sftp.get(f"{WORK}/hd_apply_log.json", os.path.join(ART, "hd_apply_log.json"))
    sftp.close()
    cli.close()
    print(f"完成。原图备份：{BKROOT}/avatars-before-hd-{ts}/")


if __name__ == "__main__":
    main()
