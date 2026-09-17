"""跨平台行读取器，提供 Tab 自动补全、历史记录(上下键)与实时重绘。

- Windows: 使用 msvcrt 逐字符读取控制台输入，并启用 VT 虚拟终端以正确渲染光标移动。
- Unix:   使用 tty/termios 进入 raw 模式逐字符读取。
- 非交互(stdin 被管道): 退化为普通 input()，保证脚本/测试可用。

支持：
  - 左右方向键移动光标，Home/End 跳到行首/行尾
  - 退格(Backspace)删除光标前字符，Delete 删除光标处字符
  - 上下方向键浏览历史
  - Tab 自动补全

不依赖任何第三方库。补全候选由 set_completer() 注入。
"""

import sys


class LineReader:
    def __init__(self):
        self.history = []
        self._completer = None

    def set_completer(self, func):
        """func(line:str) -> list[str]  返回当前光标前的补全候选。"""
        self._completer = func

    def add_history(self, line):
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)

    # ----------------------------- 公共入口 -----------------------------
    def read_line(self, prompt):
        if not self._is_interactive():
            try:
                return input(prompt + " ")
            except EOFError:
                return None
        if sys.platform == "win32":
            self._enable_vt_windows()
            return self._read_win(prompt)
        return self._read_unix(prompt)

    def _is_interactive(self):
        try:
            return sys.stdin.isatty()
        except Exception:
            return False

    # ------------------------ Windows VT 开启 ------------------------
    @staticmethod
    def _enable_vt_windows():
        # 让 Windows 控制台支持 ANSI 转义（光标移动/清行），保证左右键重绘正常
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            STD_OUTPUT_HANDLE = -11
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            h = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            mode = ctypes.c_uint32()
            kernel32.GetConsoleMode(h, ctypes.byref(mode))
            kernel32.SetConsoleMode(h, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        except Exception:
            pass

    # --------------------------- 补全辅助 ---------------------------
    def _complete(self, line):
        if self._completer is None:
            return []
        return self._completer(line)

    @staticmethod
    def _apply_completion(line, comp):
        if line == "" or line.endswith(" "):
            return line + comp
        parts = line.split()
        parts[-1] = comp
        return " ".join(parts)

    # ----------------------------- 重绘 -----------------------------
    def _redraw(self, prompt, buf, pos):
        line = "".join(buf)
        # 回到行首并清除整行，再输出 提示符+内容，最后把光标移回 pos 处
        sys.stdout.write("\r\033[K" + prompt + line)
        back = len(line) - pos
        if back > 0:
            sys.stdout.write("\033[%dD" % back)
        sys.stdout.flush()

    def _history_up(self, idx):
        if not self.history:
            return idx, None
        if idx > 0:
            idx -= 1
        return idx, self.history[idx]

    def _history_down(self, idx):
        if not self.history:
            return idx, None
        if idx < len(self.history) - 1:
            idx += 1
            return idx, self.history[idx]
        return len(self.history), ""

    # --------------------------- Windows ----------------------------
    def _read_win(self, prompt):
        import msvcrt
        buf = []
        pos = 0
        hist_idx = len(self.history)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            ch = msvcrt.getwch()
            if ch == "\r" or ch == "\n":
                sys.stdout.write("\n")
                line = "".join(buf)
                self.add_history(line)
                return line
            elif ch == "\t":
                self._do_complete(prompt, buf, pos)
                pos = len(buf)
            elif ch == "\x08":  # Backspace —— 删除光标前一个字符
                if pos > 0:
                    del buf[pos - 1]
                    pos -= 1
                    self._redraw(prompt, buf, pos)
            elif ch == "\x03":  # Ctrl+C
                sys.stdout.write("^C\n")
                raise KeyboardInterrupt
            elif ch in ("\x00", "\xe0"):  # 特殊键前缀，再读一个字符
                nxt = msvcrt.getwch()
                if nxt == "H":  # 上
                    hist_idx, new = self._history_up(hist_idx)
                    if new is not None:
                        buf[:] = list(new)
                        pos = len(buf)
                        self._redraw(prompt, buf, pos)
                elif nxt == "P":  # 下
                    hist_idx, new = self._history_down(hist_idx)
                    if new is not None:
                        buf[:] = list(new)
                        pos = len(buf)
                        self._redraw(prompt, buf, pos)
                elif nxt == "K":  # 左
                    if pos > 0:
                        pos -= 1
                        self._redraw(prompt, buf, pos)
                elif nxt == "M":  # 右
                    if pos < len(buf):
                        pos += 1
                        self._redraw(prompt, buf, pos)
                elif nxt == "G":  # Home
                    pos = 0
                    self._redraw(prompt, buf, pos)
                elif nxt == "O":  # End
                    pos = len(buf)
                    self._redraw(prompt, buf, pos)
                elif nxt == "S":  # Delete —— 删除光标处字符
                    if pos < len(buf):
                        del buf[pos]
                        self._redraw(prompt, buf, pos)
            elif ch >= " ":
                buf.insert(pos, ch)
                pos += 1
                self._redraw(prompt, buf, pos)

    # ---------------------------- Unix ------------------------------
    def _read_unix(self, prompt):
        import tty
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        buf = []
        pos = 0
        hist_idx = len(self.history)
        try:
            tty.setraw(fd)
            sys.stdout.write(prompt)
            sys.stdout.flush()
            while True:
                ch = sys.stdin.read(1)
                if ch == "\r" or ch == "\n":
                    sys.stdout.write("\n")
                    line = "".join(buf)
                    self.add_history(line)
                    return line
                elif ch == "\t":
                    self._do_complete(prompt, buf, pos)
                    pos = len(buf)
                elif ch in ("\x7f", "\x08"):  # Backspace
                    if pos > 0:
                        del buf[pos - 1]
                        pos -= 1
                        self._redraw(prompt, buf, pos)
                elif ch == "\x03":  # Ctrl+C
                    sys.stdout.write("^C\n")
                    raise KeyboardInterrupt
                elif ch == "\x04":  # Ctrl+D
                    if not buf:
                        sys.stdout.write("\n")
                        return None
                elif ch == "\x1b":  # ESC 序列(方向键/Home/End/Delete)
                    n1 = sys.stdin.read(1)
                    if n1 == "[":
                        n2 = sys.stdin.read(1)
                        if n2 == "A":  # 上
                            hist_idx, new = self._history_up(hist_idx)
                            if new is not None:
                                buf[:] = list(new)
                                pos = len(buf)
                                self._redraw(prompt, buf, pos)
                        elif n2 == "B":  # 下
                            hist_idx, new = self._history_down(hist_idx)
                            if new is not None:
                                buf[:] = list(new)
                                pos = len(buf)
                                self._redraw(prompt, buf, pos)
                        elif n2 == "C":  # 右
                            if pos < len(buf):
                                pos += 1
                                self._redraw(prompt, buf, pos)
                        elif n2 == "D":  # 左
                            if pos > 0:
                                pos -= 1
                                self._redraw(prompt, buf, pos)
                        elif n2 == "H":  # Home
                            pos = 0
                            self._redraw(prompt, buf, pos)
                        elif n2 == "F":  # End
                            pos = len(buf)
                            self._redraw(prompt, buf, pos)
                        elif n2 == "3":  # Delete (~)
                            if sys.stdin.read(1) == "~" and pos < len(buf):
                                del buf[pos]
                                self._redraw(prompt, buf, pos)
                        elif n2 in ("1", "7"):  # Home 变体
                            n3 = sys.stdin.read(1)
                            if n3 == "~":
                                pos = 0
                                self._redraw(prompt, buf, pos)
                        elif n2 in ("4", "8"):  # End 变体
                            n3 = sys.stdin.read(1)
                            if n3 == "~":
                                pos = len(buf)
                                self._redraw(prompt, buf, pos)
                    elif n1 == "O":
                        n2 = sys.stdin.read(1)
                        if n2 == "H":
                            pos = 0
                            self._redraw(prompt, buf, pos)
                        elif n2 == "F":
                            pos = len(buf)
                            self._redraw(prompt, buf, pos)
                elif ch >= " ":
                    buf.insert(pos, ch)
                    pos += 1
                    self._redraw(prompt, buf, pos)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    # ------------------------ 补全处理 -----------------------------
    def _do_complete(self, prompt, buf, pos):
        # 基于光标前文本做补全；补全结果替换光标前最后一个词
        prefix = "".join(buf[:pos])
        comps = self._complete(prefix)
        if not comps:
            return
        if len(comps) == 1:
            new = self._apply_completion(prefix, comps[0])
            buf[:] = list(new) + buf[pos:]
        else:
            sys.stdout.write("\n" + "  ".join(sorted(comps)[:30]) + "\n")
            self._redraw(prompt, buf, pos)
