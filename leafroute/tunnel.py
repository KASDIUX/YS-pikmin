"""Start the installed tunnel with Windows UAC; reuse existing services."""
import asyncio
import ctypes
import os
import subprocess
import sys
from .device import read_tunnels, same_id


def launch_admin(arguments):
    if os.name != 'nt':
        raise ValueError('自動啟動通道目前支援 Windows。')
    params = subprocess.list2cmdline(arguments)
    shell = ctypes.windll.shell32.ShellExecuteW
    shell.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                      ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    shell.restype = ctypes.c_ssize_t
    result = shell(None, 'runas', sys.executable, params, os.path.dirname(sys.executable), 0)
    if result <= 32:
        raise ValueError('未能啟動通道，可能已取消管理員確認。請重新按「連接 iPhone」。')


def launch_tunnel():
    prefix = ['--device-cli'] if getattr(sys, 'frozen', False) else ['-m', 'pymobiledevice3']
    launch_admin(prefix + ['remote', 'tunneld', '--host', '127.0.0.1', '--protocol', 'tcp'])


async def ensure_tunnel(target_id, report):
    async def ready():
        try:
            tunnels = await asyncio.to_thread(read_tunnels)
            return any(same_id(key, target_id) and value for key, value in tunnels.items()), True
        except (OSError, ValueError):
            return False, False
    found, server = await ready()
    if found:
        return
    if not server:
        report('正在啟動通道，請允許 Windows 管理員確認視窗…')
        await asyncio.to_thread(launch_tunnel)
    report('等待 iPhone 通道；請保持手機解鎖並開啟開發者模式…')
    for _ in range(45):
        found, _ = await ready()
        if found:
            return
        await asyncio.sleep(1)
    raise ValueError('通道尚未就緒。請確認已允許管理員提示、解鎖手機並開啟開發者模式，再重試。')
