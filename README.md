# 本地知识库助手（personal-kb）

一个可移植的本地文件知识库：扫描指定文件夹/盘符，建立本地索引，通过 **MCP** 暴露给飞书机器人、豆包或其他 MCP 客户端。

> 完整安装与使用说明：[docs/安装与使用.md](docs/安装与使用.md)（前置条件、脚本清单、权限、索引、排错）。
> 飞书细节：[docs/飞书接入手册.md](docs/飞书接入手册.md)。

当前版本已实现：

- 环境自检 `kb doctor`
- 图片整理 `kb organize --strategy image`（OpenCV 分类：人物/风景/文档截图/其他）
- 本地模型问答 `kb model` + Ollama（可选；不装则用摘录式）
- 一键环境检测与接入引导 `kb setup`（权限、豆包、飞书、索引、服务）
- macOS 菜单栏控制 `kb tray`（启动/停止、日志、重新索引、退出）
- 3D 星空知识库脑图 `kb map`（星云聚类、AI 聊天、推荐删除、图片整理、文件详情浮窗、全屏）
- 启动时选择索引范围（文件夹/盘符/用户目录）
- 文件元数据 + 文本内容索引（SQLite FTS5 trigram）
- 搜索、读取文件、列目录、总结文件夹、摘录式问答
- 整理文件夹：按类型/月份/内容归类，支持移动、重命名、预览、确认、撤销
- MCP 服务：stdio 和 Streamable HTTP
- 服务日志落盘（`logs/`）与 `kb logs` 查看
- macOS / Windows / Linux（含鸿蒙 Python 终端）安装脚本

已提供基础版飞书机器人（长连接 + 文本/文件回复），但需要你自己建飞书自建应用并审核。

尚未实现（下一步）：

- 单文件可执行程序打包（当前是 Python 虚拟环境）
- 鸿蒙 OS 真机验证（需要 Python 3.10+ 终端；纯鸿蒙环境暂不支持）

## 快速开始

### macOS 一键启动

双击 **`一键启动.command`**，会依次：

1. 缺环境时先安装（含菜单栏依赖 `rumps`）。
2. 检查 `~/Documents`、`~/Desktop`、`~/Downloads` 和 `.venv` 的读取权限；被系统拦截时询问并打开对应的「完全磁盘访问权限 / 文件与文件夹」设置页。
3. 检查豆包工作是否安装、是否已有 `personal-kb` 连接器；没装/没配时给出步骤。
4. 检查飞书 `app_id/app_secret`、白名单和机器人能力。
5. 没有索引范围时进入选择菜单；索引为空时询问是否建立。
6. 启动 3D 脑图、MCP、监听和飞书机器人。
7. 在菜单栏显示 «KB» 图标（关闭终端窗口也会继续运行）。

关闭：菜单栏「退出（停止服务）」，或双击 **`一键关闭.command`**。

只想检查、不做任何修改：

```bash
kb setup --check
kb setup --check --json
```

### macOS / Linux

```bash
bash install.sh
source .venv/bin/activate

kb index          # 第一次运行会自动提示选择文件夹/盘符，然后建索引
kb start          # 启动 3D 脑图 + MCP + 后台监听
kb search "报销"
kb ask "报销流程是什么"
kb summarize /path/to/folder
```

也可以双击 `一键安装.command`。

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
.\.venv\Scripts\Activate.ps1

