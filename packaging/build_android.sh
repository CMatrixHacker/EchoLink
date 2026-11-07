#!/usr/bin/env bash
# 在 Linux / WSL2 里构建 Android APK。
#
#   bash packaging/build_android.sh            # debug 包(可直接安装)
#   bash packaging/build_android.sh release    # release 包(需要签名才能装)
#
# 前提:sudo apt install openjdk-17-jdk python3-pip zip unzip autoconf libtool \
#       pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo6 cmake \
#       libffi-dev libssl-dev && pip install buildozer cython==0.29.36
set -euo pipefail

MODE="${1:-debug}"
cd "$(dirname "$0")/.."

if ! command -v buildozer >/dev/null 2>&1; then
    echo "缺 buildozer。先执行:" >&2
    echo "    python3 -m pip install --user buildozer cython==0.29.36" >&2
    exit 1
fi

if ! command -v javac >/dev/null 2>&1; then
    echo "缺 JDK。先执行: sudo apt install openjdk-17-jdk" >&2
    exit 1
fi

echo "== JDK =="
javac -version
echo "== buildozer =="
buildozer --version
echo

# 首次运行会下载 SDK/NDK(数 GB),之后走缓存
buildozer -v android "${MODE}"

echo
echo "== 产物 =="
ls -lh bin/*.apk 2>/dev/null || echo "没有找到 APK,请回看上面的日志"
