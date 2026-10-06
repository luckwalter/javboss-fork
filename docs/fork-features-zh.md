# JavBoss Fork 功能详解

> 本文档说明**本分支相对上游到底多了什么、为什么这么做、怎么验证**。
> 面向接手人 / 使用者。踩坑与排障细节在 [`maintenance-zh.md`](maintenance-zh.md)，本文不重复。

---

## 0. 定位与边界

本仓库是 `Solr159/JavBoss` 的个人维护分支。**上游是本体**，本分支只做四件事：

| 类别 | 内容 |
|---|---|
| **加一个能力** | 女优头像 = 独立高清人像（上游没有这个概念） |
| **修两个缺陷** | ① 容器模式工具路径硬编码 ② 播放错误误分类 / 死代码 / 永久缓存 |
| **加固一处升级路径** | goose 迁移入口加 `WithAllowMissing()`，让 fork 的 `2099` 迁移号段不阻塞上游新迁移 |
| **带一套工具** | `scripts/maintenance/`（12 脚本）+ 两份文档 |

**改动量**：Go 代码 **10 个文件 / +293 −48 行**，占上游 68,321 行的 **0.4%**；前端 `web/` **零改动**。
已跟进的上游基线 = **`main` @ `5aa89f3`**（含 #375/#376/#377/#379 四个未发布提交）。

**刻意保持小改动面**的原因：让「跟官方升级」永远是一次 `git merge` 就能完成的事（见 §7）。

---

## 1. 女优独立高清头像

### 1.1 上游的行为，以及它为什么算问题

上游 `jav_idol` 表**没有头像列**。女优列表里显示的那张脸，是**随机挑一部该女优的作品，把封面裁一块**得到的——具体是哪一部取决于查询返回的顺序。

由此带来三个可见问题：构图随机（人脸可能在角落）、清晰度取决于封面、同一女优在不同页面可能换脸。

### 1.2 数据结构

新增列（goose 迁移 `internal/db/migrations/209901010001_add_jav_idol_avatar.go`）：

| 列 | 含义 | 示例 |
|---|---|---|
| `avatar_code` | 虚拟番号码，用于复用封面路由 | `GFAV-1234` / `HD-1234` |
| `avatar_file` | 头像文件在**容器内**的绝对路径 | `/app/data/gfriends_avatars/1234.jpg` |

`avatar_code` 上建了索引。GORM 侧仅新增两个可空字段（`internal/models/jav.go`）。

### 1.3 请求链路（零新增路由）

```
GET /jav/GFAV-1234/cover
      │
      ▼
getJavCover(code)
      │
      ├─ lookupIdolAvatarFile(code)  ← 先查：code 形如 GFAV-/HD- 时反查 avatar_file
      │     ├─ 命中且文件存在 → c.File(头像文件)   ← 独立头像优先，直接返回
      │     └─ 未命中 ↓
      │
      └─ FindCoverPath(code)          ← 原逻辑：按作品番号找封面
```

**关键点**：路线复用 `/jav/:code/cover`，前端不需要知道「这张图是头像还是封面」——所以**前端一行都不用改**。

反向的保护由 `hasIdolAvatarFile()` 提供：作品刮削时若发现该女优已有独立头像，**早退**，避免 Gfriends 头像被作品封面覆盖回填。这一步是必须的，否则下次刮削就会把 `cover_code` 写回真实番号。

### 1.4 头像素材与替换判据