kb index          # 第一次运行会自动提示选择盘符/文件夹
kb start
kb search "报销"
```

也可以双击 `一键安装.bat`。

### Linux / 鸿蒙 OS

```bash
bash install.sh
source .venv/bin/activate
kb start
```

鸿蒙 OS 需要先有可用的 Python 3.10+ 终端环境；没有 Python 的纯鸿蒙系统暂不支持。

### 安装时的本地模型选项

安装脚本最后会询问是否安装本地模型：

- 可选：`qwen2.5:3b`（推荐，约 1.9GB，平衡）、`qwen2.5:1.5b`（更快，约 1GB，质量略低）、`qwen2.5:7b`（更强，约 4.7GB，CPU 明显更慢）。
- 时间：100Mbps 下载约 3~6 分钟；慢网 10~30 分钟。
- 效果：离线问答和总结，资料不出本机；小模型回答简短，复杂推理一般。
- 场景：重视隐私、经常没网、只想快速问资料；只找文件可以不装，默认用摘录式搜索。
- 非交互安装可设 `KB_OLLAMA_MODEL=qwen2.5:1.5b` 指定模型，`KB_WITH_OLLAMA=0` 跳过。

## 脚本一览

| 文件 | 平台 | 用途 |
|---|---|---|
| `一键安装.command` | macOS | 双击安装环境（装完暂停） |
| `一键启动.command` | macOS | 缺环境先装；检测权限/接入/索引；启动服务并拉起菜单栏 |
| `一键关闭.command` | macOS | 停止服务并退出菜单栏 |
| `install.sh` / `install.ps1` | macOS、Linux、鸿蒙 / Windows | 命令行安装或升级 |
| `一键安装.bat` | Windows | 双击安装 |
| `start.sh` / `start.bat` | macOS、Linux、鸿蒙 / Windows | 终端启动 `kb start` |
| `scripts/scan-secrets.sh` / `.ps1` | macOS、Linux / Windows | 分享打包前的隐私/密钥扫描 |

完整说明见 [docs/安装与使用.md](docs/安装与使用.md)。

## 环境要求

| 项目 | 最低 | 推荐 |
|---|---|---|
| 系统 | Windows 10/11 x64；macOS 12+ | Windows 11 / macOS 13+ |
| Python | 3.10+ | 3.12 |
| 内存 | 8GB | 16GB |
| 磁盘 | 10GB 空闲 | 50GB+ SSD |

安装后不含模型的程序本体约几十 MB；索引大小取决于文件数量。

## 索引范围

第一次运行 `kb index`、`kb start` 或 `kb feishu` 时，如果没有配置过范围，会自动弹出选择菜单，不需要单独先跑 `kb scope`。

```bash
kb index                       # 第一次会自动提示选择范围，然后建索引
kb scope                       # 以后想改范围时再用
kb scope --add "D:\资料"        # Windows
kb scope --add ~/Documents     # macOS
kb scope --list
kb scope --clear
kb index --full                # 全量重建
```

范围保存在数据库里；默认排除系统目录、缓存、密钥文件、临时文件。超过 `max_file_size_mb`（默认 20MB）的文件只索引文件名，不读正文。

> 自动提示需要终端交互。如果作为后台服务/开机自启运行、没有终端输入，会提示先运行一次 `kb scope`。

## 常用命令

```bash
kb status
kb search "供应商准入"
kb search "报价单" --ext xlsx
kb get /path/to/file --max-chars 8000
kb ls /path/to/folder --depth 2
kb summarize /path/to/folder
kb ask "报销流程是什么"
kb organize /path/to/photos --strategy image --plan  # OpenCV 图片归类预览
kb model status                # 查看本地 Ollama 模型
kb model install --name qwen2.5:3b  # 安装并下载模型
kb model enable                # 启用模型问答（disable 恢复摘录式）
kb watch --events              # 默认：文件事件增量，只索引变动的文件
kb selfcheck                   # 系统自检：索引/服务/磁盘/权限/索引锁
kb start                       # 3D 脑图 + MCP HTTP + 监听（配置了飞书会自动带机器人）
kb start --no-feishu           # 临时不启动飞书机器人
kb stop                        # 关闭由 kb start 启动的后台服务
kb stop --force                # 同时清理占用配置端口的残留进程
kb setup                       # 环境检测 + 权限引导 + 接入引导 + 启动
kb tray                        # macOS 菜单栏控制
kb map --open                  # 3D 星空脑图（星云、可拖动/缩放 AI 聊天、整理、全屏）
kb logs                        # 列出服务日志
kb logs                        # 列出日志；kb logs map -f 跟踪 3D 脑图
```

`kb start` 现在是守护模式：

- 再次运行 `kb start` 时，会先关闭同一套旧服务，再启动新的，不会出现端口冲突。
- Web、MCP、监听、飞书机器人任意一个意外退出，会自动拉起；连续失败 5 次后停止重试，原因同时写入 `logs/<服务>.log`。
- `config/feishu.json` 填好 `app_id` / `app_secret` 后，`kb start` 会自动带上飞书机器人；临时不想启动加 `--no-feishu`。飞书长连接断开后会自动重连。
- `kb stop` 会同时停止守护进程和所有子服务，避免“停掉子服务又被自动拉起”。
- 如果端口被其他程序占用，会打印占用 PID；`kb stop --force` 可以清理配置端口上的残留进程。

3D 脑图没有关闭服务的按钮；要停服务用托盘里的“停止服务”，或运行 `kb stop`（会一起停止 MCP、监听、飞书和自检）。

## 日志

`kb start` 启动的每个服务都会把输出同时写到终端和文件，日志目录是 `KB_HOME/logs/`（默认就是项目下的 `logs/`）：

| 文件 | 内容 |
|---|---|
| `logs/map.log` | 3D 脑图（独立启动时） |
| `logs/mcp.log` | MCP HTTP 服务的启动和访问日志 |
| `logs/watch.log` | 后台增量扫描的入库 / 错误记录 |
| `logs/feishu.log` | 飞书长连接、消息处理和发送失败 |
| `logs/tray.log` | 菜单栏托盘的启动、异常和退出记录 |

```bash
kb logs                  # 列出全部日志（大小、修改时间）
kb logs map              # 看最后 50 行
kb logs map -n 200       # 看最后 200 行
kb logs map -f           # 持续跟踪，Ctrl+C 退出
kb logs map --clear      # 清空
```

- 单个日志超过 5MB 时，会在该服务下次启动时轮转成 `xxx.log.1`，只保留一份备份。
- stdio 模式的 MCP（`kb mcp`）不会写到 stdout，避免破坏 MCP 协议；输出仍然落在 `logs/mcp.log`。
- `logs/` 不会进入 `kb export` 的分享包，迁移包里也不包含。

## 环境检测与接入引导

`kb setup` 会逐项检查并给出处理方式：

| 项目 | 检查内容 |
|---|---|
| 系统权限 | `~/Documents`、`~/Desktop`、`~/Downloads`、项目 `.venv` 是否可读；被拒绝时可打开对应系统设置页 |
| 豆包工作 | `/Applications/DoubaoWork.app` 是否安装、版本、是否已有 `personal-kb` 连接器 |
| 飞书 | `config/feishu.json` 是否填了 `app_id/app_secret`、白名单、机器人能力 |
| 索引 | 索引范围、文件数、已提取正文、错误数 |
| 服务 | 3D 脑图 8770、MCP 8766、监听、飞书机器人、自检 |

```bash
kb setup                 # 交互检查，并按需打开设置、选范围、建索引、启动服务
kb setup --check         # 只检查，不修改、不打开窗口、不启动
kb setup --check --json  # 给脚本/监控用
kb setup --start         # 检查后直接启动服务
kb setup --no-open       # 不自动打开系统设置或应用
```

## 菜单栏（macOS）

```bash
kb tray          # 启动菜单栏
kb tray --check  # 只检查 rumps 依赖和服务状态
```

菜单项：运行状态、打开 3D 脑图、复制 MCP 地址、环境与接入检查、重新索引、查看日志（map/mcp/watch/feishu/tray）、打开项目目录、启动服务、停止服务、退出（停止服务）。

- 托盘日志：`logs/tray.log`
- 双击 `一键启动.command` 会自动把托盘以后台方式拉起
- Windows 没有菜单栏，继续用 `start.bat` / `kb start` / `kb stop`

## 两个地址不要混

| 地址 | 用途 | 浏览器打开会怎样 |
|---|---|---|
| `http://127.0.0.1:8770/` | 3D 脑图 | 正常显示星空和助手 |
| `http://127.0.0.1:8766/mcp` | MCP 接口，给豆包/机器人用 | 会提示缺少 session 或 Bad Request，这是正常的 |
| `http://127.0.0.1:8766/` | MCP 根地址 | 现在会显示“这是 MCP 接口，不是网页”的提示 |

