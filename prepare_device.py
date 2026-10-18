"""User-invoked preparation; explicitly select the device before mounting a DDI."""
import asyncio
import subprocess
import sys
from leafroute.device import discover


def main():
    result = asyncio.run(discover())
    if result['warning']:
        print(result['warning'])
    devices = result['devices']
    if not devices:
        print('未找到裝置，請先用 Apple Devices 配對並解鎖手機。')
        return 1
    for number, device in enumerate(devices, 1):
        print(f"{number}. {device['name']} | iOS {device['ios'] or '?'} | {device['id'][-6:]}")
    print('這會下載／掛載 Apple 開發者映像；第一次執行可能需要數分鐘。')
    try:
        choice = int(input('輸入要準備的裝置編號，或直接按 Enter 取消：'))
        if not 1 <= choice <= len(devices):
            return 1
    except ValueError:
        return 0
    device = devices[choice-1]
    modern = not device['ios'] or int(device['ios'].split('.')[0]) >= 17
    prefix = [sys.executable, '--device-cli'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'pymobiledevice3']
    args = prefix + ['mounter', 'auto-mount',
            '--tunnel' if modern else '--udid', device['id']]
    return subprocess.run(args, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
