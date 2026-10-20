"""Standalone local Windows helper, independent from the GPS workbench server."""
import argparse
import asyncio
import importlib.metadata
import json
from pathlib import Path
import queue
import threading

from leafroute.developer_mode import DeveloperModeBackend, friendly_error, status_text

VERSION = '1.0.0'


class HelperWindow:
    def __init__(self, root, backend=None):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.backend = root, backend or DeveloperModeBackend()
        self.events = queue.Queue()
        self.phones, self.busy, self.selected = {}, False, None
        root.title('YS-pikmin · 開發者模式工具')
        root.geometry('800x700')
        root.minsize(760, 660)
        style = ttk.Style(root)
        style.configure('Treeview', rowheight=30)
        style.configure('TButton', padding=(10, 7))
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='iPhone 開發者模式工具', font=('Microsoft JhengHei', 17, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='接上支援資料傳輸的 USB 線、解鎖手機，並先在 Apple Devices 完成「信任這部電腦」。',
                  wraplength=700, justify='left').pack(anchor='w', pady=(10, 12))
        self.tree = ttk.Treeview(frame, columns=('name', 'ios', 'state', 'tail'), show='headings', height=5, selectmode='browse')
        for column, title, width in [('name', '已信任的 USB 手機', 225), ('ios', 'iOS', 80),
                                      ('state', '開發者模式', 220), ('tail', '識別碼末六碼', 115)]:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=65, stretch=True)
        self.tree.pack(fill='x')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=12)
        self.scan_button = ttk.Button(buttons, text='重新掃描 USB', command=self.scan)
        self.scan_button.pack(side='left')
        self.reveal_button = ttk.Button(buttons, text='顯示開發者模式選項', command=self.reveal)
        self.reveal_button.pack(side='left', padx=8)
        self.check_button = ttk.Button(buttons, text='確認已開啟', command=self.check)
        self.check_button.pack(side='left')
        self.status = tk.StringVar(value='準備掃描 USB 手機…')
        ttk.Label(frame, textvariable=self.status, wraplength=700, justify='left',
                  font=('Microsoft JhengHei', 10, 'bold')).pack(anchor='w', pady=(0, 12))
        steps = ttk.LabelFrame(frame, text='在手機上完成設定', padding=14)
        steps.pack(fill='x')
        ttk.Label(steps, text='1. 選擇上方手機，按「顯示開發者模式選項」。\n'
                  '2. 手機「設定 → 隱私權與安全性 → 開發者模式」，手動開啟。\n'
                  '3. 依手機提示重新啟動；解鎖後再確認「開啟」並輸入密碼。\n'
                  '4. USB 接回同一支手機，按「確認已開啟」讀取實際狀態。',
                  wraplength=660, justify='left').pack(anchor='w')
        ttk.Label(frame, text='「選項已顯示」與「模式已開啟」是不同狀態。請在手機閱讀並確認系統提示。\n'
                  '此工具只顯示選項及檢查狀態；不會傳送模擬位置。',
                  wraplength=700, justify='left').pack(anchor='w', pady=12)
        ttk.Label(frame, text=f'YS-pikmin Developer Mode Helper {VERSION}', foreground='#737373').pack(anchor='w')
        root.after(80, self.poll)
        self.scan()

    def controls(self):
        self.scan_button.configure(state='disabled' if self.busy else 'normal')
        phone = self.phones.get(self.selected)
        reveal = bool(phone and phone.supported and phone.enabled is False)
        self.reveal_button.configure(state='normal' if reveal and not self.busy else 'disabled')
        self.check_button.configure(state='normal' if phone and not self.busy else 'disabled')
        self.tree.configure(selectmode='none' if self.busy else 'browse')

    def select(self, _=None):
        if self.busy: return
        selection = self.tree.selection()
        self.selected = selection[0] if selection else None
        self.controls()

    def run(self, operation, callback, message):
        if self.busy: return
        self.busy = True
        self.status.set(message)
        self.controls()
        def worker():
            try: self.events.put((callback, asyncio.run(operation()), None))
            except Exception as error: self.events.put((callback, None, friendly_error(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                callback, result, error = self.events.get_nowait()
                self.busy = False
                if error: self.status.set(error)
                else: callback(result)
                self.controls()
        except queue.Empty: pass
        self.root.after(80, self.poll)

    def scan(self):
        self.run(self.backend.scan, self.scanned, '正在掃描並確認 USB 配對與開發者模式狀態…')

    def scanned(self, result):
        phones, warning = result
        self.tree.delete(*self.tree.get_children())
        self.phones.clear()
        self.selected = None
        for index, phone in enumerate(phones):
            key = str(index)
            self.phones[key] = phone
            self.tree.insert('', 'end', iid=key, values=(phone.name, phone.ios, status_text(phone), phone.identity[-6:]))
        if len(phones) == 1:
            self.tree.selection_set('0')
            self.selected = '0'
        self.status.set(warning or ('請選擇要處理的手機。' if phones else
                        '未找到可確認狀態的 USB 手機。請接線、解鎖，並先完成信任配對後重新掃描。'))

    def update_phone(self, key, phone):
        self.phones[key] = phone
        self.tree.item(key, values=(phone.name, phone.ios, status_text(phone), phone.identity[-6:]))

    def reveal(self):
        key = self.selected
        phone = self.phones.get(key)
        if phone is None: return
        def done(result):
            current, revealed = result
            self.update_phone(key, current)
            if not current.supported: self.status.set('此 iOS 版本不需要開發者模式開關。')
            elif current.enabled: self.status.set('已確認開發者模式開啟，可關閉工具並使用 YS-pikmin。')
            elif revealed: self.status.set('選項已顯示；尚未確認模式開啟。請在手機完成下方步驟，再按「確認已開啟」。')
        self.run(lambda: self.backend.reveal(phone.identity), done, '正在確認所選 USB 手機並顯示設定選項…')

    def check(self):
        key = self.selected
        phone = self.phones.get(key)
        if phone is None: return
        def done(current):
            self.update_phone(key, current)
            if not current.supported: self.status.set('此 iOS 版本不需要開發者模式開關。')
            elif current.enabled: self.status.set('已確認開發者模式開啟，可關閉工具並使用 YS-pikmin。')
            else: self.status.set('尚未開啟。請在手機完成開關、重新啟動及開機後確認，再按此按鈕。')
        self.run(lambda: self.backend.inspect(phone.identity), done, '正在讀取原本選擇手機的實際狀態…')


def main():
    parser = argparse.ArgumentParser(description='YS-pikmin USB Developer Mode helper')
    parser.add_argument('--self-check', action='store_true', help='Check bundled modules without contacting phones')
    parser.add_argument('--check-usb', action='store_true', help='Read trusted USB device status only')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.self_check or args.check_usb:
        result = {'tool_version': VERSION}
        code = 0
        try:
            import tkinter
            import pymobiledevice3.services.amfi
            DeveloperModeBackend()._load()
            result['pymobiledevice3'] = importlib.metadata.version('pymobiledevice3')
            result['tk'] = tkinter.Tcl().eval('info patchlevel')
            if args.check_usb:
                phones, warning = asyncio.run(DeveloperModeBackend().scan())
                result['phones'] = [{'ios': p.ios, 'enabled': p.enabled, 'supported': p.supported} for p in phones]
                result['warning'] = warning
            result['ok'] = True
        except Exception as error:
            result.update(ok=False, error=friendly_error(error))
            code = 1
        if args.report: args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        else: print(json.dumps(result, ensure_ascii=False))
        return code
    import tkinter as tk
    root = tk.Tk()
    HelperWindow(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
