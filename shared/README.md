# 内置宿主组件

框架通过 `from shared import wbkit` 使用本目录，运行时不读取仓库之外的 Shared 目录。Python、JavaScript、样式和 Windows 宿主源码沿用本地工作台已有实现，并在此独立维护；无需安装第三方 Python 包。

`assets/` 中的 `fuuko-avatar.png`、`fuuko-round.png` 和 `fuuko.ico` 用于品牌位、网页图标和窗口图标。本机安装可以使用定制图标；正式源码导出工具仅发布由 `ui/icon.svg` 对应几何图形生成的通用图标，不复制本机头像。用户在界面设置的头像另外保存在用户数据目录。

Windows 窗口使用 WebView2 和 .NET Framework 4.8。编译入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File shared/host/build.ps1
```

脚本下载固定版本 `Microsoft.Web.WebView2 1.0.4191.47`，校验固定 SHA-256，然后在系统临时目录解压和编译。成功或失败都会清理本轮临时目录。下载默认限时 120 秒，可通过 `-DownloadTimeoutSeconds` 调整。也可以通过 `-SdkPackage <文件>` 使用已有且哈希相同的 SDK 包。默认输出 `shared/host/bin/`；`-OutputDirectory` 可指定输出目录。

运行输出仅包含宿主程序、配置、必要 WebView2 DLL 及其第三方许可文件；图标直接使用 `assets` 中的资源，不在输出目录重复保存。不把 SDK 包和解压目录保留在项目内。若宿主尚未编译，框架使用默认浏览器打开页面。目标系统需有 Microsoft Edge WebView2 Runtime。

框架自身的公开发布许可证由项目所有者确定；此文件不授予原有源码或资源新的许可。WebView2 依赖的许可随构建输出保留。
