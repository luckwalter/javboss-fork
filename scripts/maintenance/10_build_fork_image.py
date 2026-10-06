#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""10_build_fork_image.py —— 改完 Go 源码后，编译并构建新的 fork 镜像。

流程：
  1) 用 golang 容器编译 fork/src → fork/build/javboss
     （CGO_ENABLED=1，sqlite 需要；首次约 7 分钟，之后有 gocache 会快很多）
  2) 生成两行 Dockerfile.patch：FROM <底座镜像> + COPY javboss /app/javboss
     底座镜像已自带 ffprobe/ffmpeg（见 docs/maintenance-zh.md §9），
     所以只需覆盖二进制，不必重建整条链
  3) docker build -t <新 tag>

环境变量：
  NAS_PASS       必填
  NAS_HOST / NAS_USER
  BASE_IMAGE     底座镜像（默认 javboss-fork:2.2.0，即"上一版可用镜像"）
  NEW_TAG        目标 tag（默认 javboss-fork:2.2.1）
  GO_IMAGE       编译镜像（默认 golang:1.25-bookworm）

用法：
  export NAS_PASS=xxxx
  python3 10_build_fork_image.py                 # 编译 + 构建
  python3 10_build_fork_image.py --skip-build    # 复用已有 fork/build/javboss，只构建镜像

构建完成后：按 docs/maintenance-zh.md §10 的模板重建容器
（务必带 JAVBOSS_HOST_PATH_PREFIX=1 等 4 个变量），再跑 9_verify_playback.py --deep。
"""
import os
import sys

import nas_env

D = nas_env.DOCKER
FORK = nas_env.FORK
BASE_IMAGE = os.environ.get("BASE_IMAGE", "javboss-fork:2.2.0")
NEW_TAG = os.environ.get("NEW_TAG", "javboss-fork:2.2.1")
GO_IMAGE = os.environ.get("GO_IMAGE", "golang:1.25-bookworm")


def main():
    cli = nas_env.connect()
    try:
        if "--skip-build" in sys.argv:
            print("== 1. 跳过编译（--skip-build），复用已有产物 ==")
        else:
            print("== 1. 编译（%s）==" % GO_IMAGE)
            cmd = (
                "{D} run --rm "
                "-v {F}/src:/src -v {F}/build:/out -v {F}/modcache:/go/pkg/mod "
                "-v {F}/gocache:/root/.cache/go-build -v {F}/build.sh:/build.sh:ro "
                "-e CGO_ENABLED=1 -e GOFLAGS=-mod=mod "
                "-e GOPROXY=https://goproxy.cn,direct -e GOSUMDB=off -e GOPATH=/go "
                "{GO} sh /build.sh"
            ).format(D=D, F=FORK, GO=GO_IMAGE)
            out, err = nas_env.sh(cli, cmd, t=2400)
            print(out.strip() or err.strip()[:800])
            if "[build] OK" not in out:
                print("\n[!] 编译未成功（输出里没有 '[build] OK'），终止")
                return 1

        out, _ = nas_env.sh(cli, "ls -l %s/build/javboss" % FORK)
        print("  产物：%s" % out.strip())

        print("\n== 2. 生成 Dockerfile.patch（底座 %s）==" % BASE_IMAGE)
        dockerfile = (
            "# 用 fork 源码编译出的二进制覆盖底座镜像中的 /app/javboss\n"
            "FROM %s\n"
            "COPY --chmod=0755 javboss /app/javboss\n" % BASE_IMAGE
        )
        sftp = cli.open_sftp()
        nas_env.put_script(sftp, dockerfile, FORK + "/Dockerfile.patch")
        sftp.close()
        print(dockerfile.rstrip())

        print("\n== 3. 构建 %s ==" % NEW_TAG)
        out, err = nas_env.sh(
            cli, "cd %s/build && %s build -t %s -f %s/Dockerfile.patch ."
                 % (FORK, D, NEW_TAG, FORK), t=2400)
        print(out.strip() or err.strip()[-800:])

        out, _ = nas_env.sh(
            cli, "%s images --format '{{.Repository}}:{{.Tag}} | {{.Size}}' | grep javboss" % D)
        print(out.strip())

        print("\n== 4. 后续步骤 ==")
        print("  1) 用 docs/maintenance-zh.md §10 的 docker run 模板重建容器（tag 换成 %s）" % NEW_TAG)
        print("  2) python3 9_verify_playback.py --deep     # 应 2243/2243 全 200")
        print("  3) 记得同步更新 Container/javboss/docker-compose.yml 里的 tag")
        return 0
    finally:
        cli.close()


if __name__ == "__main__":
    sys.exit(main())
