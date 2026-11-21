"""Local-only submission launcher; uses the project's real HTTP and TCP servers."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from http import HTTPStatus
import json
import os
from pathlib import Path
import socket
import sys
import threading
import urllib.request
import webbrowser


HOST = '127.0.0.1'


def prepared_html(html: str, tcp_port: int, scenario: str, auto_run: bool) -> bytes:
    html = html.replace('id="port" value="9090"', f'id="port" value="{tcp_port}"')
    html = html.replace('name="mode" value="live" checked', 'name="mode" value="live"')
    html = html.replace('name="mode" value="demo"', 'name="mode" value="demo" checked')
    html = html.replace('TCP 9090', f'TCP {tcp_port}').replace('tcp.port == 9090', f'tcp.port == {tcp_port}')
    html = html.replace(f'<option value="{scenario}">', f'<option value="{scenario}" selected>')
    startup = 'updateMode();' + ('document.querySelector("#diagnose").click();' if auto_run else '')
    html = html.replace('</body>', f'<script>{startup}</script>\n</body>')
    return html.encode('utf-8')


class ExclusivePort:
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def start_services(stack: ExitStack, scenario: str, auto_run: bool):
    from app.server import DiagnosticServer, DiagnosticHandler
    from app.web_client import DiagnosticWebServer, WebClientHandler, STATIC_DIR

    class TCPServer(ExclusivePort, DiagnosticServer):
        pass

    class WebServer(ExclusivePort, DiagnosticWebServer):
        daemon_threads = True

    def bind(server_type, handler, preferred):
        try:
            return server_type((HOST, preferred), handler)
        except OSError:
            return server_type((HOST, 0), handler)

    tcp = stack.enter_context(bind(TCPServer, DiagnosticHandler, 62036))
    tcp_port = tcp.server_address[1]

    class Handler(WebClientHandler):
        def _send_file(self, path, content_type):
            if path == STATIC_DIR / 'index.html':
                data = prepared_html(path.read_text(encoding='utf-8'), tcp_port, scenario, auto_run)
                self.send_response(HTTPStatus.OK)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
            else:
                super()._send_file(path, content_type)

        def log_message(self, *_args):
            pass

    web = stack.enter_context(bind(WebServer, Handler, 62039))
    for service in (tcp, web):
        thread = threading.Thread(target=service.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        thread.start()
        # ExitStack runs these callbacks before closing sockets.
        stack.callback(thread.join, 3)
        stack.callback(service.shutdown)
    return tcp_port, web.server_address[1]


def check_services(tcp_port: int, web_port: int, scenario: str):
    url = f'http://{HOST}:{web_port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url+'/', timeout=5) as response:
        html = response.read().decode('utf-8')
    assert f'id="port" value="{tcp_port}"' in html
    assert 'name="mode" value="demo" checked' in html
    for resource in ('style.css', 'app.js', 'target-choices.js'):
        with opener.open(url+'/'+resource, timeout=5) as response:
            assert response.status == 200
    payload = json.dumps({'host': HOST, 'port': tcp_port, 'mode': 'demo', 'demo_scenario': scenario}).encode()
    request = urllib.request.Request(url+'/api/diagnose', data=payload, headers={'Content-Type':'application/json'})
    with opener.open(request, timeout=8) as response:
        result = json.load(response)
    expected = {'mixed':['NORMAL','WARNING','DOWN'], 'normal':['NORMAL'], 'warning':['WARNING'], 'down':['DOWN']}[scenario]
    assert [r['status'] for r in result['results']] == expected
    assert all(r['mode'] == 'DEMO' and 'finding' in r and 'next_check' in r for r in result['results'])
    assert len(result['osi_trace']) == 7
    return {'tcp_port':tcp_port,'web_port':web_port,'statuses':expected, 'osi_layers':7,'mode':'DEMO',
            'scenario':scenario, 'findings':[r['finding'] for r in result['results']]}


def main():
    if sys.version_info < (3,14,3):
        raise RuntimeError('Python 3.14.3 이상을 설치한 뒤 다시 실행해 주세요.')
    parser = argparse.ArgumentParser(description='로컬 DEMO 실행')
    parser.add_argument('--self-test', action='store_true', help='브라우저를 열지 않고 실행 확인 후 종료')
    parser.add_argument('--scenario', choices=('mixed','normal','warning','down'), default='mixed')
    parser.add_argument('--auto-run', action='store_true', help='화면을 열면 선택한 DEMO를 자동 진단')
    args = parser.parse_args()
    # This launch mode never inherits real DB credentials from another project.
    for key in tuple(os.environ):
        if key.startswith(('DB_', 'DB2_', 'DB3_')):
            os.environ.pop(key, None)
    with ExitStack() as stack:
        tcp_port, web_port = start_services(stack, args.scenario, args.auto_run)
        result = check_services(tcp_port, web_port, args.scenario)
        if args.self_test:
            print(json.dumps(result))
            return
        url = f'http://{HOST}:{web_port}/'
        print('\nDBMS 네트워크 접속 진단 - DEMO 실행 준비 완료')
        print(f'화면 주소: {url}')
        print(f'진단 서버: {HOST}:{tcp_port}')
        print(f'시나리오: {args.scenario} / 결과: {", ".join(result["statuses"])}')
        for index,(status,finding) in enumerate(zip(result['statuses'],result['findings']),1):
            print(f'[DEMO] DB {index}: {status} - {finding}')
        print('화면에서 [진단 시작]으로 다시 확인하거나 모의 상황을 변경할 수 있습니다.')
        print('Python 표준 라이브러리로 실행하며 MariaDB 설치·실제 DB 접속은 하지 않습니다.')
        print('포트가 사용 중이면 다른 빈 포트를 선택합니다. 실행 중인 다른 프로그램은 종료하지 않습니다.')
        try:
            opened = webbrowser.open(url, new=2)
        except webbrowser.Error:
            opened = False
        if not opened:
            print('브라우저를 직접 열고 위 화면 주소를 입력해 주세요.')
        try:
            input('\n이 창을 유지해 주세요. 종료하려면 Enter 또는 Ctrl+C를 누르세요. ')
        except (KeyboardInterrupt, EOFError):
            pass
    print('제출용 DEMO 서버와 웹 화면 서버를 종료했습니다.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'실행을 완료하지 못했습니다: {exc}', file=sys.stderr)
        sys.exit(1)
