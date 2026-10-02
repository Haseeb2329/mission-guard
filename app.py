"""Mission Guard: dependency-free, local educational spacecraft security simulator."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import json, os, random, threading, time, uuid

ROOT = Path(__file__).parent
HOST = os.getenv('MISSION_HOST', '127.0.0.1')
PORT = int(os.getenv('MISSION_PORT', '8765'))
TOKEN = os.getenv('MISSION_TOKEN', 'demo-operator-token')
LOCK = threading.RLock()
FAILED_AUTH = {}
AUTH_WINDOW_SECONDS = 60
AUTH_ALERT_THRESHOLD = 3
state = {'tick': 0, 'mode': 'NORMAL', 'link': 'ONLINE', 'radiation': 18,
         'battery': 86, 'temperature': 21, 'alerts': [], 'events': [],
         'commands': [], 'last_telemetry': None}

def event(kind, message):
    entry = {'id': uuid.uuid4().hex[:8], 'at': int(time.time()), 'kind': kind, 'message': message}
    state['events'].insert(0, entry)
    state['events'] = state['events'][:100]
    return entry

def alert(severity, message):
    if not any(a['message'] == message and not a['resolved'] for a in state['alerts']):
        a = {'id': uuid.uuid4().hex[:8], 'at': int(time.time()), 'severity': severity,
             'message': message, 'resolved': False}
        state['alerts'].insert(0, a)
        event('ALERT', message)

def assess():
    if state['radiation'] >= 70: alert('CRITICAL', 'Radiation exceeds safety threshold (70 mSv/h)')
    if state['battery'] <= 25: alert('HIGH', 'Battery below 25% reserve')
    if state['temperature'] >= 45: alert('HIGH', 'Spacecraft temperature exceeds 45 °C')
    if state['link'] == 'OFFLINE': alert('HIGH', 'Ground link unavailable; autonomous safety policy active')
    if state['radiation'] >= 70 or state['battery'] <= 15 or state['temperature'] >= 55:
        if state['mode'] != 'SAFE':
            state['mode'] = 'SAFE'
            event('AUTONOMY', 'Entered SAFE mode under deterministic safety policy')

def snapshot():
    return {k: v for k, v in state.items() if k != 'last_telemetry'}

def record_failed_auth(source, now=None):
    """Track failed command authentication without recording submitted credentials."""
    now = time.time() if now is None else now
    with LOCK:
        attempts = [t for t in FAILED_AUTH.get(source, []) if now - t < AUTH_WINDOW_SECONDS]
        attempts.append(now)
        FAILED_AUTH[source] = attempts
        event('SECURITY', f'Unauthorized command request rejected from {source}')
        if len(attempts) >= AUTH_ALERT_THRESHOLD:
            alert('HIGH', f'Repeated unauthorized command attempts from {source}')
        return len(attempts)

def act(action, data):
    with LOCK:
        if action == 'tick':
            state['tick'] += 1
            state['battery'] = max(0, state['battery'] - random.choice([0, 1, 2]))
            state['temperature'] = max(-20, min(70, state['temperature'] + random.choice([-1, 0, 1])))
            state['radiation'] = max(0, state['radiation'] + random.choice([-2, -1, 0, 1, 2]))
            state['last_telemetry'] = int(time.time())
            assess()
        elif action == 'scenario':
            scenario = data.get('name')
            if scenario == 'solar_storm': state['radiation'] = 88
            elif scenario == 'battery_failure': state['battery'] = 12
            elif scenario == 'overheat': state['temperature'] = 59
            elif scenario == 'link_outage': state['link'] = 'OFFLINE'
            elif scenario == 'recovery':
                state.update(radiation=18, battery=86, temperature=21, link='ONLINE', mode='NORMAL')
                for a in state['alerts']: a['resolved'] = True
            else: raise ValueError('Unknown scenario')
            event('SCENARIO', scenario.replace('_', ' ').title())
            assess()
        elif action == 'propose':
            command = data.get('command')
            if command not in ('ENTER_SAFE', 'RESUME_NORMAL', 'RESTART_LINK'):
                raise ValueError('Command not allowed')
            item = {'id': uuid.uuid4().hex[:8], 'command': command, 'status': 'PENDING',
                    'at': int(time.time())}
            state['commands'].insert(0, item)
            event('COMMAND', f'{command} proposed; awaiting approval')
        elif action == 'approve':
            item = next((c for c in state['commands'] if c['id'] == data.get('id')), None)
            if not item or item['status'] != 'PENDING': raise ValueError('Pending command not found')
            if item['command'] == 'RESUME_NORMAL' and (state['radiation'] >= 70 or state['battery'] <= 25 or state['temperature'] >= 45):
                item['status'] = 'BLOCKED'
                event('POLICY', 'Unsafe RESUME_NORMAL blocked by safety interlock')
            else:
                if item['command'] == 'ENTER_SAFE': state['mode'] = 'SAFE'
                if item['command'] == 'RESUME_NORMAL': state['mode'] = 'NORMAL'
                if item['command'] == 'RESTART_LINK': state['link'] = 'ONLINE'
                item['status'] = 'EXECUTED'
                event('COMMAND', f"{item['command']} approved and executed")
                assess()
        else: raise ValueError('Unknown action')
        return snapshot()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'static'), **kwargs)
    def log_message(self, fmt, *args): pass
    def respond(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if urlparse(self.path).path == '/api/state':
            with LOCK: self.respond(200, snapshot())
        elif urlparse(self.path).path in ('/', '/index.html'):
            self.path = '/index.html'
            super().do_GET()
        else: self.send_error(404)
    def do_POST(self):
        if urlparse(self.path).path != '/api/action': return self.respond(404, {'error': 'Not found'})
        if self.headers.get('X-Mission-Token') != TOKEN:
            record_failed_auth(self.client_address[0])
            return self.respond(401, {'error': 'Unauthorized'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size > 4096: return self.respond(413, {'error': 'Request too large'})
            data = json.loads(self.rfile.read(size))
            result = act(data.get('action'), data)
            self.respond(200, result)
        except (ValueError, json.JSONDecodeError, TypeError) as exc:
            self.respond(400, {'error': str(exc)})

if __name__ == '__main__':
    event('SYSTEM', 'Mission Guard simulator initialized')
    print(f'Mission Guard running at http://{HOST}:{PORT}')
    print(f'Demo token: {TOKEN}')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
