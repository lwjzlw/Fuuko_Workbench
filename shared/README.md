# shared

风子和接入的工作台共用的基础组件：

- `wbkit.py`：本机网页服务（只监听 127.0.0.1、令牌校验、运行信息文件、正常关闭）。
- `wbkit.js`、`fuuko.css`：前端小工具和统一样式，网页里通过 `/_shared/` 访问。
- `assets/`：默认图标。
- `host/`：独立窗口程序（WebView2）的源码和构建脚本。

## 构建窗口程序

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File shared/host/build.ps1
```

需要 .NET Framework 4.8 和 WebView2 Runtime。脚本会下载固定版本的 `Microsoft.Web.WebView2 1.0.4191.47` 并核对 SHA-256，编译完清理临时文件，结果输出到 `shared/host/bin/`。

- 离线构建：`-SdkPackage <nupkg 路径>`（版本和哈希要一致）
- 下载超时：`-DownloadTimeoutSeconds`（默认 120 秒）
- 输出位置：`-OutputDirectory`

没有构建窗口程序时，风子会用默认浏览器打开页面。
