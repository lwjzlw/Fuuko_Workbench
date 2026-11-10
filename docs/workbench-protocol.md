# 子工作台接入协议

风子 0.5.0 支持说明文件格式 1、宿主接口主版本 1。子工作台独立发布，只需在根目录携带 `workbench.json`；无须调用登记 API 才能被扫描识别。

## 说明文件

```json
{
  "schema_version": 1,
  "id": "example-workbench",
  "name": "示例工作台",
  "version": "0.1.0",
  "requires": {"host_api": 1},
  "description": "独立运行的示例。",
  "category": "工具",
  "launch": {
    "command": ["{python}", "hello_app.py", "--serve", "--data-dir", "{data_dir}"],
    "cwd": "."
  },
  "url": {"runtime": "{data_dir}/runtime.json"},
  "health": {"type": "runtime", "path": "{data_dir}/runtime.json"},
  "open": ["tab", "window"],
  "stop": {"type": "wbkit", "allow_force": false}
}
```

| 字段 | 约定 |
| --- | --- |
| `schema_version` | 整数 `1`；无此字段的旧清单以兼容模式读取 |
| `id` | 稳定标识，小写英文字母、数字、`-` 或 `_`；同一安装只能登记一份 |
| `name`、`version` | 显示名称、版本号，例如 `0.1.0` |
| `requires.host_api` | 整数 `1`；与框架软件版本分开检查 |
| `description`、`category`、`order`、`accent`、`icon` | 可选展示信息；图标支持 SVG、PNG、ICO、JPG 及 Windows 可执行文件图标 |
| `launch.command`、`launch.cwd` | 启动参数数组与工作目录；不隐式拼成 shell 命令 |
| `url` | `fixed` 指定本机 HTTP 地址，或 `runtime` 指定运行信息文件 |
| `health` | `http`、`port`、`runtime`、`process` 四种方式之一；也可省略 |
| `open` | `tab`、`window`、`launch` 的数组，首项为默认；内嵌和窗口方式需要 URL |
| `stop` | 关闭策略，可省略或设 `false`；旧 runtime/process 检查默认推断 wbkit/close |
| `repository` | 可选的项目 HTTP(S) 地址，仅记录来源，当前不联网下载或更新 |

格式或协议不支持时显示原因，不执行清单。旧清单显示兼容标记，现有绝对路径可以继续使用，但新发布应采用相对路径和占位符。

## 路径与运行时

图标、工作目录及运行信息路径相对于说明文件所在目录解析。命令参数中的脚本路径按 `cwd` 解析。

- `{app_dir}`：说明文件所在目录。
- `{data_dir}`：该工作台的用户数据目录，默认位于当前用户本地应用数据目录下 `FuukoWorkbenches/<id>`。该目录必须同时传给工作台并用于运行信息和状态检查。
- `{python}`、`{pythonw}`：优先使用本机明确配置的解释器，再查工作台 `.venv`，最后使用宿主解释器。
- `{node}`、`{powershell}`：使用本机设置或系统可用的运行时。

本机 `settings.json` 支持 `runtime_paths`、`workbench_data_root` 和按 ID 设置的 `data_dirs`。这些是用户安装配置，不应写入子工作台发行清单。子工作台负责自身依赖安装，扫描不会执行安装命令。独立运行时应采用相同数据目录约定或接受 `--data-dir`。

## 状态与关闭

`url.fixed` 及 HTTP 健康检查只接受 `127.0.0.1` 或 `localhost` 的 HTTP 地址，必须含端口。采用 runtime 的服务将 `app`、`pid`、`url` 写入指定 JSON 文件，正常退出时删除该文件。进程和 HTTP 健康检查可用于不采用 runtime 的工具。

省略 `health` 时，固定 URL 默认检查端口，runtime URL 默认检查运行信息文件；无 URL 的纯启动入口保持状态未知。runtime 方式同时沿用默认 wbkit 关闭策略，可用 `stop:false` 禁止该能力。

`stop.type=wbkit` 需要运行信息中的 `control` 随机关闭密钥。宿主先请求 `/api/health` 核对 `app`、`pid`，再携带 `X-WB-Control` 请求 `POST /api/shutdown`；该关闭接口拒绝带有 Origin 的请求。服务有任务在运行时可返回 409；是否允许强制关闭由 `allow_force` 决定。最小完整实现见 [hello_app.py](../examples/hello/hello_app.py)。

其他关闭策略：`command` 运行指定参数数组；`http` 请求本机停止地址，可配置取令牌接口；`close` 请求 Windows 程序关窗。没有关闭策略的工作台仍可登记和启动。

Web 工作台在 `tab` 模式下需允许来源 `http://127.0.0.1:17800` 嵌入。独立窗口程序只需提供 `launch`，不必改成 Web 页面。

## 本地登记 API

接口绑定本机，使用宿主当前会话的 `X-WB-Token`。POST 需要通过同源检查。页面令牌不同于子工作台关闭密钥。所有路径由本机用户配置，接口不提供远程安装能力。

| 方法与路径 | 请求与结果 |
| --- | --- |
| `GET /api/registry` | 返回 `entries`、`conflicts`、`errors`、`excluded`、`settings` |
| `POST /api/registry/scan` | `{}`；扫描配置目录并登记，返回快照及 `summary` |
| `POST /api/registry/register` | `{"manifest_path":"绝对路径/workbench.json"}`；重复同路径保持幂等 |
| `POST /api/registry/register` | 额外传 `"replace":true` 可明确选取同 ID 的另一份安装 |
| `POST /api/registry/unregister` | `{"id":"example-workbench"}`；取消并排除当前路径，不删除文件 |
| `POST /api/registry/settings` | 部分更新 `scan_roots`、`runtime_paths`、`data_dirs`、`workbench_data_root`，并扫描 |
| `GET /api/apps` | 首页视图，含 `available`、`error`、`version`、`legacy` 与运行状态 |
| `POST /api/open` | `{"id":"example-workbench","mode":"tab"}`；重验清单后启动或返回现有地址 |

每个扫描根目录检查自身及直接子目录。首次发现多个相同 ID 时不自动启用任一份；已有登记遇到另一份相同 ID 时保留原选择并报告冲突。手动导入支持扫描目录以外的位置。

已登记的路径丢失或清单无效时，保留身份、常用和分类，禁用操作；恢复后重新验证。取消登记的路径不会在下次扫描中自动加入，手动导入可恢复。清单被改成另一 ID 时需先取消旧登记再导入。

登记文件损坏或被另一实例同时修改时报告错误，不静默覆盖。设置和登记记录使用原子替换写入，失败会清理本次写入的临时文件。配置后的扫描失败会恢复先前设置；如文件又被外部修改或恢复失败，会明确报告当前设置可能改变，并阻止继续启动直到检查和重启。

## 后续线上功能

将来的线上目录通过稳定 ID、版本、来源与本机登记对应。下载或更新完成后仍调用上述登记服务。当前没有线上目录请求、后台下载、自动更新或远程脚本执行。
