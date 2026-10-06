# JavBoss fork 维护手册（资料补全 · 头像管理 · 跟官方升级）

> 本手册沉淀 2026-10-04 ~ 10-06 两轮实战（Gfriends 头像替换、高清艺术照替换、资料补全）的全部可复用知识。
> 配套工具在 `scripts/maintenance/`（用法见其 README）。新会话/新环境接手，先读这份再动手。

## 1. fork 改动清单（升级时重点看这几处）

上游基线 = `main` @ `5aa89f3`（2026-10-06 合并，含 #375 watch-time / #376 jav delete / #377 cover 修复 / #379 播放列表）。
fork 自己的提交见 `git log` 里的 `[FORK]` 前缀。

**A. 女优独立高清头像**

| 文件 | 改动 |
|---|---|
| `internal/db/migrations/209901010001_add_jav_idol_avatar.go` | jav_idol 加 `avatar_code`/`avatar_file` 两列 + 索引（**2099 保留号段**，见 §5.1） |
| `internal/models/jav.go` | 对应两个 GORM 字段 |
| `internal/db/jav.go` | idol 查询 SELECT 加 `COALESCE(ji.avatar_code, ...)`，Group 同步 |
| `internal/server/jav_cover_api.go` | `lookupIdolAvatarFile`：`/jav/<idol>/cover` 优先返回独立头像文件 |
| `internal/server/jav_idol_api.go` | `hasIdolAvatarFile`：已有独立头像时防作品封面覆盖 |

**B. 容器模式播放探测修复**（对应 issue #1 / #2）

| 文件 | 改动 |
|---|---|
| `internal/util/video.go` | ffprobe/ffmpeg **候选链回退**；新增 `ErrFFToolMissing` 哨兵错误；仅成功时缓存 |
| `internal/server/video_api.go` | 「工具缺失」改报 **503** 而非误导性的 404 |
| `internal/manager/ffmpeg_tool_manager.go` | 工具路径解析同步候选链 |
| `internal/util/video_test.go` | 上述行为的单元测试 |

**C. 迁移入口加固**

| 文件 | 改动 |
|---|---|
| `internal/db/migrations.go` | `goose.UpContext` 加 `WithAllowMissing()`（与 2099 号段**成对使用**，见 §5.1） |

合计 **10 个 `.go` 文件 / +293 −48**（占官方 Go 代码 0.4%），跟官方升级预期零冲突。

## 2. 资料渠道清单（2026-10-06 实测）

| 渠道 | 可用性 | 给什么 | 说明 |
|---|---|---|---|
| Will 集团 7 厂牌官网（S1/MOODYZ/ATTACKERS/Madonna/E-BODY/OPPAI/Wanz） | ✅ | 官方艺术照 URL + 日文/罗马名 | CDN `cdn.up-timely.com`，470×600~702×900；列表页 `actress/<行>?page=N` 直接带名字+图，无需进详情；**Referer 必须带对应站点** |
| av-wiki.net | ✅ | 生日/身高/三围/别名 | 资料在 `og:description` content 里，**正则打原始 HTML，不能先去标签**；命中 ~87% |
| ja.wikipedia API（langlinks zh） | ✅ | 中文译名 + 改名关系 | 一次 50 标题；结果必须过字形重叠过滤（见 6 号脚本），否则混入「白雪ひめ→光之美少女」类错配 |
| Gfriends（github.com/gfriends/gfriends） | ✅ | 大量演员头像 | Filetree 的 **value 才是文件名，key 会 95% 404**；数据在 fork 的 `/data/gfriends_avatars/<idol_id>.jpg` |
| pics.dmm.co.jp | ✅ | 图床 | 直连/代理都通，样品图唯一可靠图床 |
| javdb 网页 | ⚠️ | 中文译名/本名 | 页面可访问但**图床 c0.jdbstatic.com 完全不通**；搜索页第一个 /actors/ 条目常是"相似名"，必须遍历全部条目精确匹配 |
| xslist / minnano-av / javlibrary | ❌ | — | Cloudflare 403（代理出口是 AWS 新加坡 IP，被 CF 无差别挑战，换 UA/指纹无效，只能换出口） |
| SOD / Prestige / idea-pockets / kawaii / KMP | ❌ | — | 503（squid 上游不承载）或 000；素人系头像无法补 |
| avdbs / jdbstatic | ❌ | — | 连接不通 |

