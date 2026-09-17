#!/usr/bin/env python3
"""华为交换机/路由器命令模拟终端。

启动流程（无参数时交互式）:
    1) 选择设备类型: 交换机 / 路由器
    2) 选择具体设备: 已有的(可继续) 或 新建一台

也可命令行指定类型:
    python main.py                 # 进入交互式选择
    python main.py --device router # 直接进入一台默认路由器
    python main.py --reset         # 进入一台全新的默认设备(忽略已保存)

特性:
    - 华为 VRP 风格的多级视图与命令(支持前缀缩写、undo)
    - Tab 自动补全 / 上下键历史(真实控制台可用)
    - 配置按设备持久化到 JSON (data/devices/<类型>/<名称>.json)
"""

import argparse
import re
import sys
import datetime

from device import Device
from persistence import (
    migrate_old, list_devices, load_device, device_info,
)
from linereader import LineReader
from cli import CLI


def banner():
    print("=" * 64)
    print("   Huawei VRP CLI Simulator  -  华为交换机/路由器命令练习终端")
    print("   持久化: JSON (data/devices/<类型>/<名称>.json)   自动补全: 按 Tab")
    print("=" * 64)


def prompt_type():
    while True:
        print("")
        print("  请选择设备类型：")
        print("    1) 交换机 (switch)")
        print("    2) 路由器 (router)")
        ans = input("  请输入 1 或 2: ").strip()
        if ans in ("1", "switch", "交换机"):
            return "switch"
        if ans in ("2", "router", "路由器"):
            return "router"
        print("  输入无效，请重试。")


def prompt_device(dtype):
    devices = list_devices(dtype)
    label = "交换机" if dtype == "switch" else "路由器"
    print("")
    if devices:
        print("  已有的%s设备：" % label)
        for i, n in enumerate(devices, 1):
            info = device_info(dtype, n)
            mt = datetime.datetime.fromtimestamp(info["mtime"]).strftime("%Y-%m-%d %H:%M") if info else ""
            print("    %d) %s   (%s)" % (i, n, mt))
        print("    0) 新建一台%s" % label)
        while True:
            ans = input("  选择编号、输入设备名，或回车新建(默认 0): ").strip()
            if ans == "" or ans == "0":
                return None
            if ans.isdigit() and 1 <= int(ans) <= len(devices):
                return devices[int(ans) - 1]
            if ans in devices:
                return ans
            # 不在列表里 → 当作新设备名
            return ans
    else:
        print("  没有已保存的%s设备，将新建一台。" % label)
        return None


def build_startup(args):
    migrate_old()

    # 命令行快捷模式
    if args.device:
        dtype = args.device
        name = "default"
        saved = None if args.reset else load_device(dtype, name)
        if saved:
            dev = Device(saved.get("device_type", dtype))
            dev.from_dict(saved)
            return dev, name, "The device is running with the saved configuration."
        dev = Device(dtype)
        return dev, name, "The device is running with the default configuration."

    # 交互模式
    dtype = prompt_type()
    name = prompt_device(dtype)
    if name is None:
        while True:
            raw = input("  请输入新设备名称 (字母/数字/减号/下划线, 默认 Huawei): ").strip()
            name = re.sub(r'[^A-Za-z0-9\-_]', '_', raw) or "Huawei"
            if name not in list_devices(dtype):
                break
            print("  该名称已存在，请换一个或直接从列表中选择。")
        dev = Device(dtype)
        boot = "The device is running with the default configuration."
    else:
        saved = load_device(dtype, name)
        if saved:
            dev = Device(saved.get("device_type", dtype))
            dev.from_dict(saved)
            boot = "The device is running with the saved configuration."
        else:
            # 输入了一个列表中没有的名字 -> 视为新建一台设备
            dev = Device(dtype)
            boot = "The device is running with the default configuration."
    return dev, name, boot


def main():
    parser = argparse.ArgumentParser(description="Huawei VRP CLI Simulator")
    parser.add_argument("--device", choices=["switch", "router"],
                        help="设备类型: switch / router（指定后跳过交互选择）")
    parser.add_argument("--reset", action="store_true",
                        help="忽略已保存配置，从默认配置启动一台新设备")
    args = parser.parse_args()

    if not args.device:
        banner()

    device, name, boot_msg = build_startup(args)

    if not args.device:
        print("")
        print("  已选择设备: [%s] %s" % (
            "交换机" if device.device_type == "switch" else "路由器", name))
    print("  Model: %s" % device.model)
    print("  " + boot_msg)
    print("  输入 '?' 或 'help' 查看当前视图命令；'quit' 退出；'save' 保存配置。")
    print("")

    reader = LineReader()
    cli = CLI(device, reader, name)
    try:
        cli.run()
    except KeyboardInterrupt:
        print("\nBye.")
    sys.exit(0)


if __name__ == "__main__":
    main()