3D 脑图默认只绑定 `127.0.0.1`；如需局域网访问，可单独用 `kb map --host 0.0.0.0` 启动，并确认网络环境安全。

## MCP 接入

### stdio（本机客户端）

```bash
.venv/bin/python -m kb mcp
```

客户端配置示例：

```json
{
  "mcpServers": {
    "personal-kb": {
      "command": "/绝对路径/personal-kb/.venv/bin/python",
      "args": ["-m", "kb", "mcp"],
      "env": { "KB_HOME": "/绝对路径/personal-kb" }
    }
  }
}
```

Windows 把 `command` 换成 `.venv\Scripts\python.exe`。

### Streamable HTTP（豆包/远程客户端）

```bash
kb mcp --http
# 默认：http://127.0.0.1:8766/mcp
```

只绑定 `127.0.0.1`。如果要给云端客户端用，需要自己做 HTTPS 隧道和 Token，不要直接把端口暴露到公网。

### 工具清单

| 工具 | 作用 |
|---|---|
| `search_files` | 按文件名/内容搜索 |
| `get_file_info` | 读取文件元数据/文本预览 |
| `list_dir` | 列目录 |
| `summarize` | 总结文件夹 |
| `ask_knowledge` | 基于本地索引回答（摘录式） |
| `index_status` | 索引状态 |
| `reindex` | 重新索引 |
| `plan_files` | 生成整理计划 |
| `apply_files` | 执行整理（必须 `confirm=true`） |
| `undo_files` | 撤销整理 |

