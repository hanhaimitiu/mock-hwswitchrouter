"""华为 VRP 风格 CLI 引擎。

负责：视图栈(user/system/interface/vlan/aaa/ui)、命令解析与前缀匹配、
undo 处理、display 系列输出、以及 Tab 补全候选生成。
"""

import json
import datetime

from device import (
    is_ipv4, mask_to_prefix, prefix_to_mask, ip_to_int, int_to_ip, normalize_mask,
)
from persistence import (
    save_device, load_device, delete_device, device_info, list_devices,
)
from device import Device


MODES_ALL = ["user", "system", "interface", "vlan", "aaa", "ui"]

# (命令名, 可用视图, 帮助, 别名)
CMD_SPECS = [
    ("system-view", ["user"], "Enter the system view.", ["sys", "system"]),
    ("display", MODES_ALL, "Display information.", ["dis"]),
    ("quit", MODES_ALL, "Exit from current view.", []),
    ("return", MODES_ALL, "Return to user view.", []),
    ("save", ["user", "system"], "Save the current configuration.", []),
    ("reset", ["user"], "Reset the saved configuration file.", []),
    ("reboot", ["user"], "Reboot the device.", []),
    ("ping", ["user"], "Ping a remote host.", []),
    ("tracert", ["user"], "Trace the route to a remote host.", []),
    ("dir", ["user"], "List files in the device.", []),
    ("sysname", ["system"], "Set the device host name.", []),
    ("interface", ["system"], "Enter the interface view.", ["int"]),
    ("vlan", ["system"], "Create a VLAN and enter its view.", []),
    ("ip", ["system", "interface"], "Configure IP.", []),
    ("aaa", ["system"], "Enter the AAA view.", []),
    ("user-interface", ["system"], "Enter the user-interface view.", ["ui"]),
    ("stelnet", ["system"], "STelnet (SSH) server configuration.", []),
    ("telnet", ["system"], "Telnet server configuration.", []),
    ("shutdown", ["interface"], "Shut down the interface.", []),
    ("description", ["interface", "vlan"], "Set the description.", []),
    ("port", ["interface"], "Port link-type and VLAN assignment.", []),
    ("local-user", ["aaa"], "Local user configuration.", []),
    ("authentication-mode", ["ui"], "Set authentication mode.", ["auth"]),
    ("set", ["ui"], "Set configurations in the UI view.", []),
    ("undo", ["system", "interface", "vlan", "aaa", "ui"], "Cancel a configuration.", []),
    ("help", MODES_ALL, "Show help.", ["?"]),
]


