# -*- coding: utf-8 -*-
"""
JavBoss NAS 维护：部署/重装每日 cron（抗 QNAP 对 /etc/config/crontab 的重写）

QNAP 会在系统事件（qpkg 启停、设置变更等）时整份重写 /etc/config/crontab，
清掉用户裸 crontab 任务。本脚本用两层保证持久化：
  1) 在 /etc/config/autorun.sh 注册开机保活（开机即把两任务写回 crontab 并 HUP crond）；
  2) 幂等追加两行 cron：
       30 4 * * *  clean_thumbs.sh      （每日清理 .@__thumb）
       40 4 * * *  fix_sample_thumbs.sh （样品图自愈，见 11/手册）

安全：密码通过环境变量 NAS_PASS 传入，切勿硬编码提交；NAS_HOST 也走环境变量。

用法：
  NAS_HOST=192.168.2.254 NAS_USER=admin NAS_PASS='***' \
  DATA_DIR=/share/CACHEDEV1_DATA/Container/javboss/data \
  python3 13_install_nas_cron.py
"""
import os
import sys
import paramiko

HOST = os.environ.get("NAS_HOST")
USER = os.environ.get("NAS_USER", "admin")
PASS = os.environ.get("NAS_PASS")
DATA = os.environ.get("DATA_DIR", "/share/CACHEDEV1_DATA/Container/javboss/data")

if not HOST or not PASS:
    sys.exit("缺少环境变量：NAS_HOST / NAS_PASS（请勿硬编码密码）")

CLEAN_SH = """#!/bin/sh
LOG=__DATA__/clean_thumbs.log
LIST=__DATA__/clean_list.txt
echo "$(date '+%Y-%m-%d %H:%M:%S') clean start" >> "$LOG"
find /share -type d -name '.@__thumb' ! -path '*/Container/javboss/*' 2>/dev/null > "$LIST"
CNT=$(wc -l < "$LIST")
while IFS= read -r d; do
  rm -rf "$d"
done < "$LIST"
echo "$(date '+%Y-%m-%d %H:%M:%S') removed $CNT dirs" >> "$LOG"
"""

AUTORUN_SH = """#!/bin/sh
# QNAP 开机保活：确保 JavBoss 维护 cron 存在（对抗 QNAP 对 /etc/config/crontab 的重写）
D=__DATA__
L1="30 4 * * * sh $D/clean_thumbs.sh >> $D/clean.log 2>&1"
L2="40 4 * * * sh $D/fix_sample_thumbs.sh >> $D/fix.log 2>&1"
T=/tmp/javboss_cron.new
crontab -l 2>/dev/null > "$T"
grep -qF "$L1" "$T" || echo "$L1" >> "$T"
grep -qF "$L2" "$T" || echo "$L2" >> "$T"
crontab "$T"
rm -f "$T"
kill -HUP "$(pidof crond)" 2>/dev/null
"""

clean = CLEAN_SH.replace("__DATA__", DATA)
autorun = AUTORUN_SH.replace("__DATA__", DATA)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS, timeout=30)

sftp = ssh.open_sftp()
sftp.open(DATA + "/clean_thumbs.sh", "w").write(clean)
sftp.open("/etc/config/autorun.sh", "w").write(autorun)
sftp.close()

i, o, e = ssh.exec_command(
    "chmod 755 /etc/config/autorun.sh " + DATA + "/clean_thumbs.sh; "
    "sh /etc/config/autorun.sh")
print(o.read().decode())
print("ERR:", e.read().decode()[:300])
ssh.close()
print("DONE: 已部署 clean_thumbs.sh + autorun.sh 保活，并注册每日 cron")
