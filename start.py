"""One local entry point. Starts the service if absent; never calls a model."""
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8771'
EXPECTED_VERSION = '0.2.0'


def health():
    try:
        with build_opener(ProxyHandler({})).open(URL+'/api/health',timeout=2) as response:
            return json.load(response)
    except (URLError, OSError, ValueError):
        return None


def compatible(data):
    return data and data.get('service_id')=='xiyan-review' and data.get('version')==EXPECTED_VERSION and data.get('workspace_connected') is True


def port_in_use():
    with socket.socket() as connection:
        connection.settimeout(.5)
        return connection.connect_ex(('127.0.0.1', 8771)) == 0


def spawn_server(executable, out, err):
    args = [str(executable), '-m', 'uvicorn', 'app:app', '--host',
            '127.0.0.1', '--port', '8771', '--workers', '1', '--no-access-log']
    options = dict(cwd=ROOT/'backend', stdin=subprocess.DEVNULL, stdout=out, stderr=err)
    if os.name != 'nt':
        return subprocess.Popen(args, start_new_session=True, **options)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        # Detached alone does not escape a parent application's Windows Job.
        # Break away where permitted so closing the development host need not kill us.
        return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
    except OSError as exc:
        if exc.winerror != 5:
            raise
        # Some hosts disallow breakaway. An independently double-clicked launcher
        # remains the supported recovery path; never install startup tasks silently.
        print('Host disallows independent process lifetime. Use 启动析言.cmd outside the development app.', file=sys.stderr)
        return subprocess.Popen(args, creationflags=flags, **options)


def main(open_browser=True):
    data=health()
    if data and not compatible(data):
        raise RuntimeError('Port 8771 runs a different/older service. Stop that service before starting Xiyan 0.2.0.')
    if not data:
        if port_in_use():
            raise RuntimeError('8771端口已被占用，但不是可用的析言服务。请检查占用进程，不要重复启动。')
        executable=ROOT/'backend'/'.venv'/'Scripts'/'python.exe'
        if not executable.exists():
            raise RuntimeError('Backend .venv is missing. Follow backend/README.md to install dependencies.')
        with (ROOT/'backend'/'server.stdout.log').open('ab') as out, (ROOT/'backend'/'server.stderr.log').open('ab') as err:
            process = spawn_server(executable, out, err)
        for _ in range(40):
            data=health()
            if compatible(data): break
            if data:
                raise RuntimeError('8771端口出现其他服务，启动已停止。')
            if process.poll() is not None:
                # Another simultaneous launcher may have won the port.
                if compatible(health()):
                    break
                raise RuntimeError('析言后端启动后退出。请查看 backend/server.stderr.log。')
            time.sleep(.5)
        else:
            raise RuntimeError('Backend did not become ready. See backend/server.stderr.log; no browser was opened.')
    print('Xiyan 0.2.0 ready: '+URL)
    if open_browser: webbrowser.open(URL)


if __name__=='__main__':
    try:
        main(open_browser='--no-browser' not in sys.argv)
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
