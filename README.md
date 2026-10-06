<p align="center">
  <img src="assets/branding/javboss-icon.png" width="96" alt="JavBoss">
</p>

<h1 align="center">JavBoss Fork · 女优高清头像 + 运维工具链</h1>

<p align="center">
本仓库是 <a href="https://github.com/Solr159/JavBoss">Solr159/JavBoss</a> 的<b>个人维护分支（fork）</b>。<br>
<b>上游 JavBoss 是本体</b>——刮削、管理、检索、播放等全部核心能力都来自上游，本分支不做重写；<br>
只在它之上叠加三样东西：<b>一个官方没有的功能</b>、<b>两个官方存在的缺陷的修复</b>、<b>一整套运维工具链</b>。
</p>

<p align="center">
  <a href="https://github.com/Solr159/JavBoss"><img alt="Upstream" src="https://img.shields.io/badge/upstream-Solr159%2FJavBoss-181717?logo=github&logoColor=white"></a>
  <img alt="Based on" src="https://img.shields.io/badge/based%20on-upstream%20main%20%405aa89f3-1E88E5">
  <img alt="License" src="https://img.shields.io/badge/License-GPL--3.0-blue">
  <img alt="Go" src="https://img.shields.io/badge/Go-1.25%2B-00ADD8?logo=go&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-distroless-2496ED?logo=docker&logoColor=white">
</p>

---

## 目录

