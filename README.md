# 风子工作台

风子工作台是 Windows 上的本地工作台入口。它负责发现、登记、分类、启动和管理独立工具；各子工作台拥有自己的仓库、版本和依赖，也可以独立运行。

当前版本：**0.5.0**。支持本地扫描与手动导入。线上下载和更新尚未实现，协议已保留版本与项目来源字段。

## 启动

需要 Python 3.10 或更新版本。框架的 Python 服务只使用标准库。

```powershell
python -B fuuko_app.py
```

默认监听 `http://127.0.0.1:17800`。已构建窗口程序时使用 WebView2 窗口；未构建时通过默认浏览器打开。只启动服务可加 `--serve`。

```powershell
python -B fuuko_app.py --serve --data-dir "./local-data"
```

`--data-dir` 设置用户数据目录；也可设置环境变量 `FUUKO_DATA`。新安装默认使用当前用户的本地应用数据目录。原位升级若检测到原来的本机适配清单及同盘已有 `AppData/Fuuko`，继续使用该数据目录。框架默认从空状态开始，不自动读取源码目录 `apps/` 下的旧版适配清单；需要时可在管理页逐个导入这些 `.json`，或启动时加 `--legacy` 一次性读取（`--no-legacy` 保留为兼容参数，已是默认行为）。开发与隔离测试可使用 `--port 0` 让系统分配端口；正式使用默认端口以符合子工作台的嵌入策略。

## 登记本地工作台

1. 打开“管理”，在“本地工作台”中配置扫描目录，每行一个目录。
2. 把子工作台放入这些目录，确保其根目录带有 `workbench.json`；也可手动选择清单路径导入。
3. 点击扫描或顶部刷新。框架校验说明文件后加入入口，点击入口才会启动程序。

默认扫描目录是数据目录下的 `workbenches` 和风子所在目录的上一级。启动时不会自动登记新工作台，只有点“扫描”或手动导入才加入；`--legacy` 模式例外。扫描检查指定目录自身及其直接子目录，不搜索整盘。框架没有任何子工作台时也可启动。重名 ID 会提示冲突；无效或移走的项目显示原因，不会启动旧命令。取消登记不删除子工作台或数据，并会阻止下一次扫描自动加回；手动导入可恢复。

完整协议与接口见 [子工作台接入协议](docs/workbench-protocol.md)。[hello 示例](examples/hello/README.md) 使用标准库，可独立运行，也可手动导入 `examples/hello/workbench.json` 验证接入。

## 构建独立窗口

需要 Windows x64、.NET Framework C# 编译器及 WebView2 Runtime。构建脚本使用固定版本和 SHA-256 的 WebView2 SDK；联网下载或使用自行准备的同版本 SDK 包。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File shared/host/build.ps1
# 离线构建：在上面命令后附加 -SdkPackage "路径/microsoft.web.webview2.1.0.4191.47.nupkg"
```

运行文件写入 `shared/host/bin/`，构建过程的下载和解压目录自动清理。共享库和图标已内置，运行不依赖仓库外的 Shared 目录。

## 验证和打包

```powershell
python -B -m unittest discover -s tests -v
python -B scripts/package_release.py --output "release/fuuko-source"
python -B scripts/package_release.py --output "发行目录/fuuko-0.5.0-source.zip"
```

测试只使用隔离目录与示例服务，不启动本机业务工作台。源码包测试会解压到无邻近工作台的目录，实际启动空框架。窗口程序未构建时跳过其自检；其余核心测试仍可运行。

打包使用明确文件清单，支持生成独立源码目录或 ZIP，不复制整个项目。发布目录可独立初始化为 Git 仓库；请在导出的目录中提交源码。个人清单、运行数据、浏览器配置、缓存、工作日志和临时文件不进入发布包。本机头像在导出时替换为通用几何图标，源图保持原位。预编译窗口程序可能包含本机图标，因此不直接复制；需要二进制时，在导出的源码目录执行窗口构建命令。

`SOURCE-MANIFEST.json` 记录发布文件的 SHA-256。导出时检查用户目录、当前机器标识、邮箱及常见凭据格式；检查不能替代发布前人工复核。默认拒绝覆盖已有输出。只有先前由同一工具导出、没有额外文件或手改文件的目录能加 `--refresh` 更新；已初始化 Git 的目录须另选新输出路径。`.gitignore` 只是第二层防护，发布范围由打包脚本的白名单决定。

`.gitattributes` 禁用 Git 自动换行转换，保留导出文件的原始字节，使首次提交和跨平台克隆后的 SHA-256 仍与清单一致；文本差异仍可正常查看。

## 目录

| 路径 | 用途 |
| --- | --- |
| `fuuko_app.py` | 服务、API 与启动入口 |
| `registration.py` | 持久化登记、目录设置、冲突与失效处理 |
| `registry.py` | 说明文件协议、启动、状态与图标 |
| `control.py`、`layout.py` | 停止及窗口控制、用户分类布局 |
| `ui/` | 界面源码 |
| `shared/` | 框架内置基础设施与窗口程序 |
| `examples/hello/` | 可独立运行的最小子工作台 |
| `scripts/package_release.py` | 正式分发工具 |
| `tests/` | 可重复的隔离测试 |

`settings.json`、`registry.json`、`favorites.json`、`layout.json`、运行记录及窗口数据均保存在用户数据目录。源码目录中旧安装特有的 `apps/` 仅用于本机兼容，不应提交或打包。

公开发布前还需确定远程仓库和项目许可证。此源码尚未声明开源授权；第三方依赖与通用图标说明见 [NOTICE.md](NOTICE.md)。
