"""托盘「设置」窗口。

布局：
  1. 邮箱账号列表（默认只显示列表 + 三个按钮）
  2. 点「新增账号」/「编辑所选」（或双击某行）才展开账号表单；
     「验证并保存」就在授权码输入框下面
  3. 程序选项：运行模式、自动/手动间隔、监听时长、开机自启、隐藏托盘、打开日志
授权码输入不回显，保存前真实登录验证，DPAPI 加密落盘。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import conf, dpi
from .imap_scan import test_login

PROVIDERS = [
    ("qq", "QQ 邮箱"),
    ("163", "网易 163"),
    ("126", "网易 126"),
    ("yeah", "网易 yeah.net"),
]
PROVIDER_BY_LABEL = {label: key for key, label in PROVIDERS}
LABEL_BY_PROVIDER = {key: label for key, label in PROVIDERS}

BASE_WIDTH = 640


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.scale = dpi.scale_for(self.root)
        self.win: tk.Toplevel | None = None
        self.accounts: list[dict] = []
        self.editing: dict | None = None  # 表单里正在编辑的账号；None = 新增
        self._form_open = False
        self._q: queue.Queue | None = None
        self._busy = False
        self._suppress_select = False
        self._pos = (0, 0)

    # ---------------------------------------------------------------- 生命周期

    def open(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            return
        self._build()
        self.reload()

    def reload(self) -> None:
        """重新读 config（外部改过也能同步）。"""
        self.app.cfg = conf.load()
        self.accounts = list(self.app.cfg.get("accounts", []))
        self._fill_tree()
        if self.win is not None and hasattr(self, "v_mode"):
            self.v_mode.set(self.app.mode)
            self.v_auto_iv.set(str(self.app.auto_poll_seconds))
            self.v_man_iv.set(str(self.app.manual_poll_seconds))
            self.v_win.set(str(int(self.app.cfg.get("manual_window_seconds", 300))))
            self._sync_mode_dependent()

    # ---------------------------------------------------------------- 建界面

    def _px(self, v: int) -> int:
        return int(v * self.scale)

    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("邮箱验证码助手 · 设置")
        win.configure(bg="#f3f4f7")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self.close)

        pad = self._px(12)
        outer = ttk.Frame(win, padding=pad)
        outer.pack(fill="both", expand=True)
        self.outer = outer

        # ---------- 账号列表 ----------
        top_row = ttk.Frame(outer)
        top_row.pack(fill="x")
        ttk.Label(top_row, text="邮箱账号", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Label(
            top_row,
            text="（双击一行可改授权码）",
            foreground="#7a8494",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=(6, 0))

        tree_wrap = ttk.Frame(outer)
        tree_wrap.pack(fill="both", expand=True, pady=(self._px(6), self._px(6)))
        style = ttk.Style(win)
        style.configure("Settings.Treeview", rowheight=self._px(24))
        self.tree = ttk.Treeview(
            tree_wrap,
            columns=("name", "user", "state"),
            show="headings",
            height=5,
            style="Settings.Treeview",
        )
        for col, title, width in (("name", "名称", 150), ("user", "邮箱", 240), ("state", "授权码", 90)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=self._px(width), anchor="w")
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Double-1>", lambda e: self._on_double_click())

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="新增账号", command=self._new_account).pack(side="left")
        ttk.Button(btn_row, text="编辑所选", command=self._edit_selected).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="删除所选", command=self._delete_selected).pack(side="left", padx=(8, 0))

        # ---------- 账号表单（默认隐藏） ----------
        self.form = ttk.LabelFrame(outer, text="账号信息", padding=self._px(10))
        self.v_provider = tk.StringVar(value=LABEL_BY_PROVIDER["qq"])
        self.v_user = tk.StringVar()
        self.v_label = tk.StringVar()
        self.v_code = tk.StringVar()
        self.v_show = tk.BooleanVar(value=False)

        form = self.form
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="类型").grid(row=0, column=0, sticky="w", pady=2)
        self.cb_provider = ttk.Combobox(
            form, textvariable=self.v_provider, values=[label for _, label in PROVIDERS], state="readonly"
        )
        self.cb_provider.grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Label(form, text="邮箱地址").grid(row=1, column=0, sticky="w", pady=2)
        self.e_user = ttk.Entry(form, textvariable=self.v_user)
        self.e_user.grid(row=1, column=1, sticky="ew", pady=2)

        ttk.Label(form, text="显示名").grid(row=2, column=0, sticky="w", pady=2)
        self.e_label = ttk.Entry(form, textvariable=self.v_label)
        self.e_label.grid(row=2, column=1, sticky="ew", pady=2)

        ttk.Label(form, text="授权码").grid(row=3, column=0, sticky="w", pady=2)
        code_row = ttk.Frame(form)
        code_row.grid(row=3, column=1, sticky="ew", pady=2)
        code_row.columnconfigure(0, weight=1)
        self.e_code = ttk.Entry(code_row, textvariable=self.v_code, show="*")
        self.e_code.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(code_row, text="显示", variable=self.v_show, command=self._toggle_show).grid(
            row=0, column=1, padx=(6, 0)
        )

        # 「验证并保存」就在授权码下面
        form_actions = ttk.Frame(form)
        form_actions.grid(row=4, column=0, columnspan=2, sticky="w", pady=(self._px(8), 0))
        self.btn_save = ttk.Button(form_actions, text="验证并保存", command=lambda: self._save(verify=True))
        self.btn_save.pack(side="left")
        self.btn_offline = ttk.Button(form_actions, text="离线直接保存", command=lambda: self._save(verify=False))
        self.btn_offline.pack(side="left", padx=(8, 0))
        ttk.Button(form_actions, text="取消", command=self._cancel_form).pack(side="left", padx=(8, 0))

        ttk.Label(
            form,
            text="授权码不是邮箱密码：QQ/163 网页版设置里开启 IMAP 后生成。保存前会真实登录验证一次，"
            "加密用 Windows DPAPI，只有当前用户能解。",
            foreground="#5b6472",
            wraplength=self._px(520),
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(self._px(6), 0))

        # ---------- 程序选项 ----------
        opts = ttk.LabelFrame(outer, text="程序选项", padding=self._px(8))
        opts.pack(fill="x", pady=(self._px(10), 0))
        self.opts = opts

        mode_row = ttk.Frame(opts)
        mode_row.pack(anchor="w", fill="x")
        ttk.Label(mode_row, text="运行模式：").pack(side="left")
        self.v_mode = tk.StringVar(value=self.app.mode)
        ttk.Radiobutton(mode_row, text="自动检测", value="auto", variable=self.v_mode, command=self._toggle_mode).pack(
            side="left", padx=(2, 10)
        )
        ttk.Radiobutton(mode_row, text="手动监听", value="manual", variable=self.v_mode, command=self._toggle_mode).pack(
            side="left"
        )

        iv_row = ttk.Frame(opts)
        iv_row.pack(anchor="w", fill="x", pady=(self._px(2), 0))
        ttk.Label(iv_row, text="自动间隔").pack(side="left")
        self.v_auto_iv = tk.StringVar(value=str(self.app.auto_poll_seconds))
        self.sp_auto_iv = ttk.Spinbox(
            iv_row, from_=15, to=3600, width=6, textvariable=self.v_auto_iv, command=self._apply_intervals
        )
        self.sp_auto_iv.pack(side="left", padx=(4, 2))
        ttk.Label(iv_row, text="秒").pack(side="left", padx=(0, 12))

        ttk.Label(iv_row, text="手动间隔").pack(side="left")
        self.v_man_iv = tk.StringVar(value=str(self.app.manual_poll_seconds))
        self.sp_man_iv = ttk.Spinbox(
            iv_row, from_=5, to=600, width=6, textvariable=self.v_man_iv, command=self._apply_intervals
        )
        self.sp_man_iv.pack(side="left", padx=(4, 2))
        ttk.Label(iv_row, text="秒").pack(side="left", padx=(0, 12))

        ttk.Label(iv_row, text="监听时长").pack(side="left")
        self.v_win = tk.StringVar(value=str(int(self.app.cfg.get("manual_window_seconds", 300))))
        self.sp_win = ttk.Spinbox(
            iv_row, from_=30, to=3600, width=7, textvariable=self.v_win, command=self._apply_intervals
        )
        self.sp_win.pack(side="left", padx=(4, 2))
        ttk.Label(iv_row, text="秒").pack(side="left")
        for spin in (self.sp_auto_iv, self.sp_man_iv, self.sp_win):
            spin.bind("<FocusOut>", self._apply_intervals)
            spin.bind("<Return>", self._apply_intervals)

        ttk.Label(
            opts,
            text="自动：后台按「自动间隔」自己扫。手动：不自动扫；双击托盘图标后，按「手动间隔」扫「监听时长」，"
            "收到验证码立刻停止。手动模式下必须显示托盘图标。",
            foreground="#5b6472",
            wraplength=self._px(560),
            justify="left",
        ).pack(anchor="w", pady=(self._px(3), self._px(6)))

        self.v_autostart = tk.BooleanVar(value=bool(self.app.autostart_enabled()))
        ttk.Checkbutton(
            opts,
            text="开机自启（登录 Windows 后自动在后台运行）",
            variable=self.v_autostart,
            command=self._toggle_autostart,
        ).pack(anchor="w")

        self.v_hide_tray = tk.BooleanVar(value=bool(self.app.cfg.get("hide_tray", False)))
        self.cb_hide_tray = ttk.Checkbutton(
            opts,
            text="隐藏系统托盘图标（与手动监听冲突；隐藏后再运行一次 main.py 可打开本设置）",
            variable=self.v_hide_tray,
            command=self._toggle_tray,
        )
        self.cb_hide_tray.pack(anchor="w", pady=(self._px(4), 0))

        ttk.Button(opts, text="打开日志", command=self.app.open_log).pack(anchor="w", pady=(self._px(8), 0))

        # ---------- 状态 + 关闭 ----------
        self.v_status = tk.StringVar(value="")
        self.lbl_status = ttk.Label(
            outer,
            textvariable=self.v_status,
            foreground="#1a7f37",
            wraplength=self._px(580),
            justify="left",
        )
        self.lbl_status.pack(anchor="w", pady=(self._px(8), self._px(4)))

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="关闭", command=self.close).pack(side="right")

        # 初始尺寸：按内容，窗口居中；之后展开/收起表单会跟着长高/变矮
        win.update_idletasks()
        width = self._px(BASE_WIDTH)
        height = min(win.winfo_reqheight(), int(win.winfo_screenheight() * 0.9))
        x = (win.winfo_screenwidth() - width) // 2
        y = max(self._px(30), (win.winfo_screenheight() - height) // 2)
        self._pos = (x, y)
        win.geometry(f"{width}x{height}+{x}+{y}")

        self._sync_mode_dependent()

    # ---------------------------------------------------------------- 列表

    def _fill_tree(self) -> None:
        self._suppress_select = True
        self.tree.delete(*self.tree.get_children())
        for acct in self.accounts:
            state = "已设置" if conf.has_secret(acct) else "未设置"
            if not acct.get("enabled", True):
                state += "（停用）"
            self.tree.insert(
                "", "end", iid=str(acct.get("id")), values=(conf.account_label(acct), acct.get("user"), state)
            )
        if self.accounts:
            first = str(self.accounts[0].get("id"))
            self.tree.selection_set(first)
            self.tree.focus(first)
        self.tree.configure(height=max(2, min(5, len(self.accounts))))
        if self.win is not None:
            self.win.after_idle(self._clear_suppress)
            self._fit()

    def _selected_account(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        for acct in self.accounts:
            if str(acct.get("id")) == sel[0]:
                return acct
        return None

    def _clear_suppress(self) -> None:
        self._suppress_select = False

    def _on_select(self) -> None:
        if self._suppress_select or self._form_open:
            return

    def _on_double_click(self) -> None:
        acct = self._selected_account()
        if acct is not None:
            self._show_form(acct)

    # ---------------------------------------------------------------- 表单显隐

    def _show_form(self, acct: dict | None) -> None:
        if self._busy:
            return
        self.editing = acct
        self._form_open = True
        self._fill_form(acct)
        self.form.pack(fill="x", pady=(self._px(10), 0), before=self.opts)
        self._fit()
        self.v_code.set("")
        self.e_code.focus_set()
        self._set_status("填入信息后点「验证并保存」。" if acct is None else "只改授权码就行，邮箱/类型保持不变。", ok=None)

    def _hide_form(self) -> None:
        self._form_open = False
        self.form.pack_forget()
        self._fit()

    def _cancel_form(self) -> None:
        self._busy = False
        self._hide_form()
        self.editing = None
        self._set_status("已取消。", ok=None)

    def _fill_form(self, acct: dict | None) -> None:
        if acct is None:
            self.v_provider.set(LABEL_BY_PROVIDER["qq"])
            self.cb_provider.configure(state="readonly")
            self.v_user.set("")
            self.e_user.configure(state="normal")
            self.v_label.set("")
            return
        self.v_provider.set(LABEL_BY_PROVIDER.get(acct.get("provider", "qq"), "QQ 邮箱"))
        self.cb_provider.configure(state="disabled")
        self.v_user.set(acct.get("user", ""))
        self.e_user.configure(state="disabled")
        self.v_label.set(acct.get("label", ""))

    def _new_account(self) -> None:
        self._show_form(None)

    def _edit_selected(self) -> None:
        acct = self._selected_account()
        if acct is None:
            self._set_status("先在列表里选一个账号。", ok=False)
            return
        self._show_form(acct)

    def _fit(self) -> None:
        """表单展开/收起后按内容重算高度，左上角不跳。"""
        if self.win is None or not self.win.winfo_exists():
            return
        self.win.update_idletasks()
        width = self._px(BASE_WIDTH)
        height = min(self.win.winfo_reqheight(), int(self.win.winfo_screenheight() * 0.9))
        x, y = self._pos
        self.win.geometry(f"{width}x{height}+{x}+{y}")

    # ---------------------------------------------------------------- 保存

    def _toggle_show(self) -> None:
        self.e_code.configure(show="" if self.v_show.get() else "*")

    def _set_status(self, text: str, ok: bool | None = None) -> None:
        self.v_status.set(text)
        color = "#1a7f37" if ok else ("#c2410c" if ok is False else "#424b57")
        self.lbl_status.configure(foreground=color)

    def _build_account(self) -> dict | None:
        provider = PROVIDER_BY_LABEL.get(self.v_provider.get(), "qq")
        user = self.v_user.get().strip()
        label = self.v_label.get().strip()
        if "@" not in user:
            self._set_status("邮箱地址不完整。", ok=False)
            return None
        if self.editing is None:
            acct = conf.new_account(provider, user, label)
            acct["id"] = conf.unique_account_id(self.app.cfg, acct["id"])
            return acct
        acct = dict(self.editing)
        acct["label"] = label or conf.account_label(self.editing)
        return acct

    def _save(self, verify: bool) -> None:
        if not self._form_open or self._busy:
            return
        code = self.v_code.get().strip().replace(" ", "")
        if not code:
            self._set_status("请先填授权码。", ok=False)
            self.e_code.focus_set()
            return
        acct = self._build_account()
        if acct is None:
            return
        if not verify:
            self._commit(acct, code)
            return
        self._busy = True
        self.btn_save.configure(state="disabled")
        self.btn_offline.configure(state="disabled")
        self._set_status("正在登录验证…", ok=None)
        self._q = queue.Queue()
        threading.Thread(target=self._worker, args=(acct, code), name="settings-verify", daemon=True).start()
        self.win.after(120, self._poll)

    def _worker(self, acct: dict, code: str) -> None:
        try:
            ok, msg = test_login(acct, code)
        except Exception as exc:
            ok, msg = False, f"验证出错：{exc}"
        self._q.put((ok, msg, acct, code))

    def _poll(self) -> None:
        if self._q is None or self.win is None or not self.win.winfo_exists():
            return
        try:
            ok, msg, acct, code = self._q.get_nowait()
        except queue.Empty:
            self.win.after(150, self._poll)
            return
        self._busy = False
        self.btn_save.configure(state="normal")
        self.btn_offline.configure(state="normal")
        if ok:
            self._commit(acct, code)
        else:
            self._set_status(f"验证失败：{msg}（可换授权码重试，或用「离线直接保存」）", ok=False)

    def _commit(self, acct: dict, code: str) -> None:
        cfg = conf.load()
        existing = None
        for item in cfg.get("accounts", []):
            if item.get("id") == acct.get("id"):
                existing = item
                break
        if existing is None and self.editing is not None:
            for item in cfg.get("accounts", []):
                if item.get("user") == acct.get("user"):
                    existing = item
                    break
        if existing is None:
            target = acct
            cfg.setdefault("accounts", []).append(target)
        else:
            target = existing
            target["label"] = acct.get("label") or target.get("label")
        conf.set_secret(target, code)
        conf.save(cfg)
        self.v_code.set("")
        self.app.cfg = cfg
        self.app.on_settings_saved()
        self.editing = None
        self._hide_form()
        self.reload()
        self._set_status(f"已保存「{conf.account_label(target)}」，加密后明文已丢弃，马上开始扫描。", ok=True)

    def _delete_selected(self) -> None:
        if self._busy:
            return
        acct = self._selected_account()
        if acct is None:
            self._set_status("先在列表里选一个账号。", ok=False)
            return
        if not messagebox.askyesno(
            "删除账号", f"确定删除「{conf.account_label(acct)}」？\n（只删本地配置，不动邮箱）", parent=self.win
        ):
            return
        target_id = acct.get("id")
        cfg = conf.load()
        cfg["accounts"] = [a for a in cfg["accounts"] if a.get("id") != target_id]
        conf.save(cfg)
        self.editing = None
        self._hide_form()
        self.app.on_settings_saved()
        self.reload()
        self._set_status(f"已删除「{conf.account_label(acct)}」。", ok=True)

    # ---------------------------------------------------------------- 选项

    def _toggle_mode(self) -> None:
        mode = self.v_mode.get()
        if not self.app.set_mode(mode):
            self._set_status("切换运行模式失败，详见日志。", ok=False)
            return
        self._sync_mode_dependent()
        if mode == "manual":
            if getattr(self.app, "tray_hidden", False):
                self._set_status("已切换到手动模式，但托盘图标当前是隐藏的 —— 请先取消下面的「隐藏系统托盘图标」。", ok=False)
            elif getattr(self.app, "tray", None) is None:
                self._set_status("已切换到手动模式（当前是无托盘调试启动，只能用菜单命令触发）。", ok=False)
            else:
                self._set_status("已切换到手动模式：双击托盘图标开始监听。", ok=True)
        else:
            self._set_status("已切换到自动模式：后台自动检查。", ok=True)

    def _apply_intervals(self, event=None) -> None:
        def clamp(raw: str, default: int, lo: int, hi: int) -> int:
            try:
                value = int(float(str(raw).strip()))
            except (TypeError, ValueError):
                value = default
            return max(lo, min(hi, value))

        auto_v = clamp(self.v_auto_iv.get(), self.app.auto_poll_seconds, 15, 3600)
        man_v = clamp(self.v_man_iv.get(), self.app.manual_poll_seconds, 5, 600)
        win_v = clamp(self.v_win.get(), int(self.app.cfg.get("manual_window_seconds", 300)), 30, 3600)
        self.v_auto_iv.set(str(auto_v))
        self.v_man_iv.set(str(man_v))
        self.v_win.set(str(win_v))
        if self.app.set_intervals(auto_v, man_v, win_v):
            self._set_status(f"已保存：自动 {auto_v} 秒 / 手动 {man_v} 秒 / 监听 {win_v} 秒。", ok=True)

    def _sync_mode_dependent(self) -> None:
        manual = bool(getattr(self.app, "manual_mode", False))
        if getattr(self, "cb_hide_tray", None) is not None:
            self.cb_hide_tray.configure(state="disabled" if manual else "normal")
        if manual:
            self.v_hide_tray.set(False)

    def _toggle_autostart(self) -> None:
        want = bool(self.v_autostart.get())
        if self.app.set_autostart(want):
            self._set_status("开机自启已开启（下次登录 Windows 自动运行）。" if want else "开机自启已关闭。", ok=True)
        else:
            self.v_autostart.set(not want)
            self._set_status("开机自启设置失败，详见日志。", ok=False)

    def _toggle_tray(self) -> None:
        want = bool(self.v_hide_tray.get())
        if want and getattr(self.app, "manual_mode", False):
            self.v_hide_tray.set(False)
            self._set_status("手动监听需要托盘图标（双击它才开始监听），不能隐藏；想隐藏请先切回自动检测。", ok=False)
            return
        if self.app.set_tray_hidden(want):
            if want:
                self._set_status("托盘图标已隐藏；以后想改设置，再运行一次 main.py 即可。", ok=True)
            else:
                self._set_status("托盘图标已显示。", ok=True)
        else:
            self.v_hide_tray.set(not want)
            self._set_status("托盘设置失败，详见日志。", ok=False)

    # ---------------------------------------------------------------- 收尾

    def close(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.destroy()
        self.win = None
        self._form_open = False
        self._busy = False
        self._q = None
