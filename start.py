"""Run with Python 3.12; preview mode uses the standard library only."""
import argparse
import sys
import os
import multiprocessing
from pathlib import Path
import threading
import webbrowser
from urllib.request import ProxyHandler, build_opener

from leafroute.desktop import configure_windowed_streams,run_window,show_message,log_folder
configure_windowed_streams()

from leafroute.server import App
from leafroute import __version__
from leafroute.lifecycle import ConsoleCloseHandler


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='YS-pikmin local GPS route workbench')
    parser.add_argument('--port', type=int, default=8765)
    default_data = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YS-pikmin' / 'data' if getattr(sys, 'frozen', False) else root/'data'
    parser.add_argument('--data-dir', type=Path, default=default_data)
    ui = parser.add_mutually_exclusive_group()
    ui.add_argument('--no-browser','--headless',dest='headless',action='store_true')
    ui.add_argument('--browser',action='store_true',help='Use the external browser instead of a desktop window')
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
                        message=f'目前執行的是 v{running_version}；請先關閉舊版，再啟動 v{__version__}。'
                        print(message,flush=True)
                        if not args.headless:show_message(message,error=True)
                        return 1
                    print(f'YS-pikmin 已在執行：{origin}')
                    if args.browser:
                        webbrowser.open(origin)
                    elif not args.headless:
                        show_message('YS-pikmin 已在執行，請從工作列開啟原本的視窗。')
                    return 0
        except OSError:
            pass
        message='無法啟動：連接埠可能已被使用。請關閉先前的 YS-pikmin，或用 --port 8766。'
        print(message,flush=True)
        if not args.headless:show_message(message,error=True)
        return 1
    print(f'YS-pikmin {__version__} | {app.origin}', flush=True)
    print('啟動於預覽模式。關閉主程式視窗時會清理連線。', flush=True)
    if args.browser:
        threading.Timer(0.5, lambda: webbrowser.open(app.origin)).start()
    try:
        if not args.headless and not args.browser:
            run_window(app)
        else:
            with ConsoleCloseHandler(app.close):
                app.http.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        import traceback
        traceback.print_exc()
        if not args.headless:
            show_message(f'{error}\n\n診斷紀錄：{log_folder()}',error=True)
        return 1
    finally:
        app.close()
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == '--device-cli':
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from pymobiledevice3.__main__ import main as device_main
        device_main()
    elif len(sys.argv) == 3 and sys.argv[1] == '--tunnel-helper':
        from leafroute.lifecycle import run_tunnel_helper
        try:run_tunnel_helper(sys.argv[2])
        except Exception:
            import traceback
            traceback.print_exc()
            raise SystemExit(1)
    elif len(sys.argv) == 3 and sys.argv[1] == '--wifi-helper':
        import asyncio
        from leafroute.wifi import helper
        try:asyncio.run(helper(sys.argv[2]))
        except Exception:
            import traceback
            traceback.print_exc()
            raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == '--prepare-device':
        from prepare_device import main as prepare_main
        raise SystemExit(prepare_main())
    else:
        raise SystemExit(main())
