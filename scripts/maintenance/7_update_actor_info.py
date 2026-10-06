# -*- coding: utf-8 -*-
"""步骤 7：演员资料落库（写操作！默认预览，APPLY=1 才写）。

汇总三个来源，只填【当前为空】的字段，绝不覆盖已有值：
  - japanese_name ← name（name 本身即日文名，零成本回填）
  - roman_name     ← 厂牌索引（步骤 2，姓→名顺序翻转+首字母大写）
  - birth_date/height_cm/bust/waist/hips ← av-wiki（步骤 5）
  - chinese_name   ← 维基过滤计划（步骤 6）
  - jav_idol_alias ← 步骤 6 判定的「改名/别名」关系
"""
import json
import os
import re
import sqlite3
import time

from match_util import norm_jp, norm_rom
from nas_env import ART, BKROOT, DATA, WORK, connect, docker_python, put_json, put_script, sh

APPLY = r'''# -*- coding: utf-8 -*-
import json, sqlite3
UPD = json.load(open("/data/_probe/actor_updates.json", encoding="utf-8"))
try:
    ALI = json.load(open("/data/_probe/actor_aliases.json", encoding="utf-8"))
except Exception:
    ALI = []
con = sqlite3.connect("/data/javboss.db")
cur = con.cursor()
n = 0
field_cnt = {}
for sid, fields in UPD.items():
    i = int(sid)
    sets = ", ".join(f"{k}=?" for k in fields)
    cur.execute(f"UPDATE jav_idol SET {sets}, updated_at=datetime('now') WHERE id=?",
                list(fields.values()) + [i])
    n += cur.rowcount
    for k in fields:
        field_cnt[k] = field_cnt.get(k, 0) + 1
con.commit()
alias_n = 0
for a in ALI:
    cur.execute("SELECT 1 FROM jav_idol_alias WHERE jav_idol_id=? AND alias=?", (a["id"], a["alias"]))
    if cur.fetchone():
        continue
    cur.execute("INSERT INTO jav_idol_alias (jav_idol_id, alias, created_at) VALUES (?,?,datetime('now'))",
                (a["id"], a["alias"]))
    alias_n += 1
con.commit()


def cnt(col):
    return cur.execute(
        f"SELECT COUNT(*) FROM jav_idol WHERE {col} IS NOT NULL AND TRIM(CAST({col} AS TEXT))<>''").fetchone()[0]


tot = cur.execute("SELECT COUNT(*) FROM jav_idol").fetchone()[0]
print(f"更新行数 {n}，别名插入 {alias_n}")
print("字段更新分布:", json.dumps(field_cnt, ensure_ascii=False))
print(f"总人数 {tot}")
print(f"现在：中文名 {cnt('chinese_name')} | 日文名 {cnt('japanese_name')} | 罗马名 {cnt('roman_name')} "
      f"| 生日 {cnt('birth_date')} | 身高 {cnt('height_cm')} | 胸围 {cnt('bust')} | 腰围 {cnt('waist')} | 臀围 {cnt('hips')}")
con.close()
'''


