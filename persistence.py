"""多设备 JSON 持久化：每台设备保存在 data/devices/<类型>/<名称>.json，
模拟多台交换机的 NVRAM。启动时选择类型与具体设备，save 写入对应文件，
reboot 从该文件重载，从而实现跨重启、跨设备的持久化。

同时兼容旧版单文件 data/saved_config.json（首次启动时自动迁移进仓库）。
"""

import os
import re
import json
import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEVICES_DIR = os.path.join(DATA_DIR, "devices")
OLD_SAVED = os.path.join(DATA_DIR, "saved_config.json")


def ensure_dir():
    os.makedirs(os.path.join(DEVICES_DIR, "switch"), exist_ok=True)
    os.makedirs(os.path.join(DEVICES_DIR, "router"), exist_ok=True)


def _fname(name):
    """设备名 -> 安全文件名（去掉路径分隔等非法字符）。"""
    safe = re.sub(r'[^A-Za-z0-9\-_]', '_', name)
    return safe + ".json"


def list_devices(device_type):
    ensure_dir()
    d = os.path.join(DEVICES_DIR, device_type)
    if not os.path.isdir(d):
        return []
    return sorted(fn[:-5] for fn in os.listdir(d) if fn.endswith(".json"))


def device_info(device_type, name):
    p = os.path.join(DEVICES_DIR, device_type, _fname(name))
    if not os.path.exists(p):
        return None
    st = os.stat(p)
    return {"size": st.st_size, "mtime": st.st_mtime}


def save_device(device, name):
    ensure_dir()
    p = os.path.join(DEVICES_DIR, device.device_type, _fname(name))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(device.to_dict(), f, indent=2, ensure_ascii=False)
    return p


def load_device(device_type, name):
    p = os.path.join(DEVICES_DIR, device_type, _fname(name))
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def delete_device(device_type, name):
    p = os.path.join(DEVICES_DIR, device_type, _fname(name))
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def migrate_old():
    """首次启动时把旧版单文件 saved_config.json 搬进新设备仓库（仅一次）。"""
    if not os.path.exists(OLD_SAVED):
        return
    try:
        with open(OLD_SAVED, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return
    dt = d.get("device_type", "switch")
    name = d.get("hostname", "Huawei")
    if name in list_devices(dt):
        os.remove(OLD_SAVED)
        return
    ensure_dir()
    p = os.path.join(DEVICES_DIR, dt, _fname(name))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.remove(OLD_SAVED)
