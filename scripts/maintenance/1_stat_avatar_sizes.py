# -*- coding: utf-8 -*-
"""步骤 1：统计 NAS 上 gfriends_avatars 全部头像的真实分辨率（纯 struct 解析，无需 PIL）。

产物：artifacts/avatar_sizes.json  →  步骤 3 生成替换计划时用它判断"值不值得换"。
"""
import os

from nas_env import ART, DATA, WORK, connect, docker_python, put_script, sh

STAT = r'''# -*- coding: utf-8 -*-
import json, os, struct

SRC = "/data/gfriends_avatars"
out = {}
bad = []


def size(fp):
    with open(fp, "rb") as f:
        b = f.read(300000)
    if b[:2] != b"\xff\xd8":
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", b[16:24])
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            return ("WEBP", len(b))
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


files = [f for f in os.listdir(SRC) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
for f in files:
    key = os.path.splitext(f)[0]
    s = size(os.path.join(SRC, f))
    if s and isinstance(s[0], int):
        out[key] = [s[0], s[1], os.path.getsize(os.path.join(SRC, f))]
    else:
        bad.append(f)

with open("/data/_probe/avatar_sizes.json", "w", encoding="utf-8") as fp:
    json.dump(out, fp)

short = sorted(min(v[0], v[1]) for v in out.values())
n = len(short)
print(f"总数 {len(files)}  解析成功 {n}  失败 {len(bad)} {bad[:5]}")
if n:
    print(f"短边 min={short[0]} p25={short[n//4]} 中位={short[n//2]} p75={short[3*n//4]} max={short[-1]}")
    buckets = {"<300": 0, "300-500": 0, "500-700": 0, "700-1000": 0, ">=1000": 0}
    for s in short:
        if s < 300: buckets["<300"] += 1
        elif s < 500: buckets["300-500"] += 1
        elif s < 700: buckets["500-700"] += 1
        elif s < 1000: buckets["700-1000"] += 1
        else: buckets[">=1000"] += 1
    print("短边分档:", buckets)
'''


def main():
    os.makedirs(ART, exist_ok=True)
    cli = connect()
    sh(cli, f"mkdir -p {WORK}")
    sftp = cli.open_sftp()
    put_script(sftp, STAT, f"{WORK}/stat_av.py")
    sftp.close()

    print("=== 统计中（NAS 容器内跑）===")
    out, err = docker_python(cli, "/data/_probe/stat_av.py", t=900, network=False)
    print(out)
    if err.strip():
        print("stderr:", err[-500:])

    sftp = cli.open_sftp()
    dst = os.path.join(ART, "avatar_sizes.json")
    sftp.get(f"{WORK}/avatar_sizes.json", dst)
    sftp.close()
    cli.close()
    # 坑：sftp.get 远端文件不存在时会在本地留 0 字节文件——用前必须校验大小
    print(f"已保存 {dst}  {os.path.getsize(dst) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
