[app]

# 应用显示名与包名
title = EchoLink
package.name = echolink
package.domain = org.echolink

# 源码根目录(整个仓库),排除掉测试/文档/构建产物
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,txt,ttf
source.exclude_dirs = tests, docs, .git, .github, .pytest_cache, .smoke, logs, bin, .buildozer, __pycache__
source.exclude_patterns = *.md, *.spec.bak, server*.json, *secret*

version = 1.0.0

# python3 + kivy 提供界面,pyjnius 提供 android.bluetooth 的 RFCOMM 通道
requirements = python3,kivy==2.3.0,pyjnius,android

orientation = portrait
fullscreen = 0

# Android 11+ 需要 BLUETOOTH_CONNECT / BLUETOOTH_SCAN 才能发现与连接已配对设备
android.permissions = INTERNET,BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_CONNECT,BLUETOOTH_SCAN,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE,POST_NOTIFICATIONS

android.api = 33
android.minapi = 23
android.archs = arm64-v8a,armeabi-v7a
android.allow_backup = True
android.accept_sdk_license = True

# 竖屏 + 深色标题栏
android.presplash_color = #101418

[buildozer]
log_level = 2
warn_on_root = 1
