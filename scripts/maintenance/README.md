# JavBoss 维护工具链（scripts/maintenance）

把"演员资料补全 + 高清头像替换"的全部流程脚本化，凭据走环境变量，产物落 `artifacts/`（已 gitignore）。

## 环境准备

```bash
pip install paramiko          # 唯一依赖（Python 3.9+）
export NAS_HOST='192.168.1.10'      # 你的 QNAP 地址
export NAS_PASS='<QNAP SSH 密码>'   # 必填
# 可选：NAS_USER / NAS_DATA / NAS_BACKUP / PROXY_URL / JAVBOSS_PASS
```

要求：本机与 QNAP SSH(22) 可达；NAS 上正向代理（squid，`PROXY_URL`）可用。

## 流水线（按序执行）

| 步骤 | 脚本 | 作用 | 产物 | 写操作 |
|---|---|---|---|---|
| 0 | `0_snap_db.py` | 拉 DB 一致性快照到本地 | artifacts/javboss.db | 否 |
| 1 | `1_stat_avatar_sizes.py` | 统计现有头像分辨率 | avatar_sizes.json | 否 |
| 2 | `2_crawl_brand_index.py` | 抓 7 家厂牌官网演员索引 | actress_index.json | 否 |
| 3 | `3_download_hd_avatars.py` | 匹配+下载官方艺术照（不动现有文件） | newavatar_result.json | 下载到 /data/_newavatars |
| 4 | `4_apply_hd_avatars.py` | 按有效像素判据替换/新增头像 | hd_apply_log.json | **停容器+写库**，有确认提示 |
| 5 | `5_enrich_avwiki.py` | 抓 av-wiki 生日/身高/三围 | avwiki_full.json | 否 |
| 6 | `6_wiki_zh_names.py` | 维基跨语言链接挖中文名+过滤 | wiki_zh.json / wiki_zh_plan.json | 否 |
| 7 | `7_update_actor_info.py` | 组装更新计划并落库（只填空字段） | — | **APPLY=1 才写库** |

头像链路：0→1→2→3→4；资料链路：0→2→5→6→7。`match_util.py` 是名字归一化/匹配共用库。

## 原则（违反必翻车）

1. **改库先备份再停容器**（步骤 4/7 已内置：`javboss.db.bak-<用途>-<日期>`）。
2. **只填空字段，绝不覆盖已有值**——重跑安全（幂等）。
3. **替换判据用"有效像素"不是文件大小**，详见 `docs/maintenance-zh.md`。
4. **sftp.get 下来的 json 用前查 `getsize()>100`**——远端不存在时会在本地留 0 字节文件。
5. 全部踩坑与升级流程见 `docs/maintenance-zh.md`，动手前先读一遍。