## 3. 头像替换判据（核心算法）

前端 `JavIdolGrid.jsx`：`IDOL_COVER_VISIBLE_RATIO=0.47` → 裁切窗宽高比 **0.6989**（(800×0.47)/538）。
图按 **h-full 渲染、横向裁切**，所以：

```
有效像素 eff(w,h) = h × min(w, h × 0.6989)
```

- **横图一半宽度被浪费**：1200×741 的横图 eff≈518k，不如 702×900 竖图 eff≈629k。
- **替换阈值：eff(new)/eff(old) ≥ 1.15 才换**；≤0.87 保留（绝不降级）。
- 新增头像写 `avatar_code='HD-<idol_id>'`、`avatar_file='/app/data/gfriends_avatars/<id>.jpg'`（注意容器内前缀是 `/app/data`，宿主机是 `/share/.../javboss/data`）。
- 裁切偏移（Gfriends 图人像居左时）：`cropLeft=(A−0.6989)/(2A)`，A=图宽高比。

## 4. 踩坑手册 Top 12（按浪费时间的程度排序）

1. **本地写 .sh 必须 LF**：`open(...,"w")` 默认 CRLF → 远端 `/bin/sh` 报 bad interpreter。用 `newline="\n"` 或 BytesIO。
2. **paramiko `exec_command` 不阻塞**：必须 `o.read()` 等命令结束，否则 cp 与 sftp.put 竞态、备份是坏的。
3. **`sftp.get()` 远端文件不存在时在本地留 0 字节文件**：`os.path.exists` 通过但 `json.load` 崩——下载产物用前查 `getsize()>100`。
4. **QNAP dockerd 代理**：走 supervisord 包装脚本 `run-docker-proxy.sh`（勿用 daemon.json proxies → Fatal；勿用 supervisord environment → NO_PROXY 逗号破坏解析）。容器内程序访问外网必须自己带 `HTTP_PROXY/HTTPS_PROXY` env，dockerd 的代理不传给容器。
5. **DNS 污染**：`registry-1.docker.io`/`auth.docker.io` 被解析到 Facebook IP；一切外网访问（含容器内抓取）走局域网正向代理（`PROXY_URL`）。busybox wget 不读 HTTPS_PROXY，测连通用 curl。
6. **distroless 容器（javboss）**：`docker exec` 进不去（无 sh）。诊断姿势：宿主机 bind mount 目录直接看文件 + HTTP API（登录 `POST /auth/login` body `{"password":...}` 用 cookie 会话，不返回 token）；改 sqlite 用 alpine 容器挂 /data + apk 装 python3。
7. **QNAP 上改 sqlite 的引号地狱**：`%` 经 shell 会被吃（`LIKE '%x%'` 报错）、反引号被替换——SQL 一律写进 .py 文件经容器执行，别拼命令行。
8. **Container Station GUI 下载镜像**：填全名（ghcr.io/...）；日志在 `/var/log/container-station/`。存在无代理残留 ctstation 实例导致 GUI 偶发卡顿，但不影响 `docker pull`。
9. **`jav_idol` 与 video 不直接关联**：经 `video_location(video_id, jav_id)`，同一 video 可挂多条 jav；按 jav 行判断会误判，必须 `GROUP BY video_id` 聚合。`jav_scrape_override=':skip'` 锁定防重刮。
10. **`executemany` 后 `SELECT changes()` 只算最后一行**——统计一律独立 `COUNT(*)`。
11. **打印 DB 行先对 PRAGMA 列名**：列序号错位会造成"birth_date 显示名字"的假象，白查一轮。
12. **av-wiki 全量 917 人 ≈ 33 分钟**（4 并发 + 0.8-2s 延迟 + 30s 超时重试），任务要 `run_in_background`，别前台干等超时。

## 5. 跟官方升级流程

```bash
cd JavBoss-src
git remote add upstream https://github.com/Solr159/JavBoss.git   # 首次
git fetch upstream
git log --oneline main..upstream/main        # 看官方新提交
git merge upstream/main                       # 预期零冲突（fork 仅 10 文件 +293/−48）
```

