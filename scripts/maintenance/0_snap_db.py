# -*- coding: utf-8 -*-
"""步骤 0：从 NAS 拉一份 javboss.db 一致性快照到本地 artifacts/（含 wal/shm），只读。

后续所有"本地分析/生成计划"类脚本都基于这份快照，绝不直接改线上库。
"""
import os

from nas_env import ART, DATA, connect, sh

LOCAL = ART


def main():
    os.makedirs(LOCAL, exist_ok=True)
    cli = connect()

    out, err = sh(cli, f"ls -la {DATA}/javboss.db*")
    print("=== NAS 上的 db 文件 ===")
    print(out, err)

    # 先在 NAS 侧拷到临时目录（保证 db+wal+shm 一致），下载后即删
    out, err = sh(
        f"rm -rf {DATA}/_snap && mkdir -p {DATA}/_snap && "
        f"cp {DATA}/javboss.db {DATA}/_snap/javboss.db && "
        f"cp {DATA}/javboss.db-wal {DATA}/_snap/javboss.db-wal 2>/dev/null; "
        f"cp {DATA}/javboss.db-shm {DATA}/_snap/javboss.db-shm 2>/dev/null; "
        f"ls -la {DATA}/_snap"
    )
    print("=== 快照 ===")
    print(out, err)

    import paramiko  # noqa: F401  (连接已建)
    sftp = cli.open_sftp()
    for fn in ("javboss.db", "javboss.db-wal", "javboss.db-shm"):
        try:
            sftp.stat(f"{DATA}/_snap/{fn}")
        except IOError:
            print(f"跳过 {fn}（不存在）")
            continue
        sftp.get(f"{DATA}/_snap/{fn}", os.path.join(LOCAL, fn))
        print(f"已下载 {fn}  {os.path.getsize(os.path.join(LOCAL, fn)) / 1048576:.1f} MB")
    sftp.close()

    sh(cli, f"rm -rf {DATA}/_snap")  # 清掉远端临时目录
    cli.close()
    print("完成 →", LOCAL)


if __name__ == "__main__":
    main()
