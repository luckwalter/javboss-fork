# -*- coding: utf-8 -*-
"""维护脚本共享配置：凭据一律走环境变量，绝不写死在代码里。

环境变量：
  NAS_HOST      QNAP 地址            默认 192.168.1.10（示例值，请按实际设置）
  NAS_USER      QNAP SSH 用户        默认 admin
  NAS_PASS      QNAP SSH 密码        必填
  NAS_DATA      JavBoss 宿主机数据目录（容器 bind mount 源）
                                     默认 /share/CACHEDEV1_DATA/Container/javboss/data
  NAS_BACKUP    备份根目录           默认 /share/CACHEDEV1_DATA/Backup/javboss
  PROXY_URL     局域网 squid 代理    默认 http://192.168.1.20:3128（示例值）
  JAVBOSS_PASS  JavBoss Web 登录密码 默认 admin

用法：
  export NAS_PASS=xxxx
  python3 0_snap_db.py
"""
import io
import os
import sys

import paramiko

NAS_HOST = os.environ.get("NAS_HOST", "192.168.1.10")
NAS_USER = os.environ.get("NAS_USER", "admin")
NAS_PASS = os.environ.get("NAS_PASS", "")
DOCKER = "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"
DATA = os.environ.get("NAS_DATA", "/share/CACHEDEV1_DATA/Container/javboss/data")
WORK = DATA + "/_probe"
BKROOT = os.environ.get("NAS_BACKUP", "/share/CACHEDEV1_DATA/Backup/javboss")
PROXY = os.environ.get("PROXY_URL", "http://192.168.1.20:3128")
JAVBOSS_PASS = os.environ.get("JAVBOSS_PASS", "admin")

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "artifacts")  # 本地产物目录（json / db 快照），已被 .gitignore 排除


def require_pass():
    if not NAS_PASS:
        sys.exit("[nas_env] 请先 export NAS_PASS=<QNAP SSH 密码>")


def connect():
    """建 SSH 连接（paramiko）。"""
    require_pass()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(NAS_HOST, username=NAS_USER, password=NAS_PASS, timeout=20)
    return cli


def sh(cli, cmd, t=600):
    """执行远端命令并【阻塞等它结束】（read() 必须调用，否则 cp/put 有竞态）。"""
    _, o, e = cli.exec_command(cmd, timeout=t)
    return (o.read().decode("utf-8", "replace"),
            e.read().decode("utf-8", "replace"))


def docker_python(cli, remote_py, t=1200, network=True):
    """在 NAS 上起 alpine 工作容器跑一个 /data 下的 python 脚本。

    QNAP 宿主机没有 python3/sqlite3，这是改库/抓取的标准姿势。
    network=True 时注入 squid 代理（容器内访问外网必须走代理）。
    """
    proxy_env = f"-e HTTP_PROXY={PROXY} -e HTTPS_PROXY={PROXY} -e PROXY_URL={PROXY}" if network \
        else "-e PROXY_URL="
    cmd = (f"{DOCKER} run --rm {proxy_env} "
           f"-e NO_PROXY=localhost,127.0.0.1,192.168.0.0/16 "
           f"-v {DATA}:/data alpine:latest "
           f"sh -c 'apk add --no-cache python3 >/dev/null 2>&1 && python3 -u {remote_py}'")
    return sh(cli, cmd, t)


def put_script(sftp, code, remote_path):
    """上传 python 脚本文本。统一走 BytesIO + utf-8，避免换行/编码问题。"""
    sftp.putfo(io.BytesIO(code.encode("utf-8")), remote_path)


def put_json(sftp, obj, remote_path):
    put_script(sftp, __import__("json").dumps(obj, ensure_ascii=False), remote_path)
