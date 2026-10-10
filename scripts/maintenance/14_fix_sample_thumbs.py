#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JavBoss 样品图自愈（幂等，可重复执行；cron 调用）

维护脚本：对应仓库外 `javboss/fix_sample_thumbs.py` 的**版本化副本**。
部署到 NAS 时通过环境变量指定真实地址与密码，本文件不含任何明文密码 / 内网 IP：

    export JAVBOSS_URL=http://<NAS内网IP>:8655
    export JAVBOSS_PASS=<JavBoss Web 登录密码>

规则：
  1. 元素 thumbnail_url / detail_url 命中「坏源名单」或为空 / :not_found —— 视为坏图。
  2. 坏源名单 = 静态名单 + 动态判定（2026-10-07 起）：
     调后端自带的数据源可用性检测 API（GET /jav/providers，必要时 POST
     /jav/providers/<数字id>/availability 主动探测），探测 status=ok 的 provider
     本轮**从坏名单移除**（解封，如 javbus 哪天解封就自动恢复抓取）；
     其余一律沿用静态名单。任何 API 异常（不可达/登录失败/解析失败）都回退
     纯静态名单 —— 行为只会「少修」，不会「误删」，幂等性不变。
  3. 坏图优先用"同元素 detail_url"补；仍坏则取"同一 code 其它元素的可用 URL"补；
     都补不上则丢弃该元素（前端 qV 会丢弃不合法元素，留着只会破图）。
  4. 补完后 thumbnail == detail，前端 <img src=e.thumbnail_url> 才能正常显示。