## 飞书机器人

### 安装飞书依赖

```bash
KB_WITH_FEISHU=1 bash install.sh
# 或
.venv/bin/python -m pip install -e ".[feishu]"
```

Windows：

```powershell
$env:KB_WITH_FEISHU="1"
powershell -ExecutionPolicy Bypass -File install.ps1
```

### 创建飞书应用

1. 打开飞书开放平台，创建「企业自建应用」。
2. 添加应用能力 → 机器人。
3. 权限管理里至少勾选：
   - 接收消息（`im:message`）
   - 以应用身份发消息（`im:message:send_as_bot`）
   - 私聊消息（`im:message.p2p_msg:readonly`）
   - 群聊 @ 消息（`im:message.group_at_msg:readonly`）
   - 上传文件/发送文件（按官方文件接口权限勾选）
4. 事件订阅 → 选择「长连接」→ 订阅 `im.message.receive_v1`。
5. 发布应用，等管理员审核通过。
6. 把 App ID / App Secret 填到 `config/feishu.json`：

```json
{
  "app_id": "cli_xxx",
  "app_secret": "xxx",
  "allow_all": false,
  "allowed_open_ids": ["ou_你的open_id"],
  "allowed_chat_ids": []
}
```

`allow_all` 默认 `false`；先把自己的 `open_id` 加进白名单，或临时设 `true` 测试。

### 启动

```bash
kb feishu                 # 只启动机器人
kb start                  # 配置了 feishu.json 后自动带机器人
kb start --no-feishu      # 临时不启动机器人
kb start --with-feishu    # 强制启动
```

机器人只处理私聊和群里的 @ 消息；不在白名单里的用户会被忽略。长连接断开会自动重连，重连记录在 `logs/feishu.log`。`kb setup` 会校验 `app_id/app_secret`、机器人能力和白名单。

手机飞书里私聊机器人，或把它拉进群 @ 它：

```text
找 报价单
发我 报销流程
总结 D:\项目A
整理 D:\发票
确认整理 <计划ID>
撤销 <计划ID>
报销流程是什么
/whoami
```

注意：机器人进程必须跑在有文件的电脑上，电脑要开机、联网、不休眠；手机只是入口。

## 豆包工作

豆包工作（DoubaoWork）通过「连接器」接 MCP，只接受 HTTP 地址，不需要命令行。

1. 先确保本机服务已启动：`kb start`（3D 脑图 8770、MCP 8766）。
2. 打开豆包工作 → 左侧「插件 · 技能」→ 右上「管理」→「连接器」标签 →「添加」→「新建自定义连接器」。
3. 填写：
   - 服务器名称：`personal-kb`
   - 传输类型：`HTTP`
   - 服务器 URL：`http://127.0.0.1:8766/mcp`
   - 自定义 Headers：留空
4. 保存并确认连接器开关已打开。之后在新任务里直接说「用 personal-kb 搜索报销流程」或「调用 index_status 看索引状态」。

> 自定义连接器只在本机生效；豆包访问的是 `127.0.0.1:8766`，不会把端口暴露到局域网。

运行 `kb setup` 会自动检测豆包工作是否安装、是否已有 `personal-kb` 连接器；未配置时会给出上面的步骤。

## 整理文件夹

```bash
kb organize "D:\发票" --plan --strategy type
kb organize "D:\发票" --apply <plan_id> --yes
kb organize "D:\发票" --undo <plan_id>
```

