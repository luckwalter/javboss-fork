# 版本沿革（CHANGELOG）

本文件记录 fork 镜像 **`javboss-fork:<tag>`** 的每一次迭代：改了什么、为什么改、怎么验证的、对应哪个源码提交。
每次发布新版本时**必须**在本文件追加条目并推送 GitHub（清单见 `docs/maintenance-zh.md` §12 发版清单）。

约定：

- **fork 版本号**指本地镜像 tag `javboss-fork:x.y.z`，与官方 Release 号**没有对应关系**；
- **上游基线**指该镜像二进制所基于的官方源码 commit；
- 验证口径：`9_verify_playback.py --deep` 的「全库可播数 / 总数」。

---

## 工具链动态（不发新镜像，只改维护脚本）

- **2026-10-07 观测接口接入**：上游 #357（数据源可用性）与 #369（资源监控）两个后端 API 接进维护脚本——
  ① `9_verify_playback.py` 验证期间每 60s 采样 `GET /system/resources`，输出 RSS/goroutine/磁盘水位摘要（>90% 告警）；
  ② `11_deploy_fork_image.py` 部署前后各打一份资源快照；
  ③ 样品图自愈脚本（NAS cron `fix_sample_thumbs.py`，仓库外）坏源名单动态化：调 `GET /jav/providers`
  （必要时 `POST /jav/providers/<数字id>/availability`）探测，`status=ok` 的 provider 本轮解封，
  其余沿用静态名单；API 不可达回退静态名单，幂等性不变。
  线上验证：动态判定 `javbus/javmenu=ok(解封)、javdb=not_found(坏)`，全量修复 `changed_rows=0`；
  9 号全量验证 `2243/2243` + 资源观测 7 次采样（RSS 89~121MB，data_disk 24.4%）。

---

## 镜像 tag ↔ 底座 ↔ 二进制源码 对照

| fork 镜像 tag | 发布日期 | 底座 | 二进制源码（上游基线） | 状态 |
|---|---|---|---|---|
| `2.2.3` | 2026-10-26 | 官方 Dockerfile 四段式全量构建 | fork `50a3746`（合并上游 `main 0eabaf0` = v2.1.3） | 本地已构建，待部署 NAS |
| `2.2.2` | 2026-10-13 | 官方 Dockerfile 四段式全量构建 | fork `dc0afbc`（合并上游 `main e7c5834` = v2.1.2） | 当前 NAS 生产（将被 2.2.3 取代） |
| `2.2.1` | 2026-10-06 | `javboss-fork:2.2.0`（增量 COPY） | fork `91ad357` ← 上游 `main 5aa89f3` | 已被 2.2.2 取代 |
| `2.2.0` | 2026-10-06 | 官方 Dockerfile 四段式全量构建 | fork `b413ed0`（合并上游 main `5aa89f3`） | ⚠️ 未留产线（启动 Fatal，见下） |
| `2.1.3` | 2026-10-06 | `javboss-fork:2.1.2`（增量 COPY） | fork `ad880f4` ← 上游 v2.1.1 `fde33e4` | 已被 2.2.1 取代 |
| `2.1.2` | 2026-10-06 | 官方 `v2.1.0` + 运行时补 ffprobe（docker commit） | 同 2.1.1 | 过渡版本 |
| `2.1.1` | 2026-10-04 | 官方 `ghcr.io/solr159/javboss:v2.1.0`（19 层原底座） | fork `40c3d89` ← 上游 v2.1.1 `fde33e4`+头像补丁 | 已被 2.1.2 取代 |
| `2.1.0` | 2026-10-02 | 官方 `ghcr.io/solr159/javboss:v2.1.0` 原镜像 | 官方原版，零改动 | 首个基线（回退锚点） |

