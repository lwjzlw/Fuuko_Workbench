# hello 示例

一个最小的工作台，只用 Python 标准库，复制到任何地方都能单独跑：

```powershell
python hello_app.py
```

把这个文件夹放进风子的扫描目录，或者在风子里导入这里的 `workbench.json`，就能登记。登记后点「打开」才会启动服务。

它演示了一个工作台需要的几件事：在标签页或独立窗口里打开、健康检查、按运行信息文件里的关闭密钥正常退出。

数据默认放在 `%LOCALAPPDATA%/FuukoWorkbenches/example-workbench`，可以用 `--data-dir` 指定；`--serve` 只启动服务、不开窗口。
