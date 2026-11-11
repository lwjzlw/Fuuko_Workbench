# 风子工作台

Windows 上的个人工具入口。电脑里零零散散的本地小工具（这里叫「工作台」）都可以收进来，在同一个窗口里打开、切换、关掉。

每个工作台都是独立的项目，离开风子也能自己跑；风子只管找到它们、把它们打开。

## 功能

- **首页**：常用的、最近用过的、正在运行的都摆在首页，其余按分类放好。
- **标签页**：网页类的工作台直接在风子的标签页里打开，也能单独开窗口。
- **快速打开**：`Ctrl+K` 搜名字直接打开，`Alt+数字` 切换标签页。
- **管理**：扫描文件夹找工作台，或者手动导入一个 `workbench.json`；分类、隐藏、看运行状态、关服务都在这里。
- **小细节**：浅色、深色或跟随系统；在左上角头像上连点五下，可以改名字和头像。

## 开始使用

需要 Python 3.10 以上，不用装任何第三方包。

```powershell
python -B fuuko_app.py
```

地址是 `http://127.0.0.1:17800`。构建过窗口程序就用独立窗口打开，没构建就用默认浏览器。

第一次打开首页是空的，去「管理」里加工作台：

1. 在「扫描目录」填上放工作台的文件夹（每行一个），点「保存并扫描」；
2. 或者在「导入说明文件」里粘贴某个工作台的 `workbench.json` 路径。

风子默认会扫描自己的上一级文件夹，所以把工作台和风子放在一起，点一下扫描就全找到了。

个人数据默认放在 `%LOCALAPPDATA%\Fuuko`，想换位置就用 `--data-dir` 或环境变量 `FUUKO_DATA`。

## 写一个自己的工作台

在工作台根目录放一个 `workbench.json`，写清楚名字、怎么启动、怎么打开就行。格式见 [接入协议](docs/workbench-protocol.md)，[examples/hello](examples/hello/README.md) 是最小的例子。

完整一点的例子可以看 [HBR 研究室](https://github.com/lwjzlw/Fuuko_HBR)。

## 构建独立窗口（可选）

需要 Windows x64、.NET Framework 自带的 C# 编译器和 WebView2 Runtime：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File shared/host/build.ps1
```

生成的文件在 `shared/host/bin/`。

## 开发

```powershell
python -B -m unittest discover -s tests
```

## 许可

代码用 [MIT](LICENSE) 协议开源，第三方组件见 [NOTICE.md](NOTICE.md)。
