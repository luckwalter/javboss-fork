#!/bin/sh
# JavBoss NAS 维护：清理全盘 .@__thumb 缩略图缓存（QNAP File Station 生成）
#
# 背景：
#   QNAP File Station 会为浏览过的图片/视频生成 .@__thumb 缩略图缓存目录。
#   这些目录若被 JavBoss 目录扫描命中，会被误判为正片；且会持续累积占用空间。
#   根治需在 File Station 设置里关闭“生成多媒体缩略图”（GUI 操作），
#   本脚本作为每日兜底清理。
#
# 部署：
#   放到 <javboss_data>/clean_thumbs.sh，由 cron 每日调用（部署脚本见 13_install_nas_cron.py）。
#   也可手动传参指定 data 目录：sh 12_clean_thumbs.sh /your/data/dir
#
# 安全：
#   排除 JavBoss 自身 data 目录（*/Container/javboss/*），避免误伤其封面/截图缓存。

DATA_DIR="${1:-/share/CACHEDEV1_DATA/Container/javboss/data}"
LOG="$DATA_DIR/clean_thumbs.log"
LIST="$DATA_DIR/clean_list.txt"

echo "$(date '+%Y-%m-%d %H:%M:%S') clean start" >> "$LOG"
find /share -type d -name '.@__thumb' ! -path '*/Container/javboss/*' 2>/dev/null > "$LIST"
CNT=$(wc -l < "$LIST")
while IFS= read -r d; do
  rm -rf "$d"
done < "$LIST"
echo "$(date '+%Y-%m-%d %H:%M:%S') removed $CNT dirs" >> "$LOG"