> ⚠️ **每次合并后必须复查迁移**：上游若新增迁移文件，先 `ls internal/db/migrations/ | tail` 确认
> **没有与 fork 的 `2099` 号段撞号**，再确认 `internal/db/migrations.go` 里的
> `WithAllowMissing()` 还在（它是 2099 号段能正常工作的前提，见 §5.1）。

冲突检查重点：`internal/db/jav.go`（官方若改 idol 查询的 SELECT/Group 需手工合入 COALESCE）、`jav_cover_api.go` / `jav_idol_api.go`（官方若重写这两个接口需重放 lookupIdolAvatarFile/hasIdolAvatarFile）、
`internal/util/video.go` / `internal/server/video_api.go`（官方若改播放探测或错误分类，需重放候选链回退与 `ErrFFToolMissing` 503 分类）。

### 5.1 ⚠️ 迁移版本号：fork 用 `2099` 号段 + `WithAllowMissing`（必须成对）

**goose 用「文件名数字前缀」作为迁移的唯一标识**（`AddNamedMigrationContext` → `NumericComponent(name)`）。
两个迁移文件只要数字前缀相同，就会在全局注册表里**互相覆盖**，其中一个被静默跳过。

这不是理论风险——2026-10-06 合并上游 main 时就真实撞上了：

| | 版本号 | 文件 |
|---|---|---|
| fork（≤ 2.1.3） | `202610040001` | `add_jav_idol_avatar.go`（女优头像两列） |
| 上游 #375 | `202610040001` | `add_watched_time.go`（watched_ms 两列） |

**规则**：fork 自己的迁移一律用 `2099xxxxxxxx` 号段（现为 `209901010001_add_jav_idol_avatar.go`），
与上游的日期号段彻底隔离，以后不会再撞号。新增 fork 迁移时挑一个 2099 段里没被占用的号即可。

#### 配套约束：`goose.WithAllowMissing()` 必须一起用

2099 排在一切上游日期号段之后，于是**上游后续新增的迁移版本号恒小于 `209901010001`**。
goose 默认把这些「版本号低于已应用最高版本、但没执行过」的迁移判为
`found N missing migrations before current version X`，并**直接报错退出**（容器 `Restarting (1)`）。
开了 `WithAllowMissing()` 后 goose 会把它们放进 `migrationsToApply` 正常补跑
（依据 `goose/up.go`：`if option.allowMissing { migrationsToApply = missingMigrations }`）。

所以 `internal/db/migrations.go` 里这一行是**刚需**，删掉 = 上游下次发版后容器起不来：

```go
return goose.UpContext(ctx, db, migrationDir, goose.WithAllowMissing())
```

前提是所有迁移幂等 —— 本项目全部走 `addColumnIfMissing` / `CREATE INDEX IF NOT EXISTS` /
`columnExists` 早退，重复执行无副作用。

#### 从 ≤ 2.1.3 升级时的一次性清理

旧库把 `202610040001` 记成「已执行」（那其实是 fork 头像迁移的行），于是上游的
`202610040001_add_watched_time` 被误判为已应用而**跳过**，`watched_ms` 列根本建不出来 →
上游 watch-time 功能直接报 SQL 错误。

```sql
-- 只在这一行确实是残留（video.watched_ms 缺失）时才删；先备份 DB、先停容器（防 WAL 竞态）
DELETE FROM goose_db_version WHERE version_id = 202610040001;
```

删完由 `WithAllowMissing` 自动补跑该迁移（幂等）。
工具 `scripts/maintenance/11_deploy_fork_image.py` 已内置判据：**先查 `video.watched_ms` 是否存在**，
缺失才删、存在则保留 —— 所以重复部署是安全的空操作。

> **踩坑实录：删行与重启的顺序会要命。** 若 dbMax 已经是 `209901010001` 再回头删
> `202610040001`，**没有 allowMissing 的旧镜像**会卡死在
> `found 1 missing migrations before current version 209901010001`。
> 2026-10-06 因此抢修过一次（手工补 `watched_ms` 两列 + 补回版本行）。正解就是本节的 allowMissing。

重新编译部署（NAS 上没有 Go/node 环境，一律用容器）：

