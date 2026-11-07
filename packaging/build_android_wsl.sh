#!/usr/bin/env bash
# 在 WSL2 / Linux 里一条命令产出 APK。
#
#   bash packaging/build_android_wsl.sh            # debug 包(可直接装)
#   bash packaging/build_android_wsl.sh release    # release 包(需签名)
#
# 为什么不在 /mnt/d 上直接构建:WSL 的 DrvFs 不保存 Unix 权限位,
# p4a 编译原生库时会报 "Permission denied" / "Exec format error"。
# 所以这里先把仓库同步到 ext4 的 ~/echolink-build,构建完再把 APK 拷回来。
set -euo pipefail

MODE="${1:-debug}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${ECHOLINK_BUILD_DIR:-$HOME/echolink-build}"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

# ---------- 0. 环境体检 ----------
say "环境"
[[ "$(uname -s)" == "Linux" ]] || die "这个脚本要在 Linux/WSL 里跑,当前是 $(uname -s)"
grep -qi microsoft /proc/version 2>/dev/null && echo "运行环境: WSL2" || echo "运行环境: 原生 Linux"
echo "源目录  : $SRC"
echo "构建目录: $WORK"
df -h "$HOME" | tail -1 | awk '{print "可用空间: "$4}'

avail_kb=$(df -Pk "$HOME" | awk 'NR==2{print $4}')
if [ "$avail_kb" -lt 12000000 ]; then
    die "磁盘可用空间不足 12GB(当前 $((avail_kb/1024/1024))GB)。首次构建要下 SDK+NDK 约 5GB,编译中间产物约 4GB。"
fi

# ---------- 1. 系统依赖 ----------
say "系统依赖"
APT_PKGS=(git zip unzip openjdk-17-jdk python3-pip python3-venv
          autoconf libtool pkg-config zlib1g-dev
          libncurses5-dev libncursesw5-dev libtinfo6
          cmake libffi-dev libssl-dev)

need_install=0
for pkg in "${APT_PKGS[@]}"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || { need_install=1; break; }
done

if [ "$need_install" -eq 1 ]; then
    echo "安装缺失的系统包(需要 sudo)…"
    if sudo -n true 2>/dev/null; then
        sudo -n apt-get update -qq
        sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${APT_PKGS[@]}"
    else
        die "需要 sudo 权限但当前无法免密。请先手动执行:
    sudo apt update && sudo apt install -y ${APT_PKGS[*]}"
    fi
else
    echo "系统包已齐,跳过 apt"
fi

javac -version 2>&1 | sed 's/^/javac: /'

# ---------- 2. Python 构建环境(venv,不污染系统) ----------
say "buildozer"
if [ ! -x "$WORK/.venv/bin/buildozer" ]; then
    mkdir -p "$WORK"
    [ -d "$WORK/.venv" ] || python3 -m venv "$WORK/.venv"
    "$WORK/.venv/bin/pip" install -q --upgrade pip wheel setuptools
    "$WORK/.venv/bin/pip" install -q "buildozer==1.5.0" "cython==0.29.36"
fi
# shellcheck disable=SC1091
source "$WORK/.venv/bin/activate"
buildozer --version | sed 's/^/buildozer: /'

# ---------- 3. 同步源码到 ext4 ----------
say "同步源码"
if [ "$SRC" = "$WORK" ]; then
    # 仓库本身就在 ext4 上,无需同步(也避免误删仓库里的 dist/build/.git)
    echo "源目录即构建目录,跳过多余同步"
else
    mkdir -p "$WORK"
    # 保留 .buildozer(首次下载的 SDK/NDK 缓存)和 .venv,其余覆盖
    cp -a "$SRC/." "$WORK/"
    rm -rf "$WORK/dist" "$WORK/build" "$WORK/.git" "$WORK/.pytest_cache"
fi
cd "$WORK"
[ -f buildozer.spec ] || die "构建目录里没有 buildozer.spec,同步失败"
echo "已同步: $(find . -name '*.py' -not -path './.venv/*' -not -path './.buildozer/*' | wc -l) 个 py 文件"

# ---------- 4. 构建 ----------
say "开始构建($MODE)——首次会下载 SDK/NDK,约 5~20 分钟"
export GRADLE_OPTS="-Xmx2048m -Dorg.gradle.jvmargs=-Xmx2048m"
if ! buildozer -v android "$MODE" 2>&1 | tee "$WORK/build.log"; then
    echo
    die "构建失败。日志: $WORK/build.log
常见原因见 docs/build.md「排错」一节,或直接看日志里最后 30 行错误。"
fi

# ---------- 5. 回收产物 ----------
say "产物"
mapfile -t APKS < <(find "$WORK/bin" -maxdepth 1 -name "*.apk" -type f 2>/dev/null | sort)
if [ "${#APKS[@]}" -eq 0 ]; then
    die "构建结束但没有找到 APK,请查看 $WORK/build.log"
fi

mkdir -p "$SRC/dist"
for apk in "${APKS[@]}"; do
    cp -f "$apk" "$SRC/dist/"
    printf '  %-42s %s\n' "$(basename "$apk")" "$(du -h "$apk" | cut -f1)"
done
echo
echo "APK 已放到: $SRC/dist/"
echo
echo "装到手机:"
echo "  adb install -r \"$SRC/dist/$(basename "${APKS[0]}")\""
echo "  或者把 apk 传到手机点击安装(需允许「安装未知来源应用」)"