- `--strategy type`：按文件类型归类。
- `--strategy date`：按修改月份归类。
- `--strategy content`：按文件名/内容关键词归类（规则在 `config/organize_rules.json`）。
- `--rename-pattern`：重命名模板，例如 `"{date}-{name}"`。
- 默认只移动/重命名，不删除、不覆盖；重名自动加后缀。
- 执行前必须预览；执行后保留撤销日志。

## 隐私扫描与打包

```bash
kb scan                                  # 扫描项目里的密钥、绝对用户路径、邮箱、手机号
kb export --mode share -o dist/share.zip # 分享包：代码 + 示例，不含 data/、真实配置
kb export --mode migrate -o dist/migrate.zip  # 迁移包：额外包含 data/kb.db 和 config.json
kb import dist/migrate.zip --force       # 在新电脑恢复
```

- `share` 包用于发给别人，打包前会经过隐私扫描；`data/`、`config/config.json`、`config/feishu.json` 不会进入。
- `migrate` 包用于自己换电脑，包含索引数据库；如果新电脑路径不同，重新 `kb scope`/`kb index` 即可。
- `kb scan` 发现可疑内容会返回非零退出码，可接到打包脚本里做门禁。

## 安全边界

- 索引和文件都在本地；MCP 默认只监听 `127.0.0.1`。
- 只允许访问 `kb scope` 配置的根目录，拒绝路径穿越。
- `.env`、私钥、证书、浏览器密码库等默认不索引。
- 整理是写操作，必须预览 + 确认；默认不删除。
- 飞书/豆包接入时，注意公司合规：文件内容可能通过聊天工具外发。

## 故障排查

| 现象 | 处理 |
|---|---|
| `kb` 找不到 | 先激活虚拟环境：`source .venv/bin/activate`（Windows：`.venv\Scripts\Activate.ps1`） |
| 中文搜不到 | 运行 `kb doctor` 看 FTS5 trigram 是否可用；或换关键词 |
| PDF/Word 没有正文 | 安装可选依赖：`pip install -e ".[docs]"` |
| 图片整理报缺少 OpenCV | `pip install -e ".[vision]"`，或 `kb setup --with-vision` |
| Ollama 未安装/模型没下载 | `kb model install --name qwen2.5:3b`；或 `kb setup --with-ollama` |
| 模型回答 500 / GGML 断言 | 把 `config/config.json` 的 `models.local.num_gpu` 设为 `0`，重启 Ollama |
| 模型回答很慢 | CPU 推理正常；可换 `qwen2.5:1.5b`，或减少来源数量 |
| macOS 无法读取桌面/文稿/`~/Documents` | 见下方「macOS 权限（TCC）排查」 |
| 端口被占用 / `address already in use` | 先运行 `kb stop`；仍有残留用 `kb stop --force`。也可以手动查：macOS `lsof -nP -iTCP:8766 -sTCP:LISTEN`，Windows `netstat -ano \| findstr :8766` |
| 重复运行 `kb start` | 新版会先关闭上一次启动的服务并检查端口；如果端口被其他程序占用，会直接提示 PID |
| 服务反复退出 / 看不到报错 | `kb logs` 看四个日志；自动重启的原因也在里面 |
| 索引很慢 | 缩小范围，排除大目录；首次全盘索引建议夜间跑 |

### macOS 权限（TCC）排查

如果启动时报 `Operation not permitted`、`无法读取 .venv/pyvenv.cfg`，或索引时大量目录被跳过，是 macOS 的隐私保护在拦截终端访问 `~/Documents`、桌面等目录，不是程序逻辑错误。

快捷方式：运行 `kb setup`，检测到权限被拒绝时会询问并直接打开对应设置页；也可以双击 `一键启动.command`。

1. 打开 系统设置 → 隐私与安全性 → 完全磁盘访问权限。
2. 给运行安装/服务的终端（Terminal / iTerm 等）打开开关；如果通过 AI 客户端启动，也给它授权。
3. **完全退出**终端 / AI 客户端后重新打开（只关窗口不算）。
4. 验证：

```bash
cd /path/to/personal-kb
head -c 20 .venv/pyvenv.cfg
.venv/bin/python -c "print('python ok')"
```

`start.sh` 已内置这项检查：读不到 `.venv/pyvenv.cfg` 时会直接打印处理办法并退出，不再只抛 Python fatal error。

如果不想开放完全磁盘访问权限，可以把整个 `personal-kb` 移到不受保护的目录（例如 `~/personal-kb`），删除 `.venv` 后重新运行 `bash install.sh`。
