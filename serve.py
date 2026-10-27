#!/usr/bin/env python3
"""Local server with security headers, native AI classifier, and /api proxy. Usage: python3 serve.py"""
import atexit
import base64
import glob
import io
import json
import os
import posixpath
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVICE_DIR = os.path.join(ROOT, 'ecorewind_ml', 'service')
MODEL_DIR = os.path.join(ROOT, 'ecorewind_ml', 'outputs')

VENV_PYTHON = os.path.join(ROOT, 'ecorewind_ml', '.venv', 'bin', 'python')
if os.path.exists(VENV_PYTHON) and os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)

HOST = os.environ.get('HOST', '127.0.0.1')
PORT = int(os.environ.get('PORT', '8000'))
SERVICE_URL = os.environ.get('ECOREWIND_SERVICE_URL', 'http://127.0.0.1:8100').rstrip('/')
API_KEY = os.environ.get('ECOREWIND_API_KEY', '')
MAX_BODY_BYTES = 10_000_000
UPSTREAM_TIMEOUT_S = 20

API_ROUTE = re.compile(
    r'/(health|rates|scan)'
    r'|/users/[A-Za-z0-9_.@-]{1,64}/(wallet|transactions|withdrawals)'
)
FORWARD_HEADERS = ('Content-Type', 'Accept', 'Idempotency-Key')
STATIC_DIRS = ('/css/', '/js/', '/screenshots/')

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob: mediastream:; "
    "connect-src 'self' https://cdn.jsdelivr.net https://storage.googleapis.com https://tfhub.dev; "
    "worker-src 'self' blob:; frame-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'self'"
)

SCANNER = None


def get_scanner():
    global SCANNER
    if SCANNER is None and os.path.exists(os.path.join(MODEL_DIR, 'ecorewind_classifier.keras')):
        try:
            sys.path.insert(0, os.path.join(ROOT, 'ecorewind_ml', 'scripts'))
            from predict import Scanner
            SCANNER = Scanner(model_dir=MODEL_DIR, threshold=0.65)
            print('EcoRewind ML model loaded directly in server!')
        except Exception as e:
            print('Notice: Could not load Scanner:', e)
    return SCANNER


def csp_for(path):
    clean_path = path.split('?', 1)[0]
    if clean_path in ('/terminal.html', '/terminal'):
        return CSP.replace("script-src 'self' https://cdn.jsdelivr.net",
                           "script-src 'self' 'unsafe-eval' https://cdn.jsdelivr.net")
    return CSP


def static_allowed(path):
    path = posixpath.normpath(urllib.parse.unquote(path))
    if path == '/':
        return True
    if any(part.startswith('.') for part in path.split('/') if part):
        return False
    if path.count('/') == 1:
        return path.endswith('.html')
    return path.startswith(STATIC_DIRS)


