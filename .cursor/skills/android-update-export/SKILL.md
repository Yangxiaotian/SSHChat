---
name: android-update-export
description: >-
  Builds SSHChat Android release APK and exports it to android/app-update/ with
  version.json for server OTA distribution. Use after changing android/** features,
  fixing Android bugs, or when the user mentions Android release, APK export, or
  app upgrade artifacts. iOS is out of scope.
---

# Android 升级包导出

完成 **Android 端功能改动** 后，在收尾阶段执行本流程，把 release APK 导出到升级目录，供服务端通过 `gui-open download` 等方式分发。

## 何时执行

在以下情况 **必须** 运行导出（除非用户明确说不要打包）：

- 修改了 `android/` 下任意 Kotlin、资源、Gradle 配置
- 用户要求发版、导出 APK、更新升级目录
- Android 功能开发任务已完成，准备交付

**跳过**：仅改 iOS、Electron、Python 服务端且未动 Android 时。

## 版本号

若本次是 **面向用户的功能/fix**（不仅是内部 refactor），先递增 `android/app/build.gradle.kts`：

```kotlin
versionCode = 30        // 必须 +1
versionName = "0.3.17"  // 语义版本
```

小改动可只 bump `versionCode`；发版时同步改 `versionName`。

## 导出命令

```bash
./scripts/export-android-update.sh
```

可选自定义目录：

```bash
SSHCHAT_ANDROID_UPDATE_DIR=/path/to/out ./scripts/export-android-update.sh
```

脚本会：

1. 调用 `scripts/build-android-apk.sh` 打 release APK
2. 写入升级目录（默认 `android/app-update/`）：
   - `SSHChat-latest.apk` — 固定名，服务端始终指向此文件
   - `SSHChat-<versionName>.apk` — 带版本号的备份
   - `version.json` — `versionCode` / `versionName` / 大小 / 构建时间

## 收尾汇报

导出成功后告知用户：

- `versionName` / `versionCode`
- `android/app-update/` 下三个文件的路径
- 服务端需把该目录同步到机器后，用 `/sendfile` 或后续 OTA 逻辑分发（iOS 暂不做）

## 失败处理

| 错误 | 处理 |
|------|------|
| 缺少 Android SDK | 提示设置 `ANDROID_HOME`，安装 `platforms;android-35` 与 `build-tools;35.0.0` |
| 缺少 JDK | 需要 JDK 17+ |
| Gradle 失败 | 先修编译错误，再重新导出 |

## 不要做的事

- 不要为 iOS 执行导出或改 `ios/project.yml` 版本
- 不要把 `*.apk` 提交进 git（已在 `.gitignore`）；可提交 `version.json`
- 不要用 `sudo` 运行导出脚本