- [这个 fork 做了什么](#这个-fork-做了什么)
- [1. 女优独立高清头像（核心特性）](#1-女优独立高清头像核心特性)
- [2. 上游缺陷修复](#2-上游缺陷修复)
- [3. 运维工具链](#3-运维工具链)
- [4. 部署本分支](#4-部署本分支)
- [5. 跟官方升级](#5-跟官方升级)
- [6. 仓库结构](#6-仓库结构)
- [7. 上游 JavBoss（本体）](#7-上游-javboss本体)
- [免责声明](#免责声明)

---

## 这个 fork 做了什么

> **代码层面只动 10 个文件 / +293 −48 行**（占官方 68,321 行 Go 代码的 **0.4%**），**前端零改动**。
> 改动面如此之小，是为了让「跟官方升级」永远保持低成本。

| # | 类别 | 内容 | 落地形式 |
|---|---|---|---|
| 1 | **功能增强** | 女优头像改用**独立高清人像**（1456 人中 1288 人已有），不再是「从某部作品的封面裁一块」 | 5 处源码改动 + goose 迁移 + 头像数据文件 |
| 2 | **缺陷修复** | 容器模式下 ffprobe/ffmpeg 路径硬编码，导致**全部视频播放时报「视频文件或所在目录不存在」** | 源码修复 → [issue #1](https://github.com/luckwalter/javboss-fork/issues/1) |
| 3 | **缺陷修复** | 播放探测失败被**误分类成 404**；`"ffprobe not found"` 判据是**死代码**；失败结果被 `sync.Once` **永久缓存** | 源码修复 → [issue #2](https://github.com/luckwalter/javboss-fork/issues/2) |
| 4 | **升级加固** | 迁移入口加 `goose.WithAllowMissing()`，与 fork 的 `2099` 迁移号段**成对使用**，避免跟官方升级后容器启动即 Fatal | 1 处源码改动（见 [第 5.1 节](#5-跟官方升级)） |
| 5 | **运维工具链** | 12 个可复用脚本：资料补全 / 高清头像流水线 / 播放全量验证 / 一键编译出镜像 / 一键部署到 NAS | [`scripts/maintenance/`](scripts/maintenance/) |
| 6 | **运维手册** | 资料渠道清单、头像替换判据、踩坑 Top 12、跟官方升级流程、容器重建模板、安全红线 | [`docs/maintenance-zh.md`](docs/maintenance-zh.md) |
| 7 | **功能详解** | 本 fork 每个特性的原理、数据、验证方式 | [`docs/fork-features-zh.md`](docs/fork-features-zh.md) |

**本分支不发布 Release、不分发二进制**——它只提供源码与运维脚本，成品镜像是本地构建的。

---

## 1. 女优独立高清头像（核心特性）

上游 JavBoss 的「女优头像」**并不是头像**：`jav_idol` 表没有头像列，列表里那张脸其实是**随机挑一部该女优的作品封面裁切出来的**——所以常常出现构图奇怪、人像偏移、清晰度堪忧的情况。

本分支补上了这条语义链：

| 维度 | 上游原版 | 本分支 |
|---|---|---|
| 头像来源 | 某部作品的封面，裁一块 | **独立的高清人像文件** |
| 数据库 | `jav_idol` 无头像列 | 新增 `avatar_code` / `avatar_file` 两列 + 1 个索引 |
| 访问路由 | 与作品封面共用 `/jav/:code/cover` | **复用同一路由**，用虚拟码 `GFAV-<idol_id>` 命中独立头像（未新增任何路由） |
| 防回填 | — | 已有独立头像时，作品封面刮削**不再覆盖**它 |
| 前端 | — | **一行未改** |

**实现方式**（4 个文件，全部是「新增式」改动，天然低冲突）：

1. **迁移** `internal/db/migrations/209901010001_add_jav_idol_avatar.go` — 加列 + 建索引（fork 迁移统一放在 `2099` 保留号段，与上游的日期号段彻底隔离，见 [第 5 节](#5-跟官方升级)）；
2. **模型** `internal/models/jav.go` — `JavIdol` 加 `AvatarCode` / `AvatarFile` 字段；
3. **查询** `internal/db/jav.go` — 女优列表两处 SELECT 改用 `COALESCE(ji.avatar_code, …)`，让虚拟码优先于作品番号；
4. **路由** `internal/server/jav_cover_api.go` + `jav_idol_api.go` — `lookupIdolAvatarFile()` 命中独立头像就直接返回文件（排在作品封面查找**之前**），`hasIdolAvatarFile()` 则拦住刮削回填。

**数据现状**：1456 位演员中 **1288 人**已有头像（Gfriends 素材库 1254 张 + 厂牌官网高清艺术照新增 34 张），其中 **392 张**由 Gfriends 升级为厂牌官方艺术照；素材目录 `data/gfriends_avatars/`，约 170 MB。

**替换判据不是文件大小，而是「有效像素」**——因为前端是「按高度铺满 + 横向裁切」，横图有一半宽度会被裁掉：

```
有效像素 eff(w,h) = h × min(w, h × 0.6989)      # 0.6989 = (800×0.47)/538
仅当 eff(new)/eff(old) ≥ 1.15 才替换，绝不做降级替换
```

细节见 [`docs/fork-features-zh.md`](docs/fork-features-zh.md)。

---

## 2. 上游缺陷修复

两个问题都已在本分支修好，并在本仓库开了 issue 存档（含完整复现步骤、根因代码、修复前后行为对照）：

### [issue #1](https://github.com/luckwalter/javboss-fork/issues/1) · 容器模式 ffprobe/ffmpeg 路径硬编码

| | |
|---|---|
| **现象** | Docker 部署下所有视频都播不了，提示「**视频文件或所在目录不存在**」；但文件确实在——`GET /videos/<id>/stream` 返回 **206**（直连能出数据） |
| **根因** | `internal/util/video.go` 的 `ContainerFFBinaryDir = "/app/internal/bin"`：容器模式下**只认这一个绝对路径**，完全忽略 `FFPROBE_PATH` / `FFMPEG_PATH` 环境变量。而官方镜像的 ffmpeg 布局**随版本变过**（早期在 `/usr/local/bin/`，v2.1.1 起改到 `/app/internal/bin/`）——只要镜像底座与二进制版本不一致，就必然找不到工具 |
| **修复** | 改为**有优先级的候选链**：`env(FFPROBE_PATH/FFMPEG_PATH)` → `/app/internal/bin` → `/usr/local/bin` → `PATH`；同时保留「不依赖宿主机 PATH」的原始设计意图 |
| **顺带** | `ResolveFFprobePath` 改为**仅成功时缓存**——补齐工具后**无需重启进程**即可恢复 |

### [issue #2](https://github.com/luckwalter/javboss-fork/issues/2) · 播放错误被误报 + 死代码 + 永久缓存

| | |
|---|---|
| **现象 A** | 工具缺失时返回 **404「视频文件或所在目录不存在」**，把排查方向彻底带偏到「文件丢了 / 挂载坏了 / 路径写错了」 |
| **根因 A** | `respondPlaybackError` 的 switch 里，`errors.Is(err, os.ErrNotExist)` 排在工具缺失分支**之前**；而「工具缺失」底层就是 `stat` 失败 → `fs.ErrNotExist` → 被优先命中 |
| **修复 A** | 新增哨兵错误 `util.ErrFFToolMissing`，把工具缺失判定**排到最前**，并改报 **503「缺少浏览器播放所需组件」**（语义正确，且不会误导用户去翻文件） |
| **现象 B** | 代码里 `strings.Contains(err, "ffprobe not found")` 这个判据**永远不会成立**——实际生成的消息是 `"ffprobe unavailable in Docker image at ..."`，两串字符零重叠 |
| **修复 B** | 判据一并对齐（这也是上面的哨兵错误存在的意义：不再依赖脆弱的字符串匹配） |
| **现象 C** | 按提示补好 ffprobe 后**必须重启容器**才生效，否则一直 404 |
| **修复 C** | `sync.Once` 缓存失败结果 → 改为**仅缓存成功**，失败下次重试 |

> 修复提交：`ad880f4`（源码）+ `6a1b216`（文档）——即镜像 **`javboss-fork:2.1.3` 起生效**。
> 镜像版本沿革：`2.1.3`（修复落地）→ `2.2.0`（合并上游 `main` `5aa89f3`）→ **`2.2.1`**（迁移入口加 `WithAllowMissing`，当前基线）。
> 已在 NAS 上跑过全量验证：**2243 / 2243 个视频可播、0 异常**。
> 未修复的历史镜像（≤ v2.1.2）仍可用工具链里的 `8_fix_ffprobe.py` 做运行时规避。

---

## 3. 运维工具链

把「演员资料补全 + 高清头像替换 + 播放健康检查 + 重新出镜像」全流程脚本化。凭据一律走环境变量，产物落在已 gitignore 的 `artifacts/`。

### 3.1 数据流水线（按序执行）

| 步骤 | 脚本 | 作用 | 写操作 |
|---|---|---|---|
| 0 | `0_snap_db.py` | 拉一份一致性 DB 快照到本地 | 否 |
| 1 | `1_stat_avatar_sizes.py` | 统计现有头像分辨率 | 否 |
| 2 | `2_crawl_brand_index.py` | 抓 7 家厂牌官网的演员索引 | 否 |
| 3 | `3_download_hd_avatars.py` | 匹配 + 下载官方艺术照 | 只下载，不动现有文件 |
| 4 | `4_apply_hd_avatars.py` | 按**有效像素**判据替换/新增头像 | **停容器 + 写库**（带确认） |
| 5 | `5_enrich_avwiki.py` | 抓生日 / 身高 / 三围 | 否 |
| 6 | `6_wiki_zh_names.py` | 维基跨语言链接挖中文名 + 字形重叠过滤 | 否 |
| 7 | `7_update_actor_info.py` | 组装更新计划并落库（**只填空字段**） | `APPLY=1` 才写 |

- 头像链路：`0 → 1 → 2 → 3 → 4`
- 资料链路：`0 → 2 → 5 → 6 → 7`
- `match_util.py` 是名字归一化 / 匹配的共用库

### 3.2 健康检查与构建

| 脚本 | 用途 |
|---|---|
| `9_verify_playback.py` | **全量**验证每个视频都可播（遍历逐个测 `/videos/<id>/streams`）。升级 / 重编镜像 / 动挂载后**必跑**；`--deep` 另抽测 `/stream` 与 m3u8 |
| `10_build_fork_image.py` | **改完 Go 源码后一键出镜像**：Go 容器编译 → 生成两行 `Dockerfile.patch`（`FROM <上一版镜像>` + `COPY javboss /app/javboss`）→ `docker build`。用 `--skip-build` 可复用已有产物。⚠️ 增量路线仅适用「只改了 Go」，**跟上游升级（前端也变了）请直接用仓库根的官方 `Dockerfile` 全量构建** |
| `11_deploy_fork_image.py` | **把镜像推到 NAS 并安全重建容器**：`docker save` → SFTP 上传 → NAS `docker load` → 备份 DB → 停容器 → 按需清理 goose 撞号残留版本行 → **按 `docker inspect` 现读的真实配置重建**（不凭记忆写参数）。`--dry-run` 只打印计划 |
| `8_fix_ffprobe.py` | 播放「文件不存在」故障的**运行时**修复（补齐 ffprobe + 重启 + commit 固化）。**仅用于 ≤ v2.1.2 的历史镜像**，2.1.3 起源码已根治 |
| `nas_env.py` | 共享配置：连接、远端执行、上传脚本、路径常量——**新脚本请复用它** |

### 3.3 环境准备

```bash
pip install paramiko                  # 唯一依赖（Python 3.9+）

export NAS_HOST='192.168.1.10'        # 你的 NAS 地址（占位示例）
export NAS_PASS='<NAS SSH 密码>'       # 必填，不写进任何文件
# 可选：NAS_USER / NAS_DATA / NAS_BACKUP / PROXY_URL / JAVBOSS_PASS
```

> 所有脚本里的 IP、路径都是**占位默认值**，请按自己的环境覆盖。

---

## 4. 部署本分支

本分支**不发布 Release、不推镜像**，成品镜像自己构建。

> **版本现状**（2026-10-06 核实）：
> 上游最新发布 = **v2.1.1**（`ghcr.io/solr159/javboss:v2.1.1`，也就是 `latest`），
> 而上游 `main` 已经到 **`5aa89f3`**（含 #375 watch-time / #376 jav delete / #377 cover 修复 / #379 播放列表 四个**未发布**提交）。
> **本分支已跟进到 `5aa89f3`** —— 所以在 ghcr 上**拉不到**更新版本，`5aa89f3` 的能力只能自己构建。

```bash
# 1) 全量构建（用仓库根目录自带的官方 Dockerfile，四段式）
#    node 构建前端 → golang 构建后端 → 下载静态 ffmpeg/ffprobe → distroless 底座
docker build -t javboss-fork:2.2.1 .
#    走这条路前端会跟着上游一起更新，且不存在「底座版本 ≠ 源码版本」的耦合

# 2) 推到 NAS 并按现读配置重建容器（自动备份 DB、按需清理迁移残留行）
export NAS_PASS='<NAS SSH 密码>'
python3 scripts/maintenance/11_deploy_fork_image.py --image javboss-fork:2.2.1

# 3) 全量验证「每个视频都能播」
python3 scripts/maintenance/9_verify_playback.py --deep
```

> 只改 Go 源码、前端没动时，可用增量路线 `10_build_fork_image.py` 省一次前端构建
> （原理：`FROM <上一版镜像>` + `COPY javboss /app/javboss`）。
> 但**跟上游升级时务必走全量构建**——只覆盖二进制层会丢掉上游的前端新功能。

### 重建容器时**不能漏**的 4 个环境变量

| 变量 | 值 | 漏了会怎样 |
|---|---|---|
| `JAVBOSS_CONTAINER` | `1` | 容器模式判定失效，路径逻辑走宿主机分支 |
| `JAVBOSS_HOST_PATH_PREFIX` | `1` | **数据库存错路径 → 播放报「视频文件或所在目录不存在」** |
| `HTTP_PROXY` / `HTTPS_PROXY` | 你的正向代理 | 刮削外网超时（内网 DNS 被污染时必须） |
| `TZ` | `Asia/Shanghai` | 日志与时间显示错位 |

完整的 `docker run` 命令模板、配置基线、重建前后检查清单，见 [`docs/maintenance-zh.md`](docs/maintenance-zh.md) 第 10 节。

---

## 5. 跟官方升级

改动面只有 10 个文件，所以升级路径很短：

```bash
cd JavBoss-src
git remote add upstream https://github.com/Solr159/JavBoss.git   # 首次
git fetch upstream
git log --oneline main..upstream/main                            # 看官方新提交
git merge upstream/main                                          # 预期零冲突
```

**冲突检查重点**（只有这几处可能被官方改写）：
`internal/db/jav.go`（若官方改了女优查询的 SELECT / Group，需手工合入 `COALESCE`）、
`jav_cover_api.go` 与 `jav_idol_api.go`（若官方重写这两个接口，需重放 `lookupIdolAvatarFile` / `hasIdolAvatarFile`）、
`internal/util/video.go` 与 `internal/server/video_api.go`（若官方改播放探测/错误分类，需重放候选链回退与 `ErrFFToolMissing` 503 分类）。
迁移文件是**新增文件**，不会冲突。

### 5.1 迁移版本号的坑：fork 用 `2099` 号段 + `WithAllowMissing`

**goose 用「文件名数字前缀」当迁移唯一标识。** fork 的头像迁移曾经也是 `202610040001`，
与上游 #375 新增的 `202610040001_add_watched_time.go` **撞号**，其中一个会被静默跳过。
**所以 fork 自己的迁移一律用 `2099xxxxxxxx` 保留号段**（现为 `209901010001_add_jav_idol_avatar.go`）。

2099 排在所有上游日期号段之后，于是**上游后续新增的迁移版本号恒小于它**，goose 默认会把它们判为
`found N missing migrations before current version X` 并**直接报错退出**（容器 `Restarting (1)`）。
因此 `internal/db/migrations.go` 里这行是**刚需**，与 2099 号段**成对使用**，删掉 = 上游下次发版后容器起不来：

```go
return goose.UpContext(ctx, db, migrationDir, goose.WithAllowMissing())
```

从 **≤ 2.1.3** 升级时还有一次性清理（旧库把 `202610040001` 记成「已执行」，会害得上游的
`watched_ms` 列建不出来）：`scripts/maintenance/11_deploy_fork_image.py` 会自动判断
`video.watched_ms` 是否缺失、缺失才删那一行，删完由 `WithAllowMissing` 补跑。
完整说明见 [`docs/maintenance-zh.md` §5.1](docs/maintenance-zh.md)。

升级后**必须**重编镜像——否则新官方代码不认 `avatar_code` / `avatar_file` 两列，
女优头像会**静默退回**「作品封面裁切」（数据不丢，只影响显示）。推荐直接用官方 `Dockerfile` 全量构建：

```bash
docker build -t javboss-fork:<新tag> .
```

> ⚠️ **别再用「官方旧镜像底座 + 覆盖二进制」的增量做法跟大版本升级。** 这是本仓库踩过的真实血案：
> v2.1.1 的二进制去 `/app/internal/bin/` 找 ffprobe，v2.1.0 的底座把它放在 `/usr/local/bin/` → 播放全线报错。
> 详见 [issue #1](https://github.com/luckwalter/javboss-fork/issues/1)。走官方 `Dockerfile` 全量构建就没有这个耦合。

---

## 6. 仓库结构

```
├── internal/                        # Go 后端（fork 只改了 10 个文件，见上）
├── web/                             # 前端（fork 零改动，随官方源码一起构建）
├── Dockerfile                       # 上游：四段式构建（前端 → 后端 → 静态 ffmpeg → distroless）
├── docs/
│   ├── maintenance-zh.md            # 【本分支】运维手册：渠道/判据/踩坑/升级/重建模板/安全红线
│   └── fork-features-zh.md          # 【本分支】fork 功能详解
├── scripts/
│   ├── maintenance/                 # 【本分支】运维工具链（12 脚本 + 共享库 + 说明）
│   ├── cli/  install.sh  install.ps1        # 上游：安装器
│   └── userscripts/                 # 上游：浏览器扩展
├── screenshot/                      # 上游：界面截图
├── DEVELOPMENT.md  AGENTS.md        # 上游：开发者文档
└── README.md                        # 本文件
```

**提交约定**：本分支的提交统一带 `[FORK]` 前缀，方便与上游提交区分：

```bash
git log --oneline --grep='\[FORK\]'
```

---

## 7. 上游 JavBoss（本体）

本仓库的**全部核心功能**——目录扫描、封面截图、JAV 元数据刮削、检索、目录整理、nfo/封面导出、MPV 深度集成、CloudDrive2 磁力下载、Chrome 扩展、Client 模式等——**均来自上游，本分支一行未动**。

- 上游仓库：<https://github.com/Solr159/JavBoss>
- 上游发布包（Windows / macOS / Linux / Docker）：<https://github.com/Solr159/JavBoss/releases>
- 上游使用文档：见上游仓库的 README

**部署建议**：如果你**不需要**「女优独立高清头像」，直接用官方镜像即可——
本分支的价值只在于上面那三样东西，本体部分没有任何优势。

---

## 免责声明

- 本项目是上游作者在学习 Go 语言期间开发的练手项目，本分支仅作个人运维用途，**仅供学习与交流**。
- 使用本项目时，请遵守所在国家或地区的法律法规。
- 请勿将本项目用于任何商业用途。
- 用户应自行承担使用本项目产生的一切后果，开发者不对用户的任何使用行为承担法律责任。
- 许可协议沿用上游 [GPL-3.0](LICENSE)。
