# 华为交换机 / 路由器命令模拟终端

一个用 **纯 Python 标准库** 实现的华为 VRP（Versatile Routing Platform）风格命令行模拟器，用来在电脑上练习交换机 / 路由器的配置命令。零第三方依赖，装好 Python 就能跑。

## 特性

- **多级视图**：用户视图 `<Huawei>` → 系统视图 `[Huawei]` → 接口视图 `[Huawei-GigabitEthernet0/0/1]`、`vlan` / `aaa` / `user-interface` 视图，`quit` 退一级、`return` 直接回用户视图。
- **命令集**：`system-view`、`sysname`、`interface` + `ip address` / `description` / `shutdown` / `undo ...`、`vlan` / `vlan batch`、`port link-type` / `default vlan` / `trunk allow-pass`、`ip route-static`、`aaa` + `local-user`、`display` 系列、`ping` / `tracert` / `dir` 等。
- **前缀缩写**：`sys`→`system-view`、`dis`→`display`、`int`→`interface`、`und`→`undo` 都支持。
- **`undo` 撤销**：`undo ip address`、`undo shutdown`、`undo sysname`、`undo vlan 10` 等。
- **上下文帮助**：在命令任意位置输入 `?` 都会列出下一步可填的参数（如 `port ?`、`display ip ?`）。
- **Tab 自动补全 + 历史**：支持命令名、接口名、VLAN 号、子命令补全，上下方向键浏览历史，左右方向键移动光标，Home/End 跳到行首/行尾，Backspace / Delete 删除任意位置字符。
- **多设备持久化**：以 JSON 保存每台设备配置到 `data/devices/<类型>/<名称>.json`，跨重启保留；启动时选择「交换机 / 路由器」以及具体设备（已有设备或新建）。

## 安装

只需 Python 3.8+，无需安装任何第三方库。

```bash
# 克隆（可选，若你已有目录则跳过）
git clone git@github.com:hanhaimitiu/mock-hwswitchrouter.git
cd mock-hwswitchrouter
```

## 使用

直接运行：

```bash
python main.py
```

启动后会先让你选择：

1. **设备类型**：`1` 交换机 / `2` 路由器
2. **具体设备**：列出该类型下已有的设备，可输入编号选择，**直接输入一个名字即为新建**，或回车 / `0` 新建一台

之后进入命令行练习模式。退出输入 `quit`。

也可以用命令行参数跳过交互：

```bash
python main.py --device router   # 直接进入一台默认路由器
python main.py --reset           # 忽略已保存配置，从默认配置启动一台新设备
```

## 常用命令示例

```text
# 交换机配置一台接入端口 + 划 VLAN
<Huawei> system-view
[Huawei] vlan 10
[Huawei-vlan10] quit
[Huawei] interface g0/0/1
[Huawei-GigabitEthernet0/0/1] port link-type access
[Huawei-GigabitEthernet0/0/1] port default vlan 10
[Huawei-GigabitEthernet0/0/1] quit
[Huawei] save        # 写入 JSON 持久化

# 给接口配置 IP（三层接口）
[Huawei] interface Vlanif1
[Huawei-Vlanif1] ip address 192.168.1.1 24
[Huawei-Vlanif1] quit

# 静态路由
[Huawei] ip route-static 0.0.0.0 0 192.168.1.254

# 查看
<Huawei> display version
<Huawei> display current-configuration
<Huawei> display ip interface brief
<Huawei> display vlan
<Huawei> display ip routing-table
```

## 持久化说明

- 配置保存在 `data/devices/<switch|router>/<设备名>.json`，模拟设备 NVRAM。
- `save` 写入当前设备；`reboot` 从 JSON 重新加载；`reset saved-configuration` 清除当前设备的已保存配置。
- `display saved-configuration` 查看已落盘的内容；`dir` 查看当前设备文件信息。
- 旧版单文件 `data/saved_config.json` 会在首次启动时自动迁移进多设备仓库。

## 目录结构

```
main.py          # 入口：启动选择 + 主循环
cli.py           # 命令解析、多级视图、undo、display 输出、补全候选、上下文帮助
device.py        # 设备状态模型 + IP/掩码工具 + JSON 序列化
persistence.py   # 多设备 JSON 持久化（设备仓库）
linereader.py    # 跨平台行读取器：Tab 补全、方向键、历史（Windows 用 msvcrt，Unix 用 tty）
data/devices/    # 已保存的设备配置（运行时生成）
```

## 快捷键（真实终端下生效）

| 按键 | 功能 |
| --- | --- |
| `Tab` | 自动补全命令 / 参数 |
| `↑` / `↓` | 浏览历史命令 |
| `←` / `→` | 移动光标 |
| `Home` / `End` | 行首 / 行尾 |
| `Backspace` / `Delete` | 删除光标前 / 光标处字符 |
| `?` | 上下文帮助 |
| `Ctrl+C` | 中断 |

> 说明：自动补全与方向键需在真实控制台（直接在终端运行 `python main.py`）下使用；管道 / 重定向输入时会退化为普通输入。

## License

MIT
