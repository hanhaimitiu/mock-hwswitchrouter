"""设备状态模型与工具函数。

Device 保存一台被模拟的华为交换机/路由器的运行配置，
支持 to_dict / from_dict 以便用 JSON 持久化。
"""

import json


# ----------------------------- IP / 掩码工具 -----------------------------

def is_ipv4(s):
    parts = s.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit():
            return False
        n = int(p)
        if n < 0 or n > 255:
            return False
    return True


def mask_to_prefix(mask):
    """点分掩码 -> 前缀长度(0-32)，非法掩码返回 None。"""
    try:
        parts = [int(x) for x in mask.split(".")]
        if len(parts) != 4:
            return None
        bits = "".join("{:08b}".format(p) for p in parts)
        if "01" in bits:
            return None
        return bits.count("1")
    except Exception:
        return None


def prefix_to_mask(p):
    """前缀长度 -> 点分掩码。"""
    bits = "1" * p + "0" * (32 - p)
    return ".".join(str(int(bits[i:i + 8], 2)) for i in range(0, 32, 8))


def ip_to_int(ip):
    parts = [int(x) for x in ip.split(".")]
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def int_to_ip(n):
    return ".".join(str((n >> s) & 0xFF) for s in (24, 16, 8, 0))


def normalize_mask(arg):
    """接受点分掩码或前缀长度，返回 (dotted_mask, prefix)。"""
    if is_ipv4(arg):
        p = mask_to_prefix(arg)
        if p is None:
            return None, None
        return arg, p
    if arg.isdigit() and 0 <= int(arg) <= 32:
        p = int(arg)
        return prefix_to_mask(p), p
    return None, None


# ------------------------------- Device ----------------------------------

class Device:
    def __init__(self, device_type="switch"):
        self.device_type = device_type
        self.hostname = "Huawei"
        self.version = "VRP (R) software, Version 5.170 (S5700 V200R003C00SPC200)"
        self.model = "S5700-28P-LI-AC" if device_type == "switch" else "AR2220"
        self.telnet_server = False
        self.stelnet_server = False
        self.ui_auth_mode = "none"      # none / password / aaa
        self.ui_password = ""
        self.interfaces = {}
        self.vlans = {}
        self.static_routes = []
        self.users = []
        self._init_default()

    # ---- 默认配置 ----
    def _new_if(self, itype, number):
        return {
            "type": itype,
            "number": number,
            "ip_address": None,
            "mask": None,
            "prefix": None,
            "description": "",
            "shutdown": False,
            "link_type": "access" if itype == "GigabitEthernet" else None,
            "pvid": 1,
            "trunk_vlans": [1],
        }

    def _init_default(self):
        self.interfaces["MEth0/0/1"] = self._new_if("MEth", "0/0/1")
        self.interfaces["Vlanif1"] = self._new_if("Vlanif", "1")
        self.interfaces["LoopBack0"] = self._new_if("LoopBack", "0")
        self.interfaces["NULL0"] = self._new_if("NULL", "0")
        if self.device_type == "switch":
            for i in range(1, 25):
                self.interfaces["GigabitEthernet0/0/%d" % i] = self._new_if("GigabitEthernet", "0/0/%d" % i)
        else:
            for i in range(0, 3):
                self.interfaces["GigabitEthernet0/0/%d" % i] = self._new_if("GigabitEthernet", "0/0/%d" % i)
            self.interfaces["Ethernet0/0/0"] = self._new_if("Ethernet", "0/0/0")
        self.vlans = {"1": {"description": "", "status": "enable"}}

    # ---- 序列化 ----
    def to_dict(self):
        return {
            "device_type": self.device_type,
            "hostname": self.hostname,
            "version": self.version,
            "model": self.model,
            "telnet_server": self.telnet_server,
            "stelnet_server": self.stelnet_server,
            "ui_auth_mode": self.ui_auth_mode,
            "ui_password": self.ui_password,
            "interfaces": self.interfaces,
            "vlans": self.vlans,
            "static_routes": self.static_routes,
            "users": self.users,
        }

    def from_dict(self, d):
        self.device_type = d.get("device_type", self.device_type)
        self.hostname = d.get("hostname", "Huawei")
        self.version = d.get("version", self.version)
        self.model = d.get("model", self.model)
        self.telnet_server = d.get("telnet_server", False)
        self.stelnet_server = d.get("stelnet_server", False)
        self.ui_auth_mode = d.get("ui_auth_mode", "none")
        self.ui_password = d.get("ui_password", "")
        loaded = d.get("interfaces", {})
        self._init_default()
        for k, v in loaded.items():
            if k in self.interfaces:
                self.interfaces[k].update(v)
            else:
                self.interfaces[k] = v
        self.vlans = d.get("vlans", {"1": {"description": "", "status": "enable"}})
        self.static_routes = d.get("static_routes", [])
        self.users = d.get("users", [])