- **全量重建镜像（推荐，跟上游大版本升级时用）**：直接在源码目录跑官方 `Dockerfile`，
  四段式（前端 node 构建 + Go 构建 + 下载静态 ffmpeg + distroless 底座）：

  ```bash
  docker build -t javboss-fork:<新tag> .
  ```

  这样前端 `web/dist` 会跟着上游前端改动一起更新——**上游改前端时必须走这条路**，
  只覆盖二进制层会导致新功能的 UI 缺失。

- **只改 Go 源码时（增量）**：`scripts/maintenance/10_build_fork_image.py`
  （`FROM <上一版镜像>` + `COPY javboss /app/javboss`，省一次前端构建）。

> ⚠️ **底座版本必须与二进制源码同版本**（增量构建路线）：fork 镜像 =「本地编译的二进制 + 官方镜像底座」。
> 若二进制来自 v2.1.1 而底座是 v2.1.0，会出现 ffmpeg/ffprobe 路径失配（底座 `/usr/local/bin` vs 代码硬编码 `/app/internal/bin`）→ 播放全线报「视频文件或所在目录不存在」。**见第 9 节**。
> 走全量构建路线就没有这个耦合。

**验证清单**（升级后必做）：
1. `go version -m <二进制>` 看 `vcs.revision`——判版本的硬证据，别信 tag。
2. 容器 Up + 首页 200 + `GET /jav/GFAV-1/cover` 200（独立头像路由）+ `GET /jav/idols` 带 cookie 200。
3. **`GET /videos/<id>/streams` = 200**（少了这步，ffprobe 失配会静默潜伏到主人点播放才发现）。
4. sqlite 抽查：`avatar_file` 总数、`avatar_code LIKE 'HD-%'` 数、中文名/罗马名/生日填充率（对比本手册第 7 节基线）。
5. 确认 `web/dist` 未被官方构建覆盖（fork 前端零改动，但官方新版本可能重打前端）。

## 6. 备份与回滚

| 内容 | 位置 |
|---|---|
| 镜像 tar / 源码 tar.gz / patch / 结果文档 | NAS `/share/CACHEDEV1_DATA/Backup/javboss/` |
| 头像替换前原图（392 张） | `Backup/javboss/avatars-before-hd-20261006/` |
| DB 备份命名 | `javboss.db.bak-<用途>-<日期>`（hd / info / lock / fix2）在 `data/` 同目录 |

回滚：停容器 → `cp javboss.db.bak-xxx javboss.db` →（头像回滚：从备份目录拷回 `<id>.jpg`）→ 启容器。**改库前必须先备份，容器必须先停**（防 wal 竞态）。

## 7. 数据基线（2026-10-06 落库后）

- 演员 1456 人：日文名 100% / 罗马名 1015 (70%) / 中文名 391 (27%，中文译名现实上限) / 生日 1306 (90%) / 身高·三围 ~1145 (79%)。
- 头像 1288 张（Gfriends 1254 中 392 张升级为厂牌艺术照 + 新增 34 张 HD-）；另有 620 人属素人/封禁厂牌，无图可补。
- `jav_idol_alias` 11 条（维基判定的改名关系）；`video.jav_scrape_override=':skip'` 锁 7627 条防重刮。
- NAS crontab：`30 4 * * *` 清 `.@__thumb`、`40 4 * * *` fix_sample_thumbs.sh。

## 8. 样品图排障（2026-10-06 实战沉淀）