> ⚠️ 命名坑：fork 镜像 tag `2.1.1` 与官方 Release v2.1.1 **无关**（恰好是第 2 个 fork 镜像）。
> 二进制实际是 v2.1.1 源码 + 头像补丁，底座却是官方 v2.1.0 —— 这种「底座≠源码」的错位正是
> [issue #1](https://github.com/luckwalter/javboss-fork/issues/1) 播放全线 404 的根因，
> 也是 §9「底座必须与二进制源码同版本」这条铁律的由来。

---

## 2.2.3 — 2026-10-26（本地已构建，待部署 NAS）

**源码**：fork `50a3746`（merge 上游 `main 0eabaf0`，即上游 Release **v2.1.3**）｜**构建**：官方 `Dockerfile` 四段式**全量**构建（node 前端 → golang 后端 → alpine 静态 ffmpeg → distroless，216 MB；`APP_VERSION=2.2.3` 经 ldflags 注入）

### 新增（随上游 v2.1.3 合入，7 个提交 #383–#414）

- **#403 后端结构重组**：`internal/server/*` 整体重命名为 `internal/api/*`，`internal/manager/*` 重组为 `internal/jav/scrape/cover` 等；`runtimeconfig` 包并入 `internal/common`（`runtimeconfig.ContainerMode()` → `common.ContainerMode()`）。
- **#383 / #402 / #411 / #412 / #413 / #414**：标题与界面翻译、竖屏适配、UX 调整、CI 工作流、版本号升至 v2.1.3 等。
- 全量构建原因同 2.2.2：上游改动含前端（web 大量改动），增量 `COPY` 二进制会漏掉前端产物。

### 冲突解决（merge 共 4 处，均已保留 fork 自定义）

- `internal/util/video.go`：**保留 fork 的 ffprobe/ffmpeg 多候选回退逻辑**（env → 当前镜像布局 → 历史镜像布局 → PATH），仅把 `runtimeconfig.ContainerMode()` 改为 `common.ContainerMode()`；并修复冲突合并时 `if common.ContainerMode()` 大 if 被过早关闭、候选链掉到 if 外引用未声明变量的结构错误。
- `internal/api/jav_cover_api.go`：**保留 fork 女优独立头像优先**（`lookupIdolAvatarFile` / `hasIdolAvatarFile`）；把 `manager.FindCoverPath` 改为 `cover.FindPath(cfg.JavCoverDir, code)`，`common.AppConfig` / `common.DB` 改为 `common.CurrentConfig()` / `db.Default().DB()`（为此给 `db.Store` 新增公开的 `DB() *gorm.DB` 访问器）。
- `internal/db/jav.go`：保留 fork 的 `COALESCE(ji.avatar_code, NULLIF(cover_jav.code,''), solo_idols.cover_code)` 头像优先逻辑；`buildVisibleIdolWorkCountQuery` / `buildVisibleSoloIdolCoverQuery` 随上游改为 `Store` 方法，调用处加 `store.` 前缀。
- `README.md`：以 fork 版本为主体，仅同步上游新增的翻译/竖屏/UX 章节，**不引入**上游「方式二/方式三」部署章节。

### 说明

- 本次属「跟上游升级」，非 fork 功能新增：fork 图像增强主线（女优头像 / 厂牌高清艺术照 / 样品图修复 / 播放修复）**不受影响**，fork 补丁全部保留。
- 迁移铁律不变：`2099` 号段 + `goose.WithAllowMissing()` 成对约束。
- **本地编译曾现假阴性**：`golang:1.26-alpine` 默认 `CGO_ENABLED=0` 且无 gcc，cgo-only 的 `go-sqlite3` 整体被排除、`*sqlite.SQLiteConn` 类型未定义、误报 `conn.Exec undefined`；改用官方 `golang:1.25-bookworm`（CGO 开、带 gcc）全量 `docker build` 即通过。

### 验证（阶段一·本地）

- **全量 `docker build`**：四段全部通过（前端 11892 模块 / ffmpeg sha256 校验 / 后端 `go build ./cmd/server` exit 0 / distroless 组装），产出 `javboss-fork:2.2.3` + `:latest`。
- 阶段二（部署 NAS）待主人确认后由 `11_deploy_fork_image.py` 执行，并对照 2.2.2 的 2235/2235 全量验证可播数。

---

## 2.2.2 — 2026-10-13（当前生产）

**源码**：fork `dc0afbc`（merge 上游 `main e7c5834`，即上游 Release **v2.1.2**）｜**构建**：官方 `Dockerfile` 四段式**全量**构建（node 前端 → golang 后端 → alpine 静态 ffmpeg → distroless，216 MB；`APP_VERSION=2.2.2` 经 ldflags 注入，界面可显示版本号）

### 新增（随上游 v2.1.2 合入，7 个 PR）

- **#384 观看时长显示**：`watched_ms` 全栈落地（迁移建列 → `watched_time_api.go` → 前端播放状态与图标）。
- **#385** 代码优化、**#386** Release 显示版本号、**#387** toast 优化、**#388** 修复 MPV 不随主进程退出、**#389** 版本号升至 v2.1.2、**#391** Docker 部署说明清晰度。
- 全量构建（而非增量）的原因：本次上游改动**含前端**（web 大量改动），增量 `COPY` 二进制会漏掉前端产物。

### 冲突解决（merge 仅 2 处）

- `internal/server/video_api.go`：**保留 fork 完整修复**（`util.ErrFFToolMissing` 哨兵 + 判定排在 `os.ErrNotExist` 之前的顺序保证 + 404 分支），**仅吸收上游文案**「缺少浏览器播放所需组件」→「缺少**网页播放器**所需组件」。
- `README.md`：以 fork 版本为主体；上游基线 `5aa89f3` → `e7c5834`/v2.1.2、徽章同步、修正过时的「不发布 Release」声明、补回上游 #391 的 Docker 部署说明（Docker 默认仅网页播放器，MPV 需 Client 模式）。
- `internal/db/jav.go` 由 git **自动合并**（fork 头像字段 + 上游 `watched_ms` 排序，不同区块无冲突）。

### 说明

- 本次属「**跟上游升级**」，非 fork 功能新增：fork 的图像增强主线（女优头像 / 厂牌高清艺术照 / 样品图修复 / 播放修复）**不受影响**，fork 的 10 个 `.go` 补丁全部保留。
- 迁移铁律不变：`2099` 号段 + `goose.WithAllowMissing()` 成对约束；本次部署判据为 `video.watched_ms` **已存在** ⇒ 撞号的 `202610040001` 行是真实执行记录，**保留不删**。
- **rebase 路线不通**：本 fork 是独立仓（非 fork 链接），自带 `Initial commit` 的 README 与上游形成 `add/add` 冲突，硬 rebase 会在第 4 个提交撞死。改走 `git merge origin/main`，回滚锚点 `backup-main-before-upstream-20261012`（= 合并前 `b6fecc51`）。

### 验证

- **编译**：`docker run golang:1.25` + `go build ./cmd/... ./internal/...` → exit 0（零错误）。
- **NAS 全量播放：2235 / 2235 可播、0 异常**；`--deep` 抽测前 20 部 `/stream` 与 m3u8 全 200。
- **资源观测**（7 次采样）：RSS 77.6 ~ 98.8 MB、goroutine 40 ~ 78、`data_disk` 38.3%（79.1 / 207 GB）。
- 部署前后快照：RSS 90.6 MB → 冷启动 37.3 MB；DB 已备份 `javboss.db.bak-upgrade-20261013-003506`。

---

## 2.2.1 — 2026-10-06（已被 2.2.2 取代）

**源码**：fork `91ad357`（+ 文档收尾 `6ce4b0d`）｜**构建**：增量 `FROM javboss-fork:2.2.0` + `COPY out/javboss`（二进制 sha256 与镜像内逐字节一致）

### 新增

- **迁移入口加 `goose.WithAllowMissing()`**（`internal/db/migrations.go`）：
  goose 对「版本号低于已应用最高版本、但未执行」的迁移默认**报错退出**；该选项改为**补跑**（已核对 goose v3.26.0 `up.go` 源码：`migrationsToApply = missingMigrations`）。前提是所有迁移幂等——本项目全部满足（`addColumnIfMissing` / `CREATE INDEX IF NOT EXISTS`）。
  - fork 迁移使用 `2099` 保留号段（`209901010001_add_jav_idol_avatar.go`），排在一切上游日期号段之后 ⇒ **`2099` 号段与 `WithAllowMissing()` 是成对约束，缺一不可**（见 [issue #3](https://github.com/luckwalter/javboss-fork/issues/3)、[#4](https://github.com/luckwalter/javboss-fork/issues/4)）。
- **`scripts/maintenance/11_deploy_fork_image.py`**：一键安全部署——`docker save` → SFTP → NAS `load` → 停容器 → 备份 DB → **按需**清理迁移残留行（判据 = `video.watched_ms` 是否缺失，重复部署是安全空操作）→ 按 `docker inspect` 现读配置重建。改库走本地 sqlite3 往返，不依赖 NAS 联网。

### 修复

- [issue #3](https://github.com/luckwalter/javboss-fork/issues/3)：goose 未启用 `WithAllowMissing` → 容器启动即 Fatal（`found N missing migrations before current version X`）。
- [issue #4](https://github.com/luckwalter/javboss-fork/issues/4)：goose 迁移以文件名数字前缀为唯一标识，fork `202610040001` 与上游 `#375` 同号互相覆盖（`migrate.go:35-37` duplicate panic）；fork 改 `2099` 号段。
- [issue #6](https://github.com/luckwalter/javboss-fork/issues/6)：维护工具链两处静默失败（`nas_env.docker_python` 的 `apk add` 失败被 `&&` 短路吞掉；`sh()` 不返回 exit code）。

### 文档 / 仓库

- README 徽章、概述、部署章节全面对齐新基线 `upstream main @5aa89f3`；手册 §1 改动清单、§5.1 迁移机制重写；`fork-features-zh.md` 新增 §2.3。
- 代码差异口径订正：**10 个 `.go` 文件 / +293 −48**（占上游 68,321 行的 0.4%）。

### 验证

- **A/B 回归**（同一份删掉撞号行的线上库快照）：`2.2.0` → `exited(1)` 精确复现事故；`2.2.1` → `running`，`GET / = 200`，缺失的版本行被自动补回。
- **NAS 全量播放：2243 / 2243 可播、0 异常**；接口体检全绿（登录 / `/jav/idols` / 独立头像 cover 链路 / `probe playback support error` 计数 0）。
- NAS `Container/javboss/docker-compose.yml` 基线同步 2.2.1，FF 路径纠正为 `/app/internal/bin/*`。

---

## 2.2.0 — 2026-10-06（未留产线）

**源码**：fork `b413ed0`（合并上游 main）+ `fae0324`（迁移改 2099 号段）｜**构建**：首次按官方 `Dockerfile` 四段式全量构建（216 MB，node 前端 → golang 后端 → alpine 静态 ffmpeg → distroless）

### 新增（随上游 `main 5aa89f3` 合入，4 个未发布提交）

- `#375` watch-time 观看时长统计（`watched_ms` 两列）
- `#376` jav 删除支持
- `#377` cover / capture 行为修复
- `#379` 浏览器播放列表

### 其他

- fork 迁移 `202610040001_add_jav_idol_avatar` 因与上游撞号改名 `209901010001`（`fae0324`）。

### 已知问题（本版本未上线的原因）

- 未启用 `WithAllowMissing` → 部署即容器 `Restarting (1)`：`found 1 missing migrations before current version 209901010001`。
  先用 `_hotfix_goose.py` 抢修恢复服务（停容器 → 补 `watched_ms` 两列 → 补回版本行），随后 `2.2.1` 从代码层根治。详见 [issue #3](https://github.com/luckwalter/javboss-fork/issues/3)。

---

## 2.1.3 — 2026-10-06

**源码**：fork `ad880f4`（+ 文档 `6a1b216`）｜**构建**：增量 `FROM javboss-fork:2.1.2` + `COPY`（309 MB）

### 修复（代码根治，取代 2.1.2 的运行时规避）

- [issue #1](https://github.com/luckwalter/javboss-fork/issues/1)：容器模式 ffprobe/ffmpeg 路径硬编码（`ContainerFFBinaryDir = "/app/internal/bin"` 单点、忽略 `FFPROBE_PATH`/`FFMPEG_PATH`）→ 改为**候选链回退**：`env(FFPROBE_PATH/FFMPEG_PATH)` → `/app/internal/bin` → `/usr/local/bin` → PATH。
- [issue #2](https://github.com/luckwalter/javboss-fork/issues/2)：播放探测失败被误分类成 404「视频文件或所在目录不存在」（`errors.Is(os.ErrNotExist)` 判定排在 ffprobe 缺失之前 + `ffprobe not found` 字符串判据是死代码 + 失败结果被 `sync.Once` 永久缓存）→ 新增哨兵错误 `util.ErrFFToolMissing`，工具缺失改报 **503「缺少浏览器播放所需组件」**，且仅在成功时缓存探测结果（补齐工具无需重启）。

### 验证

- NAS 全量播放 **2243 / 2243 可播、0 异常**（本条即 issue #1/#2 关闭依据）。

---

## 2.1.2 — 2026-10-06（过渡版本）

**源码**：无变更（同 2.1.1 二进制）｜**构建**：官方 `v2.1.0` 镜像内补 `/app/internal/bin/{ffprobe,ffmpeg}`（从官方 v2.1.0 镜像提取，各 48 MB）→ `docker commit` 固化（278 MB）→ 容器按 `docker inspect` 逐项对齐重建。

- 定位：播放全线 404 事故的**运行时急救**（当天已工具化为 `scripts/maintenance/8_fix_ffprobe.py`，仅适用于 ≤ 2.1.2 的历史镜像）。
- 事故复盘沉淀为手册 §9 与 [issue #1](https://github.com/luckwalter/javboss-fork/issues/1)；「底座必须与二进制源码同版本」自此成为铁律（手册 §9 根治原则）。

---

## 2.1.1 — 2026-10-04（fork 首个功能版本）

**源码**：fork `40c3d89` ← 上游 v2.1.1 `fde33e4` ｜**底座**：官方 `ghcr.io/solr159/javboss:v2.1.0` 原镜像（19 层，零改动）

### 新增

- **女优独立高清头像**：`jav_idol` 表加 `avatar_code` / `avatar_file` 两列 + 索引（迁移 `202610040001_add_jav_idol_avatar`，当时用的日期号段——此名后来在 2.2.0 撞上游 #375，见 issue #4）；`/jav/<idol>/cover` 优先返回独立头像；已有独立头像时防作品封面回填。
- **Gfriends 头像数据**：1254 / 1276 张（170 MB）落库，写 `avatar_code=GFAV-<id>`，补名 104 行。

> 此时「底座 v2.1.0 + 二进制 v2.1.1」的错位已埋下，10-06 才以播放 404 的形式爆雷（issue #1）。

---

## 2.1.0 — 2026-10-02（首个基线）

- 首次部署官方 `ghcr.io/solr159/javboss:v2.1.0` 原镜像（8655 端口，host 网络，`--restart=unless-stopped`），零改动，用于可行性验证与片库导入。
- 保留为**回退锚点**：`javboss-fork:2.1.0` 即官方镜像的原 tag 副本。