class CLI:
    def __init__(self, device, reader, device_name="default"):
        self.device = device
        self.reader = reader
        self.device_name = device_name
        self.stack = [("user", {})]
        self._running = True
        # 命令索引: key(命令名/别名) -> (name, modes, aliases)
        self._index = {}
        self._help = {}
        for name, modes, help_text, aliases in CMD_SPECS:
            self._index[name] = (name, modes, aliases)
            self._help[name] = help_text
            for a in aliases:
                self._index[a] = (name, modes, aliases)
        reader.set_completer(self._complete)

    # --------------------------- 视图/提示符 ---------------------------
    @property
    def mode(self):
        return self.stack[-1][0]

    @property
    def ctx(self):
        return self.stack[-1][1]

    def prompt(self):
        h = self.device.hostname
        m, c = self.stack[-1]
        if m == "user":
            return "<%s>" % h
        if m == "system":
            return "[%s]" % h
        if m == "interface":
            return "[%s-%s]" % (h, c["if"])
        if m == "vlan":
            return "[%s-vlan%s]" % (h, c["vid"])
        if m == "aaa":
            return "[%s-aaa]" % h
        if m == "ui":
            return "[%s-ui-%s]" % (h, c["name"])
        return "<%s>" % h

    # ------------------------------ 主循环 ------------------------------
    def run(self):
        while self._running:
            try:
                line = self.reader.read_line(self.prompt())
            except KeyboardInterrupt:
                print()
                break
            if line is None:
                print()
                break
            self.execute(line.rstrip("\n"))

    # ----------------------------- 解析执行 -----------------------------
    def execute(self, line):
        line = line.strip()
        if line == "":
            return
        if line in ("\x1a", "^Z"):
            self._return_to_user()
            return
        tokens = line.split()
        # 上下文帮助：命令中任意位置出现 "?" (如 "port ?"、"display ip ?")
        if any(t.endswith("?") for t in tokens):
            idx = next(i for i, t in enumerate(tokens) if t.endswith("?"))
            prefix = tokens[:idx]
            tail = tokens[idx].rstrip("?")
            if tail:
                prefix.append(tail)
            self.cmd_context_help(prefix)
            return
        word = tokens[0].lower()

        if word == "undo":
            self.cmd_undo(tokens[1:])
            return
        if word == "?" or word == "help":
            self.cmd_help()
            return
        if word == "quit" or word == "return":
            self.cmd_quit_or_return(word)
            return

        handler = self._lookup(word)
        if handler is None:
            matches = self._lookup_all(word)
            if matches:
                print("Error: Ambiguous command found at '^' position.")
            else:
                print("Error: Unrecognized command found at '^' position.")
            return
        handler(tokens[1:])

    def _lookup(self, word):
        cands = []
        for key, (name, modes, _a) in self._index.items():
            if self.mode not in modes:
                continue
            if key == word or key.startswith(word):
                cands.append(name)
        cands = list(dict.fromkeys(cands))
        if len(cands) == 1:
            return getattr(self, "cmd_" + cands[0].replace("-", "_"))
        return None

    def _lookup_all(self, word):
        cands = []
        for key, (name, modes, _a) in self._index.items():
            if self.mode not in modes:
                continue
            if key == word or key.startswith(word):
                cands.append(name)
        return list(dict.fromkeys(cands))

    # ------------------------- 通用辅助 -------------------------
    def _confirm(self, msg):
        try:
            ans = input(msg + " ").strip().lower()
        except EOFError:
            return False
        return ans in ("y", "yes")

    def _err(self, msg="Error: Unrecognized command."):
        print(msg)

    def _is_dirty(self):
        saved = load_device(self.device.device_type, self.device_name)
        if saved is None:
            return True
        return json.dumps(self.device.to_dict(), sort_keys=True) != json.dumps(saved, sort_keys=True)

    @staticmethod
    def _expand_interface(arg):
        a = arg
        low = a.lower()
        mapping = [
            ("gigabitethernet", "GigabitEthernet"),
            ("ge", "GigabitEthernet"),
            ("gi", "GigabitEthernet"),
            ("g", "GigabitEthernet"),
            ("ethernet", "Ethernet"),
            ("e", "Ethernet"),
            ("vlanif", "Vlanif"),
            ("meth", "MEth"),
            ("me", "MEth"),
            ("loopback", "LoopBack"),
            ("loop", "LoopBack"),
            ("lo", "LoopBack"),
            ("null", "NULL0"),
            ("n", "NULL0"),
        ]
        for pfx, full in mapping:
            if low.startswith(pfx):
                return full + low[len(pfx):]
        return a

    # ------------------------- 视图切换 -------------------------
    def _return_to_user(self):
        self.stack = [("user", {})]

    def cmd_quit_or_return(self, word):
        if word == "return":
            self._return_to_user()
            return
        # quit
        if self.mode == "user":
            if self._is_dirty():
                if not self._confirm("The configuration has not been saved. Are you sure to quit?[Y/N]"):
                    return
            self._running = False
            print("The device is now stopped.")
        else:
            self.stack.pop()

    # ------------------------- 命令实现 -------------------------
    def cmd_system_view(self, tokens):
        self.stack.append(("system", {}))

    def cmd_help(self):
        print("Available commands in %s view:" % self.mode)
        seen = set()
        for key, (name, modes, _a) in self._index.items():
            if self.mode not in modes or name in seen:
                continue
            seen.add(name)
            help_text = self._help.get(name, "")
            print("  %-18s %s" % (name, help_text))
        print("  (press Tab to autocomplete; 'undo <cmd>' to revert)")

    def cmd_context_help(self, prefix):
        """命令中间的 ? —— 按已输入前缀显示下一步可用参数。"""
        words = [w.lower() for w in prefix]
        if not words:
            self.cmd_help()
            return
        head = words[0]
        out = None
        if head in ("display", "dis"):
            out = [
                ("version", "System version information"),
                ("current-configuration", "Current configuration"),
                ("saved-configuration", "Saved configuration"),
                ("ip interface brief", "Interface IP status"),
                ("ip routing-table", "Routing table"),
                ("interface [name]", "Interface information"),
                ("vlan", "VLAN information"),
                ("this", "Current view configuration"),
                ("local-user", "Local user list"),
            ]
        elif head in ("interface", "int"):
            out = [("<name>", "GigabitEthernet0/0/1, Vlanif1 ...")]
        elif head == "port":
            if len(words) == 1:
                out = [("link-type", "Port link type"),
                       ("default", "Default VLAN of the port"),
                       ("trunk", "Trunk attribute")]
            elif words[1] in ("link-type", "linktype", "link"):
                out = [("access", "Access mode"), ("trunk", "Trunk mode"), ("hybrid", "Hybrid mode")]
            elif words[1] == "default":
                out = [("vlan", "Specify the default VLAN")]
                if len(words) >= 3:
                    out = [("<1-4094>", "VLAN ID")]
            elif words[1] == "trunk":
                out = [("allow-pass", "Permitted VLAN list")]
                if len(words) >= 3:
                    out = [("vlan", "VLAN list")]
                    if len(words) >= 4:
                        out = [("<1-4094>", "VLAN ID list")]
        elif head == "vlan":
            out = [("<1-4094>", "VLAN ID"), ("batch", "Create VLANs in batch")]
        elif head == "ip":
            if len(words) == 1:
                out = ([("address", "Set the IP address")] if self.mode == "interface" else []) \
                      + [("route-static", "Static route")]
            elif words[1] == "address":
                out = [("<A.B.C.D>", "IP address"), ("<mask>", "e.g. 255.255.255.0 or 24")]
            elif words[1] in ("route-static", "route", "routing"):
                out = [("<dest>", "Destination network"), ("<mask>", "Mask or prefix length"),
                       ("<nexthop>", "Next hop address"), ("<pref>", "Preference 1-255, default 60")]
        elif head == "undo":
            out = [(s, "Cancel") for s in
                   ("sysname", "shutdown", "description", "ip", "vlan",
                    "local-user", "port", "stelnet", "telnet", "authentication-mode")]
        elif head == "sysname":
            out = [("<name>", "Host name")]
        elif head == "local-user":
            if len(words) == 1:
                out = [("<name>", "User name")]
            else:
                out = [("password cipher", "Set password"),
                       ("service-type", "telnet/ssh/http ..."),
                       ("privilege level", "Level 0-15")]
        elif head == "user-interface":
            out = [("console 0", "Console port"), ("vty <first> [last]", "VTY lines, e.g. vty 0 4")]
        elif head == "authentication-mode":
            out = [("password", "Password authentication"), ("aaa", "AAA authentication")]
        elif head == "set":
            out = [("authentication password cipher", "Set the login password")]
        elif head in ("ping", "tracert"):
            out = [("<host>", "IP address or host name")]
        elif head in ("stelnet", "telnet"):
            out = [("server enable/disable", "Enable or disable the server")]
        if out is None:
            print("Error: Unrecognized command.")
            return
        for k, v in out:
            print("  %-24s %s" % (k, v))

    def cmd_display(self, tokens):
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        sub = tokens[0].lower()
        if sub in ("version", "ver", "v"):
            self._show_version()
        elif sub in ("current-configuration", "cu", "configuration"):
            self._show_config(self.device)
        elif sub in ("saved-configuration", "saved"):
            saved = load_device(self.device.device_type, self.device_name)
            if saved is None:
                print("No saved configuration found.")
            else:
                d = Device(saved.get("device_type", "switch"))
                d.from_dict(saved)
                self._show_config(d)
        elif sub == "ip":
            if len(tokens) >= 2 and tokens[1].lower() in ("interface", "int"):
                if len(tokens) >= 3 and tokens[2].lower() in ("brief", "br", "b"):
                    self._show_ip_int_brief()
                else:
                    name = tokens[2] if len(tokens) >= 3 else None
                    self._show_ip_interface(name)
            elif len(tokens) >= 2 and tokens[1].lower() in ("routing-table", "routing", "route"):
                self._show_routing()
            else:
                self._err("Error: Unrecognized command.")
        elif sub == "interface":
            name = tokens[1] if len(tokens) >= 2 else None
            self._show_ip_interface(name)
        elif sub == "vlan":
            self._show_vlan()
        elif sub in ("routing-table", "routing", "route"):
            self._show_routing()
        elif sub == "this":
            self._show_this()
        elif sub in ("local-user", "local"):
            self._show_local_user()
        else:
            self._err("Error: Unrecognized command.")

    def cmd_save(self, tokens):
        force = any(t.lower() == "force" for t in tokens)
        if not force:
            if not self._confirm("The current configuration will be written to the device.\n Are you sure to continue?[Y/N]"):
                print("Save cancelled.")
                return
        path = save_device(self.device, self.device_name)
        print("Now saving the current configuration to the device.")
        print("Save the configuration successfully.")
        print("File: %s" % path)

    def cmd_reset(self, tokens):
        if not self._confirm("The saved configuration file will be erased. Are you sure?[Y/N]"):
            return
        delete_device(self.device.device_type, self.device_name)
        print("The saved configuration file is erased.")
        print("Warning: the device will load the default configuration on next reboot.")

    def cmd_reboot(self, tokens):
        if not self._confirm("The system will reboot. Continue?[Y/N]"):
            return
        print("System is rebooting, please wait ...")
        saved = load_device(self.device.device_type, self.device_name)
        if saved:
            self.device = Device(saved.get("device_type", self.device.device_type))
            self.device.from_dict(saved)
            print("... Configuration is loaded from the saved file.")
        else:
            self.device = Device(self.device.device_type)
            print("... No saved configuration, loaded default configuration.")
        self.stack = [("user", {})]

    def cmd_ping(self, tokens):
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        target = tokens[0]
        if not is_ipv4(target) and not self._is_configured_ip(target):
            print("  PING %s: Error: Unknown host %s." % (target, target))
            return
        print("  PING %s: 56  data bytes, press CTRL_C to break" % target)
        for i in range(1, 6):
            print("    Reply from %s: bytes=56 Sequence=%d ttl=255 time=%d ms" % (target, i, i))
        print("")
        print("  --- %s ping statistics ---" % target)
        print("    5 packet(s) transmitted, 5 packet(s) received, 0.00%% packet loss")
        print("    round-trip min/avg/max = 1/3/5 ms")

    def cmd_tracert(self, tokens):
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        target = tokens[0]
        print(" traceroute to %s, 30 hops max, 40 bytes packet" % target)
        for i in range(1, 5):
            print("  %d  %s  %d ms  %d ms  %d ms" % (i, target, i, i + 1, i))

    def cmd_dir(self, tokens):
        dtype = self.device.device_type
        print("Directory of devices/%s/:" % dtype)
        info = device_info(dtype, self.device_name)
        if info:
            mt = datetime.datetime.fromtimestamp(info["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
            print("  %-22s %-10d %s" % (self.device_name + ".json", info["size"], mt))
        else:
            print("  (%s.json not saved yet)" % self.device_name)
        others = [d for d in list_devices(dtype) if d != self.device_name]
        if others:
            print("Other %s devices: %s" % (dtype, ", ".join(others)))

    def cmd_sysname(self, tokens, undo=False):
        if undo:
            self.device.hostname = "Huawei"
            return
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        self.device.hostname = tokens[0]

    def cmd_interface(self, tokens):
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        arg = tokens[0]
        if len(tokens) >= 2 and tokens[0].lower() in (
            "gigabitethernet", "ethernet", "vlanif", "meth", "loopback", "null"
        ):
            arg = tokens[0] + tokens[1]
        ifname = self._expand_interface(arg)
        if ifname in self.device.interfaces:
            self.stack.append(("interface", {"if": ifname}))
        else:
            print("Error: The interface %s does not exist." % ifname)

    def cmd_vlan(self, tokens, undo=False):
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        if tokens[0].lower() == "batch":
            for v in tokens[1:]:
                if v.isdigit():
                    self.device.vlans.setdefault(v, {"description": "", "status": "enable"})
            return
        if undo:
            vid = tokens[0]
            if vid == "1":
                print("Error: VLAN 1 cannot be deleted.")
                return
            if vid in self.device.vlans:
                del self.device.vlans[vid]
            else:
                self._err("Error: The VLAN does not exist.")
            return
        vid = tokens[0]
        if not vid.isdigit():
            self._err("Error: Wrong parameter.")
            return
        self.device.vlans.setdefault(vid, {"description": "", "status": "enable"})
        self.stack.append(("vlan", {"vid": vid}))

    def cmd_ip(self, tokens, undo=False):
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        sub = tokens[0].lower()
        if sub == "address":
            if self.mode != "interface":
                print("Error: This command can only be used in the interface view.")
                return
            iface = self.device.interfaces[self.ctx["if"]]
            if undo:
                iface["ip_address"] = None
                iface["mask"] = None
                iface["prefix"] = None
                return
            if len(tokens) < 3:
                self._err("Error: Incomplete command.")
                return
            ip = tokens[1]
            mask, prefix = normalize_mask(tokens[2])
            if not is_ipv4(ip) or mask is None:
                print("Error: Invalid IP address or mask.")
                return
            iface["ip_address"] = ip
            iface["mask"] = mask
            iface["prefix"] = prefix
        elif sub == "route-static":
            if undo:
                if len(tokens) < 3:
                    self._err("Error: Incomplete command.")
                    return
                dest = tokens[1]
                mask, _ = normalize_mask(tokens[2])
                nexthop = tokens[3] if len(tokens) >= 4 else None
                before = len(self.device.static_routes)
                self.device.static_routes = [
                    r for r in self.device.static_routes
                    if not (r["dest"] == dest and r["mask"] == mask and (nexthop is None or r["nexthop"] == nexthop))
                ]
                if len(self.device.static_routes) == before:
                    print("Error: The static route does not exist.")
                return
            if len(tokens) < 4:
                self._err("Error: Incomplete command.")
                return
            dest = tokens[1]
            mask, prefix = normalize_mask(tokens[2])
            nexthop = tokens[3]
            if not is_ipv4(dest) or mask is None or not is_ipv4(nexthop):
                print("Error: Invalid destination, mask or nexthop.")
                return
            pref = int(tokens[4]) if len(tokens) >= 5 and tokens[4].isdigit() else 60
            exists = any(r["dest"] == dest and r["mask"] == mask and r["nexthop"] == nexthop
                          for r in self.device.static_routes)
            if exists:
                print("Info: The static route already exists.")
            else:
                self.device.static_routes.append(
                    {"dest": dest, "mask": mask, "prefix": prefix, "nexthop": nexthop, "pref": pref}
                )
        else:
            self._err("Error: Unrecognized command.")

    def cmd_aaa(self, tokens):
        self.stack.append(("aaa", {}))

    def cmd_user_interface(self, tokens):
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        kind = tokens[0].lower()
        if kind == "console":
            name = "console0"
        elif kind == "vty":
            lo = tokens[1] if len(tokens) >= 2 else "0"
            hi = tokens[2] if len(tokens) >= 3 else lo
            name = "vty%s-%s" % (lo, hi)
        else:
            self._err("Error: Wrong parameter.")
            return
        self.stack.append(("ui", {"name": name}))

    def cmd_stelnet(self, tokens):
        self._server_cmd(tokens, "stelnet")

    def cmd_telnet(self, tokens):
        self._server_cmd(tokens, "telnet")

    def _server_cmd(self, tokens, which):
        if not tokens or tokens[0].lower() != "server":
            self._err("Error: Incomplete command.")
            return
        if len(tokens) < 2:
            self._err("Error: Incomplete command.")
            return
        action = tokens[1].lower()
        if action in ("enable", "disable"):
            val = action == "enable"
            if which == "stelnet":
                self.device.stelnet_server = val
            else:
                self.device.telnet_server = val
        else:
            self._err("Error: Wrong parameter.")

    def cmd_shutdown(self, tokens, undo=False):
        if self.mode != "interface":
            print("Error: This command can only be used in the interface view.")
            return
        iface = self.device.interfaces[self.ctx["if"]]
        iface["shutdown"] = not undo  # shutdown=True; undo shutdown -> up

    def cmd_description(self, tokens, undo=False):
        if self.mode == "interface":
            iface = self.device.interfaces[self.ctx["if"]]
            iface["description"] = "" if undo else " ".join(tokens)
        elif self.mode == "vlan":
            v = self.device.vlans[self.ctx["vid"]]
            v["description"] = "" if undo else " ".join(tokens)
        else:
            self._err("Error: This command is not allowed in this view.")

    def cmd_port(self, tokens, undo=False):
        if self.mode != "interface":
            print("Error: This command can only be used in the interface view.")
            return
        iface = self.device.interfaces[self.ctx["if"]]
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        sub = tokens[0].lower()
        if sub in ("link-type", "linktype", "link"):
            if undo:
                iface["link_type"] = None
                return
            lt = tokens[1].lower() if len(tokens) >= 2 else ""
            if lt in ("access", "trunk", "hybrid"):
                iface["link_type"] = lt
            else:
                self._err("Error: Wrong parameter.")
        elif sub == "default" and len(tokens) >= 3 and tokens[1].lower() == "vlan":
            if undo:
                iface["pvid"] = 1
                return
            vid = tokens[2]
            iface["pvid"] = int(vid) if vid.isdigit() else iface["pvid"]
        elif sub == "trunk" and len(tokens) >= 2 and tokens[1].lower() == "allow-pass":
            # port trunk allow-pass vlan <ids...>
            ids = [t for t in tokens[3:] if t.isdigit()]
            if undo:
                iface["trunk_vlans"] = [1]
            elif ids:
                iface["trunk_vlans"] = [int(x) for x in ids]
        else:
            self._err("Error: Unrecognized command.")

    def cmd_local_user(self, tokens, undo=False):
        if self.mode != "aaa":
            print("Error: This command can only be used in the AAA view.")
            return
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        name = tokens[0]
        if undo:
            self.device.users = [u for u in self.device.users if u["name"] != name]
            return
        user = next((u for u in self.device.users if u["name"] == name), None)
        if user is None:
            user = {"name": name, "password": "", "service_type": [], "level": 0}
            self.device.users.append(user)
        # sub commands
        rest = tokens[1:]
        if len(rest) >= 2 and rest[0].lower() == "password":
            # password cipher <pwd>
            if len(rest) >= 3 and rest[1].lower() in ("cipher", "simple"):
                user["password"] = rest[2]
        elif len(rest) >= 2 and rest[0].lower() == "service-type":
            user["service_type"] = [t.lower() for t in rest[1:]]
        elif len(rest) >= 3 and rest[0].lower() == "privilege" and rest[1].lower() == "level":
            if rest[2].isdigit():
                user["level"] = int(rest[2])

    def cmd_authentication_mode(self, tokens, undo=False):
        if self.mode != "ui":
            print("Error: This command can only be used in the UI view.")
            return
        if undo:
            self.device.ui_auth_mode = "none"
            return
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        mode = tokens[0].lower()
        if mode in ("password", "aaa"):
            self.device.ui_auth_mode = mode
        else:
            self._err("Error: Wrong parameter.")

    def cmd_set(self, tokens, undo=False):
        if self.mode != "ui":
            print("Error: This command can only be used in the UI view.")
            return
        if not tokens or tokens[0].lower() != "authentication" or len(tokens) < 3:
            self._err("Error: Incomplete command.")
            return
        if tokens[1].lower() == "password" and tokens[2].lower() in ("cipher", "simple"):
            self.device.ui_password = tokens[3] if len(tokens) >= 4 else ""
        else:
            self._err("Error: Wrong parameter.")

    def cmd_undo(self, tokens):
        if not tokens:
            self._err("Error: Incomplete command.")
            return
        target = tokens[0].lower()
        mapping = {
            "sysname": (self.cmd_sysname, [], True),
            "shutdown": (self.cmd_shutdown, [], True),
            "description": (self.cmd_description, [], True),
            "ip": (self.cmd_ip, [], True),
            "vlan": (self.cmd_vlan, [], True),
            "local-user": (self.cmd_local_user, [], True),
            "port": (self.cmd_port, [], True),
            "stelnet": (self.cmd_stelnet, [], True),
            "telnet": (self.cmd_telnet, [], True),
            "authentication-mode": (self.cmd_authentication_mode, [], True),
        }
        if target in mapping:
            handler, _, _ = mapping[target]
            handler(tokens[1:], undo=True)
        else:
            self._err("Error: Unrecognized command.")

    # ------------------------- display 输出 -------------------------
    def _is_configured_ip(self, ip):
        return any(i["ip_address"] == ip for i in self.device.interfaces.values())

    def _show_version(self):
        d = self.device
        print("Huawei Versatile Routing Platform Software")
        print(d.version)
        print("Copyright (C) 2000-2014 HUAWEI TECH CO., LTD")
        print("%s uptime is 0 week, 0 day, 0 hour, 0 minute" % d.model)
        print("")
        print("%s (VRP %s) with %d interfaces" % (
            d.model, d.version.split("Version")[1].strip() if "Version" in d.version else "",
            len(d.interfaces)))

    def _render_config_lines(self, dev):
        lines = ["!"]
        lines.append("sysname %s" % dev.hostname)
        if dev.telnet_server:
            lines.append("telnet server enable")
        if dev.stelnet_server:
            lines.append("stelnet server enable")
        extra_vlans = [v for v in dev.vlans if v != "1"]
        if extra_vlans:
            lines.append("vlan batch %s" % " ".join(sorted(extra_vlans, key=lambda x: int(x))))
        for vid, v in dev.vlans.items():
            if vid == "1":
                continue
            lines.append("vlan %s" % vid)
            if v.get("description"):
                lines.append(" description %s" % v["description"])
        for name, iface in dev.interfaces.items():
            if self._if_has_config(iface):
                lines.append("interface %s" % name)
                if iface["description"]:
                    lines.append(" description %s" % iface["description"])
                if iface["shutdown"]:
                    lines.append(" shutdown")
                if iface["link_type"] == "access":
                    lines.append(" port link-type access")
                    if iface["pvid"] != 1:
                        lines.append(" port default vlan %d" % iface["pvid"])
                elif iface["link_type"] == "trunk":
                    lines.append(" port link-type trunk")
                    lines.append(" port trunk allow-pass vlan %s" % " ".join(
                        str(x) for x in iface["trunk_vlans"]))
                if iface["ip_address"]:
                    lines.append(" ip address %s %s" % (iface["ip_address"], iface["mask"]))
        if dev.users:
            lines.append("aaa")
            for u in dev.users:
                lines.append(" local-user %s password cipher %s" % (u["name"], u["password"] or "******"))
                if u["service_type"]:
                    lines.append(" local-user %s service-type %s" % (u["name"], " ".join(u["service_type"])))
                lines.append(" local-user %s privilege level %d" % (u["name"], u["level"]))
        if dev.ui_auth_mode != "none":
            lines.append("user-interface console 0")
            lines.append(" authentication-mode %s" % dev.ui_auth_mode)
            if dev.ui_auth_mode == "password" and dev.ui_password:
                lines.append(" set authentication password cipher %s" % dev.ui_password)
        for r in dev.static_routes:
            lines.append("ip route-static %s %s %s" % (r["dest"], r["mask"], r["nexthop"]))
        lines.append("return")
        return lines

    @staticmethod
    def _if_has_config(iface):
        return bool(
            iface["description"] or iface["shutdown"] or iface["ip_address"]
            or (iface["link_type"] and iface["link_type"] != "access")
            or (iface["link_type"] == "access" and iface["pvid"] != 1)
        )

    def _show_config(self, dev):
        for ln in self._render_config_lines(dev):
            print(ln)

    def _show_ip_int_brief(self):
        print("%-32s %-20s %-10s %s" % ("Interface", "IP Address/Mask", "Physical", "Protocol"))
        for name, iface in self.device.interfaces.items():
            ip = "%s/%s" % (iface["ip_address"], iface["prefix"]) if iface["ip_address"] else "unassigned"
            phys = "down" if iface["shutdown"] else "up"
            proto = "down" if (iface["shutdown"] or not iface["ip_address"]) else "up"
            print("%-32s %-20s %-10s %s" % (name, ip, phys, proto))

    def _show_ip_interface(self, name):
        targets = [name] if name else list(self.device.interfaces.keys())
        if name and name not in self.device.interfaces:
            # try expand
            exp = self._expand_interface(name)
            if exp in self.device.interfaces:
                targets = [exp]
            else:
                print("Error: The interface %s does not exist." % name)
                return
        for t in targets:
            iface = self.device.interfaces[t]
            print("%s current state : %s" % (t, "DOWN" if iface["shutdown"] else "UP"))
            print("Line protocol current state : %s" % ("DOWN" if iface["shutdown"] else "UP"))
            if iface["ip_address"]:
                print("Internet Address is %s/%s" % (iface["ip_address"], iface["prefix"]))
            else:
                print("Internet protocol processing : disabled")
            if iface["description"]:
                print("Description: %s" % iface["description"])
            print("")

    def _show_vlan(self):
        vids = sorted(self.device.vlans, key=lambda x: int(x))
        print("The total number of VLANs is : %d" % len(vids))
        print("  VLAN ID  Type      Status  Port")
        for vid in vids:
            v = self.device.vlans[vid]
            ports = [n for n, i in self.device.interfaces.items()
                     if i.get("link_type") == "access" and str(i.get("pvid")) == vid]
            port_str = " ".join(ports) if ports else "--"
            print("  %-8s %-9s %-7s %s" % (vid, "common", v.get("status", "enable"), port_str))

    def _show_routing(self):
        print("Route Flags: R - relay, D - download to fib")
        print("-" * 78)
        print("Routing Tables: Public")
        rows = []
        for name, iface in self.device.interfaces.items():
            if iface["ip_address"]:
                net = ip_to_int(iface["ip_address"]) & ip_to_int(iface["mask"])
                dest = "%s/%s" % (int_to_ip(net), iface["prefix"])
                rows.append((dest, "Direct", 0, 0, iface["ip_address"], name))
        for r in self.device.static_routes:
            rows.append((r["dest"] + "/" + str(r["prefix"]), "Static", r["pref"], 0, r["nexthop"],
                         self._if_for_nexthop(r["nexthop"])))
        print("Destinations : %d        Routes : %d" % (len(rows), len(rows)))
        print("%-20s %-9s %-4s %-5s %-7s %-16s %s" % (
            "Destination/Mask", "Proto", "Pre", "Cost", "Flags", "NextHop", "Interface"))
        for dest, proto, pre, cost, nh, iface in rows:
            print("%-20s %-9s %-4d %-5d %-7s %-16s %s" % (dest, proto, pre, cost, "RD", nh, iface))

    def _if_for_nexthop(self, nh):
        for name, iface in self.device.interfaces.items():
            if iface["ip_address"] and self._same_subnet(iface, nh):
                return name
        return "Unknown"

    @staticmethod
    def _same_subnet(iface, ip):
        if not iface["ip_address"]:
            return False
        a = ip_to_int(iface["ip_address"]) & ip_to_int(iface["mask"])
        b = ip_to_int(ip) & ip_to_int(iface["mask"])
        return a == b

    def _show_this(self):
        m, c = self.stack[-1]
        if m == "interface":
            iface = self.device.interfaces[c["if"]]
            print("interface %s" % c["if"])
            if iface["description"]:
                print(" description %s" % iface["description"])
            if iface["shutdown"]:
                print(" shutdown")
            if iface["ip_address"]:
                print(" ip address %s %s" % (iface["ip_address"], iface["mask"]))
        elif m == "vlan":
            v = self.device.vlans[c["vid"]]
            print("vlan %s" % c["vid"])
            if v.get("description"):
                print(" description %s" % v["description"])
        elif m == "system":
            print("sysname %s" % self.device.hostname)
        else:
            print("return")

    def _show_local_user(self):
        if not self.device.users:
            print("No local user configured.")
            return
        for u in self.device.users:
            print("  username: %-12s level: %d  service: %s" % (
                u["name"], u["level"], ",".join(u["service_type"]) or "-"))

    # ------------------------- 补全候选 -------------------------
    def _complete(self, line):
        parts = line.split()
        if len(parts) <= 1:
            word = parts[0] if parts else ""
            cands = [k for k, (name, modes, _a) in self._index.items()
                     if self.mode in modes and k.startswith(word)]
            return sorted(set(cands))
        first = parts[0].lower()
        word = parts[-1].lower()
        if first in ("interface", "int"):
            return sorted(n for n in self.device.interfaces if n.lower().startswith(word))
        if first in ("display", "dis"):
            if len(parts) == 2:
                subs = ["version", "current-configuration", "saved-configuration",
                        "ip", "interface", "vlan", "routing-table", "this", "local-user"]
                return [s for s in subs if s.startswith(word)]
            if len(parts) >= 3 and parts[1].lower() == "ip":
                if parts[2].lower().startswith("i"):
                    return ["interface"]
                return []
            return []
        if first == "vlan" and len(parts) == 2:
            return sorted(v for v in self.device.vlans if v.startswith(word))
        if first == "port":
            if len(parts) == 2:
                return [s for s in ("link-type", "default", "trunk") if s.startswith(word)]
            second = parts[1].lower()
            if second.startswith("l") and len(parts) == 3:
                return [s for s in ("access", "trunk", "hybrid") if s.startswith(word)]
            if second.startswith("d") and len(parts) == 3:
                return ["vlan"] if "vlan".startswith(word) else []
            if second.startswith("t") and len(parts) == 3:
                return ["allow-pass"] if "allow-pass".startswith(word) else []
            if second.startswith("t") and len(parts) == 4:
                return ["vlan"] if "vlan".startswith(word) else []
            return []
        if first == "undo":
            subs = ["sysname", "shutdown", "description", "ip", "vlan",
                    "local-user", "port", "stelnet", "telnet", "authentication-mode"]
            return [s for s in subs if s.startswith(word)]
        if first in ("sysname",):
            return []
        return []