class Handler(SimpleHTTPRequestHandler):
    server_version = 'EcoRewind'
    sys_version = ''

    def end_headers(self):
        self.send_header('Content-Security-Policy', csp_for(self.path))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.send_header('Permissions-Policy', 'camera=(self), microphone=(), geolocation=()')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403, 'Directory listing disabled')
        return None

    def _send(self, status, body, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, code, message):
        self._send(status, json.dumps({'error': {'code': code, 'message': message}}).encode('utf-8'))

    def _handle_classify(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length <= 0:
                return self._send(200, json.dumps({
                    'decision': 'rejected', 'reason': 'No frame received',
                    'label': 'other_invalid', 'display_label': 'Chamber empty',
                    'confidence': 0.0, 'scores': {}, 'rate_paise_per_gram': 0,
                    'key': 'other_invalid', 'valid': False
                }).encode('utf-8'))

            raw_body = self.rfile.read(length)
            content_type = self.headers.get('Content-Type', '')

            img_bytes = None
            if 'multipart/form-data' in content_type:
                boundary = content_type.split('boundary=', 1)[1].strip()
                if boundary.startswith('"') and boundary.endswith('"'):
                    boundary = boundary[1:-1]
                boundary_bytes = ('--' + boundary).encode('utf-8')
                parts = raw_body.split(boundary_bytes)
                for part in parts:
                    if b'name="image"' in part:
                        header_end = part.find(b'\r\n\r\n')
                        if header_end != -1:
                            payload = part[header_end + 4:]
                            if payload.endswith(b'\r\n'):
                                payload = payload[:-2]
                            img_bytes = payload
                            break
            else:
                data = json.loads(raw_body.decode('utf-8'))
                img_data = data.get('image', '').strip()
                if ',' in img_data:
                    img_data = img_data.split(',', 1)[1].strip()
                if img_data:
                    img_bytes = base64.b64decode(img_data)

            if not img_bytes or len(img_bytes) < 100:
                return self._send(200, json.dumps({
                    'decision': 'rejected', 'reason': 'Align item in chamber',
                    'label': 'other_invalid', 'display_label': 'Chamber empty',
                    'confidence': 0.0, 'scores': {}, 'rate_paise_per_gram': 0,
                    'key': 'other_invalid', 'valid': False
                }).encode('utf-8'))

            from PIL import Image
            import numpy as np

            im = Image.open(io.BytesIO(img_bytes)).convert('RGB')
            arr = np.asarray(im)

            scanner = get_scanner()
            if scanner is None:
                return self._send(200, json.dumps({
                    'decision': 'rejected', 'reason': 'AI model is initializing…',
                    'label': 'other_invalid', 'display_label': 'AI loading',
                    'confidence': 0.0, 'scores': {}, 'rate_paise_per_gram': 0,
                    'key': 'other_invalid', 'valid': False
                }).encode('utf-8'))

            result = scanner.classify(arr)

            display_labels = {
                'plastic_bottle': 'Plastic bottle',
                'aluminium_can': 'Aluminium can',
                'plastic_bag': 'Plastic bag',
                'other_invalid': 'Invalid item',
            }
            rates_map = {
                'plastic_bottle': 2,
                'aluminium_can': 4,
                'plastic_bag': 1,
                'other_invalid': 0,
            }

            model_label = result['label']
            display = display_labels.get(model_label, model_label.replace('_', ' ').title())
            rate = rates_map.get(model_label, 0)
            is_accepted = (result['decision'] == 'accepted') and (rate > 0)

            out = {
                'decision': 'accepted' if is_accepted else 'rejected',
                'reason': result['reason'],
                'label': model_label,
                'display_label': display,
                'confidence': result['confidence'],
                'scores': result['scores'],
                'rate_paise_per_gram': rate,
                'rate_paise': rate,
                'key': model_label,
                'valid': is_accepted,
                'model': '4-Class MobileNetV2',
            }
            return self._send(200, json.dumps(out).encode('utf-8'))
        except Exception as e:
            return self._send(200, json.dumps({
                'decision': 'rejected',
                'reason': str(e),
                'label': 'other_invalid',
                'display_label': 'Align item in chamber',
                'confidence': 0.0,
                'scores': {},
                'rate_paise_per_gram': 0,
                'key': 'other_invalid',
                'valid': False,
                'model': '4-Class MobileNetV2',
            }).encode('utf-8'))

    def _proxy(self):
        path, _, query = self.path.partition('?')
        upstream_path = path[len('/api'):]
        if not API_ROUTE.fullmatch(upstream_path):
            return self._error(404, 'NOT_FOUND', 'Unknown API route')

        body = None
        if self.command == 'POST':
            length = self.headers.get('Content-Length', '')
            if not length.isdigit():
                return self._error(411, 'LENGTH_REQUIRED', 'Send a Content-Length header')
            if int(length) > MAX_BODY_BYTES:
                self.close_connection = True
                return self._error(413, 'BODY_TOO_LARGE', f'Requests must be under {MAX_BODY_BYTES} bytes')
            body = self.rfile.read(int(length))

        headers = {name: self.headers[name] for name in FORWARD_HEADERS if self.headers.get(name)}
        if API_KEY:
            headers['X-API-Key'] = API_KEY
        url = SERVICE_URL + upstream_path + ('?' + query if query else '')
        request = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT_S) as response:
                status, content_type, payload = (
                    response.status, response.headers.get('Content-Type'), response.read())
        except urllib.error.HTTPError as exc:
            status, content_type, payload = exc.code, exc.headers.get('Content-Type'), exc.read()
        except (urllib.error.URLError, OSError):
            return self._error(503, 'SERVICE_OFFLINE',
                               'The scanner service is not reachable. Check the serve.py console.')
        self._send(status, payload, content_type or 'application/json')

    def do_HEAD(self):
        clean_path = self.path.split('?', 1)[0]
        if clean_path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        if not static_allowed(clean_path):
            self.send_error(404)
            return
        super().do_HEAD()

    def do_GET(self):
        clean_path = self.path.split('?', 1)[0]
        if clean_path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        if clean_path == '/api/model-info':
            scanner = get_scanner()
            payload = {
                'model_loaded': scanner is not None,
                'labels': getattr(scanner, 'labels', []),
                'threshold': getattr(scanner, 'threshold', 0.65),
            }
            return self._send(200, json.dumps(payload).encode('utf-8'))
        if clean_path == '/api/sample':
            # Provides sample image data for one-click testing
            query = self.path.split('?', 1)[1] if '?' in self.path else ''
            category = query.split('class=', 1)[1].split('&', 1)[0] if 'class=' in query else 'plastic_bottle'
            sample_files = glob.glob(os.path.join(ROOT, 'ecorewind_ml', 'data', 'dataset', 'test', category, '*.jpg'))
            if sample_files:
                with open(sample_files[0], 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                return self._send(200, json.dumps({'ok': True, 'category': category, 'dataUrl': 'data:image/jpeg;base64,' + b64}).encode('utf-8'))
            return self._send(404, json.dumps({'ok': False, 'error': 'Sample not found'}).encode('utf-8'))

        if clean_path.startswith('/api/'):
            return self._proxy()
        if not static_allowed(clean_path):
            self.send_error(404)
            return
        super().do_GET()

    def do_POST(self):
        clean_path = self.path.split('?', 1)[0]
        if clean_path in ('/api/classify', '/api/predict'):
            return self._handle_classify()
        if self.path.startswith('/api/'):
            return self._proxy()
        self.send_error(404, 'Endpoint not found')


def service_up():
    try:
        with urllib.request.urlopen(SERVICE_URL + '/health', timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def start_service():
    """Starts the scanner service next to this server unless one is already running."""
    target = urllib.parse.urlsplit(SERVICE_URL)
    if os.environ.get('ECOREWIND_SPAWN_SERVICE', '1') == '0' or service_up():
        return None
    if target.hostname not in ('127.0.0.1', 'localhost'):
        return None
    print(f'Starting scanner service on {SERVICE_URL}...')
    process = subprocess.Popen(
        [VENV_PYTHON, '-m', 'uvicorn', 'app:app', '--host', target.hostname,
         '--port', str(target.port or 8100), '--log-level', 'warning'],
        cwd=SERVICE_DIR,
    )

    def stop():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    atexit.register(stop)
    return stop


def main():
    get_scanner()  # Warm up model at startup
    stop_service = start_service()
    httpd = ThreadingHTTPServer((HOST, PORT), partial(Handler, directory=ROOT))
    print(f'EcoRewind served at http://{HOST}:{PORT}/ (Ctrl+C to stop)')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if stop_service:
            stop_service()


if __name__ == '__main__':
    main()