- **渲染机制**：前端 `JavSampleImageGrid` 不用 DB 原始 URL，只按元素个数生成 `/jav/items/<id>/sample-images/<idx>/thumbnail|detail`，由后端 `getJavSampleImage`（`internal/server/jav_sample_image_api.go`）代抓并 `Cache-Control: private, max-age=86400`。→ **破图充要条件 = NAS 后端抓不到源 URL（返 502）**；排查只看 NAS 侧连通性，别被浏览器行为带偏。
- **图床可用性（NAS 视角）**：`pics.dmm.co.jp` / `awsimgsrc.dmm.co.jp` / `image.mgstage.com` ✅；`c0.jdbstatic.com`（javdb 图床）/ `www.javbus.com/pics` ❌（CF 挡 NAS 直连与代理两条路，DNS/TLS 正常但拿不到响应）。
- **DMM 占位图判别（关键）**：无图 URL 返回 **302 → `now_printing`**，有图直接 200。占位图是合法 image，能骗过 `resolveJavSampleImages` 的验证 → 全库排查法：对每作品第一张 URL 测状态码（不跟随重定向），非 200 的作品其样图全为占位，`sample_images` 置空即可（前端不渲染该区块）。工具：`check_noimage.py`（10 线程全库约 4 分钟）；`code=0` 要重试 3 次再判死（可能是网络抖动）。
- **修图流程**：① 备份 DB ② 清空问题作品 `sample_images` ③ `POST /jav/items/<id>/sample-images` 触发重刮（provider 顺序 JavMenu→JavBus，**写回前会在 NAS 实测下载 detail 验证**，所以重刮回来的 detail 天然可达）④ 跑 `fix_sample_thumbs.py` 把 thumbnail 对齐 detail（BAD 列表=javbus/javdb/javmoo/javmenu/xcity/jdbstatic）⑤ 终扫 + 代理路由抽样。
- DMM cid 不能靠猜：标准规则=番号小写补零 5 位（`ssni00272`），但 HODV 等带数字前缀（`5642hodv22044`）；DMM 搜索页对脚本返回 307。**用 resolve 接口重刮是唯一可靠路径**。

## 9. 播放故障「视频文件或所在目录不存在」（2026-10-06 实战）

> **状态（2026-10-06 晚更新）：代码根因已在 fork 中修复，镜像 `javboss-fork:2.1.3` 起生效**（提交 `ad880f4`）。
> - 容器模式不再只认 `/app/internal/bin`，改为候选链 `env(FFPROBE_PATH/FFMPEG_PATH)` → `/app/internal/bin` → `/usr/local/bin` → `PATH`
> - 「工具缺失」不再被误报成 404，改为 **503「缺少浏览器播放所需组件」**（新增哨兵错误 `util.ErrFFToolMissing`，并把该判定排到 `os.ErrNotExist` 之前）
> - `ResolveFFprobePath` 改为**仅成功时缓存**，补齐工具后**无需重启进程**即可恢复
>
> **进一步（2026-10-06 深夜，`javboss-fork:2.2.0` 起）**：跟上游 main 升级时改为
> **按官方 `Dockerfile` 全量构建**（不再用「官方底座 + 覆盖二进制」的增量做法），
> ffprobe/ffmpeg 由 Dockerfile 放进 `/app/internal/bin/`，**「底座版本 ≠ 源码版本」这个耦合根本不存在了**。
> 候选链回退仍然保留，作为对自行改造镜像者的兜底。
>
> 因此下面这套**运行时规避手段仅适用于 ≤ v2.1.2 的历史镜像**；排障思路（判别方法）仍然通用。

**症状**：网页点开视频 → 提示「视频文件或所在目录不存在」，但文件确实在（`ls` 可见、挂载正常）。

**真因（三层，缺一不可）**
1. JavBoss 容器模式下 ffprobe 路径**硬编码**：`internal/util/video.go` 的 `ContainerFFBinaryDir = "/app/internal/bin"`，`runtimeconfig.ContainerMode()` 为真时（`JAVBOSS_CONTAINER=1`）**只认 `lookup("/app/internal/bin/ffprobe")`，完全忽略 `FFPROBE_PATH` / `FFMPEG_PATH` 环境变量**。
2. 官方镜像布局**随版本变过**：vX 早期把 ffmpeg/ffprobe 放 **`/usr/local/bin/`**（镜像 env 里 `FFPROBE_PATH=/usr/local/bin/ffprobe` 就是那代的痕迹）；v2.1.1 的 `Dockerfile` 改成 `COPY --from=ffmpeg-build /ffprobe ./internal/bin/ffprobe`（即 `/app/internal/bin/`）。
3. fork 镜像是「**v2.1.1 源码编的二进制** + **v2.1.0 官方镜像底座**」→ 二进制找 `/app/internal/bin/ffprobe`，底座里只有 `/usr/local/bin/ffprobe` → 找不到。
   `ProbePlaybackSupport` 抛出的 `os.ErrNotExist` 又被 `respondPlaybackError` 的 switch **优先命中 `case errors.Is(err, os.ErrNotExist)`**（排在 `strings.Contains(err, "ffprobe not found")` 的 503 分支之前）→ 被误分类成 404「视频文件或所在目录不存在」。**这是官方错误分类 bug，专门把人往"文件丢了/挂载坏了"的错方向带。**

