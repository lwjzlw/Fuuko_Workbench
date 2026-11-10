# 独立接入示例

本目录可以整体复制到任意位置，独立运行不依赖风子代码、Shared 目录或第三方 Python 包。

```powershell
python hello_app.py
```

将本目录加入风子扫描位置，或导入本目录的 `workbench.json`，即可登记。扫描仅登记；点击打开后才会启动服务。示例支持标签页、独立窗口、健康检查和凭运行信息文件中的关闭密钥正常退出。

数据目录默认是 `%LOCALAPPDATA%/FuukoWorkbenches/example-workbench`，也可用 `--data-dir` 指定。`--serve` 只启动服务。示例仅生成运行期间的 `runtime.json`，正常关闭后移除；其余用户数据不会被删除。