背景（为什么有坏名单）：这些源的图片域名 NAS 后端抓不到（502），破图充要条件
就是「后端抓不到源 URL」。静态名单是 2026-10 实测结论；动态判定让它不再过期。
注意：站点可达 ≠ 图床可达（jdbstatic 这类 CDN 可能单独被挡），所以动态判定
只做「解封」方向，静态名单永远保底。
"""
import sqlite3, json, collections, datetime
import os
import urllib.request

DB = "/data/javboss.db"

# ---- 静态坏名单（provider name -> 它名下的 host 片段；保底，永远参与判定） ----
PROVIDER_HOSTS = {
    "javbus":  ("javbus",),
    "javdb":   ("javdb", "jdbstatic"),   # jdbstatic 是 javdb 的图床域名
    "javmoo":  ("javmoo",),
    "javmenu": ("javmenu",),
    "xcity":   ("xcity",),
}

# ---- 动态判定用的 API（后端自带，上游 #357）---- 凭据走环境变量，无明文 ----
API = os.environ.get("JAVBOSS_URL", "http://127.0.0.1:8655")  # 占位；部署时设 JAVBOSS_URL=真实地址
API_PASS = os.environ.get("JAVBOSS_PASS", "admin")            # 占位；部署时覆盖
RESULT_FRESH_SECONDS = 24 * 3600   # last_result 24h 内视为新鲜，不重复探测
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 清代理，内网直连


def _api(cookie, path, method="GET"):
    req = urllib.request.Request(API + path, method=method,
                                 headers={"Cookie": cookie} if cookie else {})
    with _opener.open(req, timeout=25) as r:
        return json.loads(r.read())


def _checked_age(iso):
    """checked_at 距今秒数；解析失败返回一个大数（视为过期）。"""
    try:
        s = str(iso).replace("Z", "+00:00")
        # 纳秒精度 Python 只吃 6 位，截断
        head, _, rest = s.partition(".")
        if "." in s or "+" in rest or "-" in rest[1:]:
            frac, _, tz = rest.partition("+") if "+" in rest else (rest, "", rest)
            frac = "".join(c for c in frac if c.isdigit())[:6]
            s = head + (("." + frac) if frac else "") + (("+" + tz) if tz and "+" not in rest else "")
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
    except Exception:
        return 1 << 30


def dynamic_bad_providers():
    """返回 (判定为坏的 provider name 集合, 说明文字)。任何异常回退静态全坏。"""
    try:
        body = json.dumps({"password": API_PASS}).encode()
        req = urllib.request.Request(API + "/auth/login", data=body,
                                     headers={"Content-Type": "application/json"})
        with _opener.open(req, timeout=15) as r:
            ck = "; ".join(c.split(";")[0] for c in (r.headers.get_all("Set-Cookie") or []))
        if not ck:
            return set(PROVIDER_HOSTS), "login 失败 -> 回退静态名单"
        # 保守起步：静态名单里的 provider 先全部视为坏，只有探测明确 ok 才解封；
        # 不在 provider 列表里的历史源（如 javmoo/xcity）没有证据可达，保持坏。
        bad = set(PROVIDER_HOSTS)
        detail = []
        probed = set()
        for p in _api(ck, "/jav/providers"):
            name = p.get("name")
            if name not in PROVIDER_HOSTS:
                continue
            probed.add(name)
            lr = p.get("last_result") or {}
            if lr.get("status") != "ok" or _checked_age(lr.get("checked_at")) > RESULT_FRESH_SECONDS:
                try:   # 主动探测一次（必须用数字 id）
                    lr = _api(ck, "/jav/providers/%s/availability" % p["id"], "POST")
                except Exception:
                    lr = {}
            if lr.get("status") == "ok":
                bad.discard(name)
                detail.append("%s=ok(解封)" % name)
            else:
                detail.append("%s=%s(坏)" % (name, lr.get("status") or "探测失败"))
        for name in PROVIDER_HOSTS:
            if name not in probed:
                detail.append("%s=不在provider列表(保持坏)" % name)
        return bad, "; ".join(detail)
    except Exception as e:
        return set(PROVIDER_HOSTS), "API 不可达(%s) -> 回退静态名单" % e


bad_providers, dyn_note = dynamic_bad_providers()
BAD = tuple(h for name in bad_providers for h in PROVIDER_HOSTS[name])
print("坏源名单: %s | %s" % (",".join(sorted(BAD)) or "(空)", dyn_note))

con = sqlite3.connect(DB, timeout=60)
cur = con.cursor()
cur.execute("PRAGMA busy_timeout=60000")
rows = cur.execute("SELECT id, code, sample_images FROM jav").fetchall()


def is_bad(u):
    u = (u or "").strip()
    if not u or u == ":not_found":
        return True
    return any(h in u.lower() for h in BAD)


# code -> 可用 URL 池
pool = collections.defaultdict(list)
for rid, code, raw in rows:
    try:
        arr = json.loads(raw)
    except Exception:
        continue
    if not isinstance(arr, list):
        continue
    for el in arr:
        if not isinstance(el, dict):
            continue
        t = str(el.get("thumbnail_url") or "").strip()
        d = str(el.get("detail_url") or "").strip()
        if not is_bad(t) and not is_bad(d) and d and d not in pool[code]:
            pool[code].append(d)

changed = replaced = deleted = 0
bad_left = 0
for rid, code, raw in rows:
    try:
        arr = json.loads(raw)
    except Exception:
        continue
    if not isinstance(arr, list) or not arr:
        continue
    new = []
    hit = False
    for el in arr:
        if not isinstance(el, dict):
            continue
        t = str(el.get("thumbnail_url") or "").strip()
        d = str(el.get("detail_url") or "").strip()
        if not is_bad(t) and not is_bad(d):
            new.append(el)
            continue
        cand = None
        if not is_bad(d):
            cand = d
        elif code in pool and pool[code]:
            cand = pool[code][0]
        if cand:
            el["thumbnail_url"] = cand
            el["detail_url"] = cand
            replaced += 1
            hit = True
            new.append(el)
        else:
            deleted += 1
            hit = True
    if hit:
        cur.execute("UPDATE jav SET sample_images=? WHERE id=?",
                    (json.dumps(new, ensure_ascii=False), rid))
        changed += 1
        for el in new:
            if is_bad(str(el.get("thumbnail_url") or "")):
                bad_left += 1

con.commit()
cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
con.close()
print("%s changed_rows=%d replaced=%d deleted=%d bad_left=%d" % (
    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), changed, replaced, deleted, bad_left))
