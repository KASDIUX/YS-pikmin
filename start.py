"""Run with Python 3.12; preview mode uses the standard library only."""
import argparse
from pathlib import Path
import threading
import webbrowser
from urllib.request import ProxyHandler, build_opener

from leafroute.server import App
from leafroute import __version__


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='YS-pikmin local GPS route workbench')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=root/'data')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        app = App(root, args.data_dir, args.port)
    except OSError:
        origin = f'http://127.0.0.1:{args.port}'
        try:
            with build_opener(ProxyHandler({})).open(origin+'/api/state', timeout=2) as response:
                running_version = response.headers.get('X-LeafRoute-Version')
                if running_version:
                    if running_version != __version__:
                        print(f'目前執行的是 v{running_version}；請先關閉舊版，再啟動 v{__version__}。')
                        return 1
                    print(f'YS-pikmin 已在執行：{origin}')
                    if not args.no_browser:
                        webbrowser.open(origin)
                    return 0
        except OSError:
            pass
        print('無法啟動：連接埠可能已被使用。請關閉先前的 YS-pikmin，或用 --port 8766。')
        return 1
    print(f'YS-pikmin {__version__} | {app.origin}', flush=True)
    print('啟動於預覽模式。保持此視窗開啟；結束請按 Ctrl+C。', flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(app.origin)).start()
    try:
        app.http.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
