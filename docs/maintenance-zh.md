# JavBoss fork 维护手册（资料补全 · 头像管理 · 跟官方升级）

> 本手册沉淀 2026-10-04 ~ 10-06 两轮实战（Gfriends 头像替换、高清艺术照替换、资料补全）的全部可复用知识。
> 配套工具在 `scripts/maintenance/`（用法见其 README）。新会话/新环境接手，先读这份再动手。

## 1. fork 改动清单（升级时重点看这 5 处）

基线 = 官方 v2.1.1（commit fde33e4），fork commit 见 `git log`（`[FORK]` 前缀）：

| 文件 | 改动 |
|---|---|
| `internal/db/migrations/202610040001_add_jav_idol_avatar.go` | jav_idol 加 `avatar_code`/`avatar_file` 两列 + 索引 |
| `internal/models/jav.go` | 对应两个 GORM 字段 |
| `internal/db/jav.go` | idol 查询 SELECT 加 `COALESCE(ji.avatar_code, ...)`，Group 同步 |
| `internal/server/jav_cover_api.go` | `lookupIdolAvatarFile`：`/jav/<idol>/cover` 优先返回独立头像文件 |
| `internal/server/jav_idol_api.go` | `hasIdolAvatarFile`：已有独立头像时防作品封面覆盖 |

与官方 diff 共 5 文件 +98/−2（占官方 Go 代码 0.15%），跟官方升级预期零冲突。

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
5. **DNS 污染**：`registry-1.docker.io`/`auth.docker.io` 被解析到 Facebook IP；一切外网访问（含容器内抓取）走 squid `192.168.2.175:3128`。busybox wget 不读 HTTPS_PROXY，测连通用 curl。
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
git merge upstream/main                       # 预期零冲突（fork 仅 5 文件 +98/−2）
```

冲突检查重点：`internal/db/jav.go`（官方若改 idol 查询的 SELECT/Group 需手工合入 COALESCE）、`jav_cover_api.go` / `jav_idol_api.go`（官方若重写这两个接口需重放 lookupIdolAvatarFile/hasIdolAvatarFile）。迁移文件是新增文件，不会冲突。

重新编译部署（NAS 上无 Go 环境，用容器编译）：

```bash
# 源码上传到 NAS /share/.../javboss/fork/src 后：
docker run --rm -v <FORK>/src:/fork -v <FORK>/build:/out golang:1.25 sh /fork/build.sh
# CGO_ENABLED=1（sqlite 必须）；产物替换镜像（见 Backup/javboss 的 Dockerfile 备份）
```

**验证清单**（升级后必做）：
1. `go version -m <二进制>` 看 `vcs.revision`——判版本的硬证据，别信 tag。
2. 容器 Up + 首页 200 + `GET /jav/GFAV-1/cover` 200（独立头像路由）+ `GET /jav/idols` 带 cookie 200。
3. sqlite 抽查：`avatar_file` 总数、`avatar_code LIKE 'HD-%'` 数、中文名/罗马名/生日填充率（对比本手册第 7 节基线）。
4. 确认 `web/dist` 未被官方构建覆盖（fork 前端零改动，但官方新版本可能重打前端）。

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

## 8. 安全红线

- **凭据只走环境变量**（`NAS_PASS` 等，见 scripts/maintenance/nas_env.py），任何脚本/文档/提交里不得出现明文密码。
- `scripts/maintenance/artifacts/` 含 DB 快照（全库数据），已 gitignore，**严禁提交/上传**。
- GitHub 推送用 SSH key；密码已在会话中暴露过的账号应改密并改用 PAT。