**一句话判别**（别再去查文件、挂载、DB 路径）
```bash
$D logs --tail 200 javboss | grep 'probe playback support error'
# probe playback support error: ffprobe unavailable in Docker image at /app/internal/bin/ffprobe: ...
```
佐证：同一作品的 `GET /videos/<id>/stream` = **206（直连能出数据）**，而 `GET /videos/<id>/streams` = **404**。两者矛盾 ⇒ 一定是探测/工具缺失，不是文件问题。

**修复**（脚本：`scripts/maintenance/8_fix_ffprobe.py`，`--check` 只检测）
```sh
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
# 1) 从底座镜像取出 ffprobe/ffmpeg；本地备成 img/internal/bin/{ffprobe,ffmpeg}
$D create --name tmpff ghcr.io/solr159/javboss:v2.1.0   # 容器名不能以 _ 开头！
$D cp tmpff:/usr/local/bin/ffprobe ./img/internal/bin/ffprobe
$D cp tmpff:/usr/local/bin/ffmpeg  ./img/internal/bin/ffmpeg
$D rm -f tmpff
# 2) 补进运行中的容器（/app/internal 不存在时用「拷父目录」写法，docker cp 会连目录一起建）
$D cp ./img/internal javboss:/app/
# 3) 重启才生效 —— ResolveFFprobePath 用 sync.Once 缓存，进程不重启永远拿旧结果！
$D restart javboss
# 4) 固化：commit 成带 ffprobe 的新镜像，重建容器时用它
$D commit javboss javboss-fork:2.1.2
```

**根治原则**：**fork 镜像的底座必须与二进制源码同版本**。用 v2.1.1 二进制就配官方 v2.1.1 镜像（或按官方 v2.1.1 Dockerfile 全量构建）；沿用 v2.1.0 底座就必须自己补 `/app/internal/bin/{ffprobe,ffmpeg}`。重编镜像后**必测** `/videos/<id>/streams` 是否为 200。

**顺手记录的两个坑**
- QNAP `/tmp` 是 **64MB tmpfs**，极易写满。表现极具误导性：`docker cp` 反查报 `no space left on device`、curl 的 `-c cookie.jar` 悄悄写失败 → 后续请求 **401「需要登录后才能继续」**（看着像认证问题，其实是磁盘满）。诊断产物一律放 `/share/...`。
- 容器名**不能以下划线开头**（`Invalid container name`），且这类错误极易被 `>/dev/null 2>&1` 吞掉，排查时先去掉重定向。

## 10. 容器重建命令模板（配置对齐版，2026-10-06 实战）

**何时用**：换镜像 tag、清运行态残留、修配置漂移。中断服务约 15 秒。

**重建前**（存档 + 检查目标镜像自包含，避免又踩第 9 节的 ffprobe 坑）：
```sh
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
$D inspect javboss > /share/CACHEDEV1_DATA/Backup/javboss/javboss-inspect-$(date +%Y%m%d).json
$D create --name chk javboss-fork:2.2.1          # 容器名不能以 _ 开头！
$D cp chk:/app/internal/bin/ffprobe /share/chk_ffprobe && ls -l /share/chk_ffprobe   # 应 ~48MB
$D rm -f chk
```

**重建**（参数逐条对齐 `docker inspect` 的结果，别凭记忆写）：
```sh
$D stop javboss && $D rm javboss
$D run -d --name javboss \
  --network host --restart unless-stopped \
  -e JAVBOSS_CONTAINER=1 \
  -e JAVBOSS_PROXY_HOST_GATEWAY=1 \
  -e JAVBOSS_HOST_PATH_PREFIX=1 \
  -e JAVBOSS_DISABLE_MPV=1 \
  -e JAVBOSS_DISABLE_DESKTOP_INTEGRATION=1 \
  -e JAVBOSS_USE_FFMPEG_SCREENSHOTS=1 \
  -e HTTP_PROXY=http://192.168.2.175:3128 \
  -e HTTPS_PROXY=http://192.168.2.175:3128 \
  -e NO_PROXY=localhost,127.0.0.1,192.168.2.0/24 \
  -e FFPROBE_PATH=/app/internal/bin/ffprobe \
  -e FFMPEG_PATH=/app/internal/bin/ffmpeg \
  -e TZ=Asia/Shanghai \
  -v /share/CACHEDEV1_DATA/Container/javboss/data:/app/data \
  -v /:/host:ro \
  javboss-fork:2.2.1 ./javboss -port 8655
```

