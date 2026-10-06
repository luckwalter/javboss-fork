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

## 故障修复 / 构建脚本（不属流水线，按需单跑）

| 脚本 | 用途 |
|---|---|
| `8_fix_ffprobe.py` | 修「**视频文件或所在目录不存在**」播放故障：**≤ v2.1.2 的历史镜像**中，JavBoss 容器模式把 ffprobe 路径硬编码为 `/app/internal/bin/{ffprobe,ffmpeg}`，底座镜像版本不同就路径失配。`--check` 只检测；默认补齐 → 重启（旧版 `sync.Once` 缓存必须重启） → 验证 `/videos/<id>/streams`=200 → commit 固化。**注：`javboss-fork:2.1.3` 起源码已修复该问题**（候选链回退 + 503 分类 + 失败不缓存），此脚本仅用于维护历史镜像。原理见 `docs/maintenance-zh.md` 第 9 节 |
| `9_verify_playback.py` | 全量验证每个视频都可播（遍历 `/videos` 逐个测 `/videos/<id>/streams`）。**升级/重编镜像/动挂载后必跑**；`--deep` 另抽测 `/stream` 与 m3u8。只依赖标准库，本机直连 NAS HTTP 端口即可 |
| `10_build_fork_image.py` | **改完 Go 源码后编译并构建新镜像**：golang 容器编译 `fork/src` → 生成两行 `Dockerfile.patch`（`FROM <上一版镜像>` + `COPY javboss /app/javboss`）→ `docker build`。底座镜像已自带 ffprobe/ffmpeg，所以只覆盖二进制即可。`--skip-build` 复用已有产物只构建。变量：`BASE_IMAGE` / `NEW_TAG` / `GO_IMAGE`。构建后按 `docs/maintenance-zh.md` §10 重建容器。**注：跟上游大版本升级（前端也变了）时请直接用官方 `Dockerfile` 全量构建，见 11 号脚本说明** |
| `11_deploy_fork_image.py` | **把本地构建好的镜像推到 NAS 并安全重建容器**：`docker save` → SFTP 上传 → NAS `docker load` → 备份 DB → 停容器 → **按需清理 `goose_db_version` 里与上游撞号的残留版本行**（判据：`video.watched_ms` 缺失才算残留，否则保留；fork 迁移用 `2099` 号段，见 `docs/maintenance-zh.md` §5.1）→ 按 `docker inspect` **现读的真实配置**重建容器（不凭记忆写参数）。`--dry-run` 只打印计划，`--skip-push` 复用 NAS 上已有镜像。重建后接 `9_verify_playback.py --deep` |
| `nas_env.py` | 共享配置（`NAS_*` / `FORK` / `DOCKER` / `connect()` / `sh()` / `put_script()`）——新增脚本请复用，别自己拼 paramiko |

## 原则（违反必翻车）

1. **改库先备份再停容器**（步骤 4/7 已内置：`javboss.db.bak-<用途>-<日期>`）。
2. **只填空字段，绝不覆盖已有值**——重跑安全（幂等）。
3. **替换判据用"有效像素"不是文件大小**，详见 `docs/maintenance-zh.md`。
4. **sftp.get 下来的 json 用前查 `getsize()>100`**——远端不存在时会在本地留 0 字节文件。
5. 全部踩坑与升级流程见 `docs/maintenance-zh.md`，动手前先读一遍。