素材来自 [Gfriends](https://github.com/gfriends/gfriends)（1254 张）+ 厂牌官网高清艺术照（新增 34 张）。落地在容器数据目录 `data/gfriends_avatars/<idol_id>.jpg`，约 170 MB。

**判据不是文件大小，而是「有效像素」**——因为前端 `JavIdolGrid.jsx` 是「图按高度铺满（`h-full`）+ 横向裁切」，`IDOL_COVER_VISIBLE_RATIO = 0.47`，裁切窗宽高比：

```
frame_aspect = (800 × 0.47) / 538 ≈ 0.6989

有效像素  eff(w, h) = h × min(w, h × 0.6989)
```

含义：**横图有一半宽度会被裁掉**。例如 1200×741 的横图 `eff ≈ 518k`，反而不如 702×900 的竖图 `eff ≈ 629k`。

替换规则：

```
eff(new) / eff(old) ≥ 1.15   → 替换
eff(new) / eff(old) ≤ 0.87   → 保留（绝不降级）
```

裁切偏移（Gfriends 素材人像偏左时需手工给偏移）：

```
crop_left = (A − 0.6989) / 2          # A = 图片宽高比
```

### 1.5 数据现状

| 指标 | 值 |
|---|---|
| 演员总数 | 1456 |
| 已有独立头像 | **1288**（Gfriends 1254 + 厂牌艺术照新增 34） |
| 其中由 Gfriends 升级为厂牌艺术照 | 392 |
| 日文名 / 罗马名 / 中文名 覆盖 | 100% / 1015 (70%) / 391 (27%) |
| 生日 / 身高三围 覆盖 | 1306 (90%) / ~1145 (79%) |
| 无图可补 | 约 620 人（素人 / 已封禁厂牌） |

### 1.6 怎么验证

```bash
# 独立头像（走 GFAV 虚拟码）→ 应 200，且大小与作品封面不同
curl -c cj -X POST -d '{"password":"admin"}' http://<host>:8655/auth/login
curl -b cj -o /dev/null -w '%{http_code} %{size_download}\n' http://<host>:8655/jav/GFAV-1/cover

# 作品封面（走真实番号）→ 应 200，走的是原逻辑
curl -b cj -o /dev/null -w '%{http_code} %{size_download}\n' http://<host>:8655/jav/MIUM-1372/cover

# 女优列表 → cover_code 应为 GFAV-n 而非作品番号
curl -b cj http://<host>:8655/jav/idols
```

> 注意：登录接口**返回 cookie，不返回 token**，所有请求都要带 `-b cj`，否则 401。

---

## 2. 上游缺陷修复

### 2.1 容器模式工具查找（[issue #1](https://github.com/luckwalter/javboss-fork/issues/1)）

**根因**：`internal/util/video.go` 中 `ContainerFFBinaryDir = "/app/internal/bin"`，容器模式下**只尝试这一个绝对路径**，且**完全忽略** `FFPROBE_PATH` / `FFMPEG_PATH` 环境变量（上游测试 `TestDockerFFBinaryLookupOnlyUsesFixedImagePath` 明确断言了这一点——说明「不依赖宿主机 PATH」是**有意设计**，只是没考虑镜像布局会变）。

而上游镜像布局恰好变过：

| 镜像版本 | ffprobe 位置 | 镜像里声明的 ENV |
|---|---|---|
| 早期（v2.1.0 等） | `/usr/local/bin/ffprobe` | `FFPROBE_PATH=/usr/local/bin/ffprobe` |
| v2.1.1 起 | `/app/internal/bin/ffprobe` | （已移除该 ENV） |

**只要「镜像底座版本 ≠ 二进制源码版本」，就必然找不到工具，且报错完全不指向真实原因。**

**修复**：改为**有优先级的候选链**，保留原始设计意图（不依赖系统 PATH）的同时容忍布局差异：

```
env(FFPROBE_PATH / FFMPEG_PATH)
  → /app/internal/bin/<name>          # 上游当前布局
  → /usr/local/bin/<name>             # 上游历史布局
  → <name>                            # 兜底走 PATH
```

全部失败时返回哨兵错误 `util.ErrFFToolMissing`（不再靠字符串匹配判断）。

### 2.2 播放错误分类（[issue #2](https://github.com/luckwalter/javboss-fork/issues/2)）

三个缺陷叠加，效果是「错误信息把人带到完全错误的方向 + 修好了也不生效」：

| # | 缺陷 | 修复 |
|---|---|---|
| A | `respondPlaybackError` 的 switch 里 `errors.Is(err, os.ErrNotExist)` **排在工具缺失分支之前**。工具缺失底层就是 `stat` 失败 → `fs.ErrNotExist` → 被命中 → 误报 **404「视频文件或所在目录不存在」** | 新增哨兵 `util.ErrFFToolMissing`，把工具缺失判定**提到最前**，改报 **503「缺少浏览器播放所需组件」** |
| B | 判据 `strings.Contains(err, "ffprobe not found")` 是**死代码**——实际消息是 `"ffprobe unavailable in Docker image at ..."`，两串零重叠 | 判据对齐；有了哨兵错误后不再依赖脆弱字符串 |
| C | `ResolveFFprobePath` 用 `sync.Once` **缓存失败结果** → 按提示补好工具后不重启进程仍 404 | 改为**仅成功时缓存**，失败下次重试（补齐即可恢复，无需重启） |

**修复后行为对照**：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 工具存在 | 200 | 200 |
| 工具不存在（布局不匹配） | **404 视频文件或所在目录不存在**（误导） | **503 缺少浏览器播放所需组件**（准确） |
| 工具不存在 + 补齐工具（不重启） | 仍 404 | **自动恢复 200** |
| 媒体文件真的不存在 | 404 | 404（正确，未变） |

> 修复提交 `ad880f4`（源码）+ `6a1b216`（文档同步），镜像 **`javboss-fork:2.1.3`** 起生效，并随 **`2.2.0`** 延续。
> ≤ v2.1.2 的历史镜像可用 `scripts/maintenance/8_fix_ffprobe.py` 做运行时规避。
>
> NAS 实测（`2.2.0`）：**2243 / 2243 个视频可播，0 异常**（`9_verify_playback.py --deep`）。

### 2.3 迁移入口加固（跟官方升级不再"启动即崩"）

**这不是上游的 bug，是 fork 特有的坑** —— fork 自己加过一个 goose 迁移，与上游撞了版本号。

**背景**：goose 用「文件名数字前缀」当迁移唯一标识。fork 的头像迁移原本是 `202610040001_add_jav_idol_avatar.go`，
而上游 #375 新增了同为 `202610040001` 的 `add_watched_time.go` —— 同号会在 goose 全局注册表里**互相覆盖**。

**本 fork 的处理**：

| 问题 | 做法 |
|---|---|
| 号段撞车 | fork 自己的迁移统一用 **`2099xxxxxxxx` 保留号段**（`209901010001_add_jav_idol_avatar.go`），与上游日期号段彻底隔离 |
| 2099 排在上游之后 → 上游新迁移被 goose 判为 `missing migrations` 而**拒绝启动** | `internal/db/migrations.go` 加 **`goose.WithAllowMissing()`**，让 goose 把这类迁移**补跑**而不是报错。<br>依据 `goose/up.go`：`if option.allowMissing { migrationsToApply = missingMigrations }` |
| 旧库残留撞号行 → 上游 `watched_ms` 列建不出来 | `11_deploy_fork_image.py` 按需清理：**只在 `video.watched_ms` 确实缺失时**才删 `202610040001` 那一行，删完由 `WithAllowMissing` 补跑（迁移幂等） |

**为什么安全**：本项目所有迁移都幂等 —— `addColumnIfMissing` / `CREATE INDEX IF NOT EXISTS` /
`columnExists` 早退，重复执行无副作用。所以「补跑」永远是正确的。

> `2099` 号段与 `WithAllowMissing()` 是**成对约束**：删掉其中任何一个，上游下次发版后容器都会启动失败。
> 依据与踩坑实录见 [`maintenance-zh.md` §5.1](maintenance-zh.md)。

---

## 3. 运维工具链

### 3.1 环境变量（凭据一律走环境变量，绝不落盘）

| 变量 | 默认 | 说明 |
|---|---|---|
| `NAS_HOST` | `192.168.1.10`（占位） | NAS / 服务器地址 |
| `NAS_USER` | `admin` | SSH 用户 |
| `NAS_PASS` | — | **必填**，SSH 密码 |
| `NAS_DATA` | `/share/CACHEDEV1_DATA/Container/javboss/data` | 宿主机数据目录（bind mount 源） |
| `NAS_BACKUP` | `/share/CACHEDEV1_DATA/Backup/javboss` | 备份根目录 |
| `PROXY_URL` | `http://192.168.1.20:3128`（占位） | 局域网正向代理 |
| `JAVBOSS_PASS` | `admin` | Web 登录密码 |

以上由共享库 `scripts/maintenance/nas_env.py` 统一读取——**新增脚本请复用它**，不要自己拼 paramiko。

### 3.2 流水线依赖

```
                 ┌──────────────┐
                 │ 0_snap_db.py │  拉一致性 DB 快照
                 └──────┬───────┘
            ┌───────────┴───────────┐
            ▼                       ▼
   【头像链路】              【资料链路】
   1_stat_avatar_sizes       2_crawl_brand_index
        ↓                         ↓
   2_crawl_brand_index      5_enrich_avwiki
        ↓                         ↓
   3_download_hd_avatars    6_wiki_zh_names
        ↓                         ↓
   4_apply_hd_avatars       7_update_actor_info
   （停容器 + 写库）         （APPLY=1 才写库）

            共用：match_util.py（名字归一化 / 匹配）
```

### 3.3 写操作边界

| 脚本 | 是否写库 | 保护措施 |
|---|---|---|
| `0` `1` `2` `3` `5` `6` | 否 | — |
| `4_apply_hd_avatars.py` | **是** | 交互确认 + 自动 `javboss.db.bak-hd-<日期>` |
| `7_update_actor_info.py` | **是** | 需显式 `APPLY=1`；**只填空字段，绝不覆盖已有值** |

**幂等原则**：所有写操作都以「只填空 / 只在提升画质时替换」为前提，重跑安全。

### 3.4 健康检查与构建

| 脚本 | 输入 | 输出 / 判定 |
|---|---|---|
| `9_verify_playback.py` | NAS 的 JavBoss HTTP 端口 | 遍历全部视频，逐个测 `/videos/<id>/streams`。**判据**：`/stream`=206 且 `/streams`=200；前者 206 后者 404 ⇒ 就是工具路径失配。验证期间每 60s 采样资源接口，输出 RSS/goroutine/磁盘水位摘要 |
| `10_build_fork_image.py` | `fork/src` 源码 | 编译 → `Dockerfile.patch`（`FROM <上一版镜像>` + `COPY javboss /app/javboss`）→ `docker build`。**增量路线，仅适用「只改了 Go」** |
| `11_deploy_fork_image.py` | 本地已构建好的镜像 | `save` → SFTP 上传 → NAS `load` → 备份 DB → 停容器 → 按需清理 goose 残留行 → 按 `docker inspect` 现读配置重建；部署前后各打一份资源快照 |
| `8_fix_ffprobe.py` | 运行中的容器 | 补 ffprobe/ffmpeg → 重启 → 验证 → `docker commit` 固化（**仅历史镜像用**） |

`10_build_fork_image.py` 的环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `BASE_IMAGE` | `javboss-fork:2.2.0` | 底座 = 上一版可用镜像（已自带 ffprobe/ffmpeg） |
| `NEW_TAG` | `javboss-fork:2.2.1` | 目标 tag |
| `GO_IMAGE` | `golang:1.25-bookworm` | 编译用镜像 |

> **两种构建路线怎么选**：
> - **改 Go 源码、前端没动** → 用 `10_build_fork_image.py` 的增量两层 Dockerfile：底座已自带 ffprobe/ffmpeg（48 MB），
>   只覆盖二进制层（`/app/javboss`）最快，也最不容易踩 [issue #1](https://github.com/luckwalter/javboss-fork/issues/1) 的坑。
> - **跟上游升级（前端也可能变了）** → **必须**用仓库根的官方 `Dockerfile` 全量构建
>   （`docker build -t javboss-fork:<tag> .`）。只覆盖二进制层会丢掉上游的前端新功能，
>   而且全量构建后 ffprobe/ffmpeg 由 Dockerfile 放进 `/app/internal/bin/`，「底座版本 ≠ 源码版本」这个耦合**根本不存在**。

---

## 4. 部署、升级与回滚

### 4.1 构建镜像

**跟上游升级 / 大版本前进（推荐）**：用仓库根的官方 `Dockerfile` 全量构建，前端后端一次到位。

```bash
docker build -t javboss-fork:2.2.1 .
```

> 版本现状（2026-10-06）：上游最新发布 = **v2.1.1**（`ghcr.io/solr159/javboss:v2.1.1` = `latest`），
> 但 `main` 已到 **`5aa89f3`**（#375/#376/#377/#379 未发布）。**本 fork 已跟进到 `5aa89f3`**，
> 所以 ghcr 上拉不到更新版本，只能自己构建。

**只改了 Go 源码**：走增量路线，省一次前端构建。

```bash
export NAS_PASS='<密码>'
python3 scripts/maintenance/10_build_fork_image.py
#   首次编译约 7 分钟（gocache 持久化后大幅加快）
#   --skip-build 复用已有产物，只重新构建镜像
```

**推到 NAS 并重建容器**（自动备份 DB + 按需清理迁移残留行）：

```bash
export NAS_PASS='<密码>'
python3 scripts/maintenance/11_deploy_fork_image.py --image javboss-fork:2.2.1
```

### 4.2 重建容器

**完整模板见 [`maintenance-zh.md`](maintenance-zh.md) §10。** 四个不能漏的环境变量：

| 变量 | 值 | 漏了会怎样 |
|---|---|---|
| `JAVBOSS_CONTAINER` | `1` | 容器模式判定失效，路径逻辑走宿主机分支 |
| `JAVBOSS_HOST_PATH_PREFIX` | `1` | **DB 存错路径 → 播放报「视频文件或所在目录不存在」** |
| `HTTP_PROXY` / `HTTPS_PROXY` | 正向代理地址 | 刮削外网超时（内网 DNS 被污染时必须） |
| `TZ` | `Asia/Shanghai` | 日志与时间显示错位 |

### 4.3 升级后验证清单（**逐步做完，别跳**）

1. `go version -m <二进制>` 看 `vcs.revision` —— **判版本的硬证据，别信 tag**；
2. 容器 Up + 首页 200；
3. `GET /jav/GFAV-1/cover` → **200**（独立头像路由活着）；
4. `GET /jav/idols` 带 cookie → **200**（防回填逻辑没把头像顶掉）；
5. `GET /videos/<id>/streams` → **200**（少了这步，工具路径失配会静默潜伏到用户点播放才发现）；
6. `python3 scripts/maintenance/9_verify_playback.py --deep` → 全量通过；
7. `docker logs <container> | grep -c 'probe playback support error'` → **0**。

### 4.4 回滚

| 想回到 | 操作 |
|---|---|
| 上一版镜像 | `docker stop/rm` 现有容器 → 用旧 tag 按 §4.2 重建 |
| 上一版数据库 | 停容器 → `cp javboss.db.bak-<用途>-<日期> javboss.db` → 起容器 |
| 上游原版（放弃 fork 特性） | 用 `ghcr.io/solr159/javboss:<版本>` 重建。**数据不丢**，但女优头像会退回「作品封面裁切」 |

> ⚠️ **改库前必须先备份，且必须先停容器**（防止 sqlite WAL 竞态）。

---

## 5. 与官方的关系

| 维度 | 官方 | 本分支 |
|---|---|---|
| 核心功能（扫描/刮削/检索/播放/下载/扩展） | ✅ | **完全一致，一行未动** |
| 女优头像 | 作品封面裁切 | **独立高清人像** |
| `jav_idol` 表 | 无头像列 | +2 列 +1 索引 |
| 容器工具路径 | 硬编码单点 | **候选链回退** |
| 工具缺失报错 | 404 误导 | **503 准确** |
| goose 迁移入口 | `UpContext` 默认（旧迁移缺失即 Fatal） | + `WithAllowMissing()`，缺失的旧迁移自动补跑 |
| fork 自家迁移号段 | —（日期号段） | `209901010001`（2099 保留号段，与上游隔离） |
| 运维工具链 / 手册 | 无 | **12 脚本 + 2 文档** |
| 代码差异 | — | **10 文件 / +293 −48 行（0.4%）** |
| 前端差异 | — | **0** |
| 跟进的上游基线 | — | **`main` @ `5aa89f3`**（含 4 个未发布提交） |
| Release / 二进制包 | ✅ 全平台 | ❌ 不发布，自行构建 |

---

## 6. 相关文档

| 文档 | 内容 |
|---|---|
| [`../README.md`](../README.md) | 总览与快速开始 |
| [`maintenance-zh.md`](maintenance-zh.md) | 运维手册：资料渠道清单、头像判据、**踩坑 Top 12**、跟官方升级流程、容器重建模板、备份回滚、数据基线、安全红线 |
| [`../scripts/maintenance/README.md`](../scripts/maintenance/README.md) | 工具链逐脚本说明 |
| [issue #1](https://github.com/luckwalter/javboss-fork/issues/1) | 容器模式 ffprobe/ffmpeg 路径硬编码 |
| [issue #2](https://github.com/luckwalter/javboss-fork/issues/2) | 播放错误误分类 + 死代码 + 永久缓存 |
| [issue #3](https://github.com/luckwalter/javboss-fork/issues/3) | goose 未启用 `WithAllowMissing` → 容器启动即 Fatal |
| [issue #4](https://github.com/luckwalter/javboss-fork/issues/4) | goose 迁移版本号撞号 → 迁移被永久跳过 |
| [issue #5](https://github.com/luckwalter/javboss-fork/issues/5) | 测试断言硬编码 LF（Windows CRLF，未修复） |
| [issue #6](https://github.com/luckwalter/javboss-fork/issues/6) | 工具链静默失败（apk 错误被吞 / 备份早于停容器） |