def main():
    apply_mode = os.environ.get("APPLY") == "1"
    PH = ART
    con = sqlite3.connect(os.path.join(ART, "javboss.db"))
    rows = {r[0]: {"name": r[1], "roman": r[2], "jp": r[3], "cn": r[4],
                   "h": r[5], "bd": r[6], "bust": r[7], "waist": r[8], "hips": r[9]}
            for r in con.execute("""SELECT id,name,COALESCE(roman_name,''),COALESCE(japanese_name,''),
                                    COALESCE(chinese_name,''),height_cm,COALESCE(birth_date,''),
                                    bust,waist,hips FROM jav_idol""")}
    con.close()

    updates = {}

    def setf(i, f, v):
        if v in (None, "", 0):
            return
        updates.setdefault(i, {})[f] = v

    # 1) japanese_name ← name
    for i, r in rows.items():
        if not r["jp"] and r["name"]:
            setf(i, "japanese_name", r["name"])

    # 2) roman_name ← 厂牌索引（只填空的）
    idx = json.load(open(os.path.join(PH, "actress_index.json"), encoding="utf-8"))
    by_jp = {}
    for it in idx:
        if norm_jp(it["jp"]):
            by_jp.setdefault(norm_jp(it["jp"]), it)
    rom_filled = 0
    for i, r in rows.items():
        if r["roman"]:
            continue
        it = by_jp.get(norm_jp(r["name"]))
        if it and it.get("romaji"):
            parts = [p for p in it["romaji"].split() if p]
            if len(parts) == 2:
                val = f"{parts[1].capitalize()} {parts[0].capitalize()}"
            else:
                val = " ".join(p.capitalize() for p in parts)
            setf(i, "roman_name", val)
            rom_filled += 1
    print("罗马名补充:", rom_filled)

    # 3) av-wiki 资料（坑：sftp.get 可能留下 0 字节文件，用前必须查大小）
    av_path = os.path.join(PH, "avwiki_full.json")
    av_hit = 0
    if os.path.exists(av_path) and os.path.getsize(av_path) > 100:
        for r in json.load(open(av_path, encoding="utf-8")):
            i = r["id"]
            if i not in rows:
                continue
            if not rows[i]["bd"] and r.get("birth_date"):
                setf(i, "birth_date", f'{r["birth_date"]} 00:00:00+00:00')
                av_hit += 1
            for f, k in (("height_cm", "height"), ("bust", "bust"), ("waist", "waist"), ("hips", "hips")):
                if rows[i].get(k) in (None, 0, "") and r.get(k):
                    try:
                        setf(i, f, int(r[k]))
                    except ValueError:
                        pass
        print("av-wiki 命中记录:", av_hit)
    else:
        print("（无有效的 avwiki_full.json，跳过；先跑 5_enrich_avwiki.py）")

    # 4) chinese_name ← 维基过滤计划
    zh_path = os.path.join(PH, "wiki_zh_plan.json")
    cn_filled = 0
    if os.path.exists(zh_path) and os.path.getsize(zh_path) > 50:
        plan = json.load(open(zh_path, encoding="utf-8"))
        for c in plan["clean"]:
            i = c["id"]
            if i not in rows or rows[i]["cn"]:
                continue
            zh = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", c["zh"]).strip()
            if not re.search(r"[\u4e00-\u9fff]", zh):
                continue
            setf(i, "chinese_name", zh)
            cn_filled += 1
        print("中文名补充:", cn_filled)
    else:
        print("（无 wiki_zh_plan.json，跳过；先跑 6_wiki_zh_names.py）")

    # 5) 别名表
    aliases = []
    if os.path.exists(zh_path) and os.path.getsize(zh_path) > 50:
        plan = json.load(open(zh_path, encoding="utf-8"))
        for a in plan.get("alias", []):
            if a["id"] in rows:
                aliases.append({"id": a["id"], "alias": a["zh"], "src": "wiki-zh"})
    print("别名表新增:", len(aliases))
    print(f"共涉及 {len(updates)} 位演员，字段更新 {sum(len(v) for v in updates.values())} 处")

    cli = connect()
    if not apply_mode:
        print("（预览模式。加 APPLY=1 环境变量执行写库）")
        cli.close()
        return

    ts = time.strftime("%Y%m%d")
    sftp = cli.open_sftp()
    put_json(sftp, {str(k): v for k, v in updates.items()}, f"{WORK}/actor_updates.json")
    put_json(sftp, aliases, f"{WORK}/actor_aliases.json")
    put_script(sftp, APPLY, f"{WORK}/apply_info.py")
    sftp.close()

    out, err = sh(cli, f"cd {DATA} && cp javboss.db javboss.db.bak-info-{ts} && ls -la javboss.db.bak-info-{ts}")
    print("备份:", out.strip())
    out, err = docker_python(cli, "/data/_probe/apply_info.py", t=900, network=False)
    print(out)
    if err.strip():
        print("stderr:", err[-400:])
    cli.close()
    print(f"DB 备份：{DATA}/javboss.db.bak-info-{ts}（备份根目录 {BKROOT}）")


if __name__ == "__main__":
    main()
