# 版本沿革（CHANGELOG）

本文件记录 fork 镜像 **`javboss-fork:<tag>`** 的每一次迭代：改了什么、为什么改、怎么验证的、对应哪个源码提交。
每次发布新版本时**必须**在本文件追加条目并推送 GitHub（清单见 `docs/maintenance-zh.md` §12 发版清单）。

约定：

- **fork 版本号**指本地镜像 tag `javboss-fork:x.y.z`，与官方 Release 号**没有对应关系**；
- **上游基线**指该镜像二进制所基于的官方源码 commit；
- 验证口径：`9_verify_playback.py --deep` 的「全库可播数 / 总数」。

---

## 镜像 tag ↔ 底座 ↔ 二进制源码 对照

| fork 镜像 tag | 发布日期 | 底座 | 二进制源码（上游基线） | 状态 |
|---|---|---|---|---|
| `2.2.1` | 2026-10-06 | `javboss-fork:2.2.0`（增量 COPY） | fork `91ad357` ← 上游 `main 5aa89f3` | ✅ 当前生产 |
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

## 2.2.1 — 2026-10-06（当前生产）

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