> **优先用 `scripts/maintenance/11_deploy_fork_image.py`**：它会从 `docker inspect` 现读当前容器的真实配置再重建，
> 不会因为漏写某个环境变量而踩坑；上表是手工重建时对照用。

**重建后必验（三步）**：
```sh
$D ps --filter name=javboss --format '{{.Image}} {{.Status}}'   # Up
python3 scripts/maintenance/9_verify_playback.py --deep         # 全量应 2243/2243 全 200
$D logs javboss | grep -c 'probe playback support error'        # 必须为 0
```

**四个漏了就出问题的环境变量**：

| 变量 | 值 | 漏了会怎样 |
|---|---|---|
| `JAVBOSS_HOST_PATH_PREFIX` | `1` | DB 存错路径 → 播放报「视频文件或所在目录不存在」 |
| `JAVBOSS_CONTAINER` | `1` | 容器模式判定失效，ffprobe/路径逻辑走宿主分支 |
| `HTTP_PROXY` / `HTTPS_PROXY` | squid `192.168.2.175:3128` | 刮削外网超时（NAS DNS 被污染） |
| `TZ` | `Asia/Shanghai` | 日志与时间显示错位 |

**配置基线**：`/share/CACHEDEV1_DATA/Container/javboss/docker-compose.yml` 已于 2026-10-06 对齐（当前 `javboss-fork:2.1.3`；旧版备份 `Backup/javboss/docker-compose.yml.bak-20261006`）。当前运行容器由 `docker run` 建立，参数与该文件一致；用 compose 重建前必须先 `stop` + `rm` 现有容器（同名冲突）。**改 tag 时记得同步更新该文件。**

**数据目录整洁**：一次性调试脚本禁止长期留在 `Container/javboss/data/`（那是 `/app/data`，会随备份一起膨胀）。走 `scripts/maintenance/`，产物放 `/share/.../Backup/`。

## 11. 安全红线

- **凭据只走环境变量**（`NAS_PASS` 等，见 scripts/maintenance/nas_env.py），任何脚本/文档/提交里不得出现明文密码。
- `scripts/maintenance/artifacts/` 含 DB 快照（全库数据），已 gitignore，**严禁提交/上传**。
- GitHub 推送用 SSH key；密码已在会话中暴露过的账号应改密并改用 PAT。

## 12. 发版清单（每个 fork 版本迭代必做，一步都不许省）

> 这是**硬性流程**：从 `2.2.1` 之后，每次出新镜像都必须走完本清单。
> 版本沿革正文写在 `docs/CHANGELOG.md`（条目格式照抄现有版本）。

1. **定 tag**：新版本 = 上一版 tag + 0.0.1；**tag ↔ 源码必须一一对应**，绝不复用旧 tag 装新代码（2.1.x 时代「底座≠源码」的教训，见 §9）。
2. **验证先行**：`9_verify_playback.py --deep` 全量可播数 = 总数（当前基线 2243），异常 0；不达标不许发布。
3. **写 CHANGELOG**：`docs/CHANGELOG.md` 顶部插入新条目 + 更新「镜像 tag ↔ 底座 ↔ 二进制源码 对照表」，必含：源码 commit、构建方式（全量/增量）、改了什么/为什么、修复的 issue、验证数字。
4. **同步文档基线**：README 徽章与部署命令里的镜像 tag、`maintenance-zh.md` / `fork-features-zh.md` 里的版本引用、10 号脚本 `NEW_TAG` 默认值，全部对齐新 tag。
5. **提交并推送**：`git add -A && git commit`（`[FORK]` 前缀）→ `git push fork main` → 回读 GitHub raw 确认。
6. **issue 收尾**：本版本修复的问题，对应 issue 关闭（`state_reason=completed` + `fixed` 标签）+ 归档评论（验证数字、镜像 tag、commit）。
7. **记录基线**：NAS `Container/javboss/docker-compose.yml` 的 `image:` 同步新 tag（旧版备份到 `Backup/javboss/`）；工作区记忆（`.workbuddy/memory/`）追加当日条目。
