import sys, unittest, json, threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

class MissionTests(unittest.TestCase):
    def setUp(self):
        app.state.update(tick=0, mode='NORMAL', link='ONLINE', radiation=18, battery=86,
                         temperature=21, alerts=[], events=[], commands=[])
        app.FAILED_AUTH.clear()
    def test_storm_blocks_resume_until_recovery(self):
        s = app.act('scenario', {'name':'solar_storm'})
        self.assertEqual(s['mode'], 'SAFE')
        self.assertEqual(len(s['alerts']), 1)
        app.act('propose', {'command':'RESUME_NORMAL'})
        s = app.act('approve', {'id':app.state['commands'][0]['id']})
        self.assertEqual(s['commands'][0]['status'], 'BLOCKED')
        app.act('scenario', {'name':'recovery'})
        app.act('propose', {'command':'RESUME_NORMAL'})
        s = app.act('approve', {'id':app.state['commands'][0]['id']})
        self.assertEqual(s['mode'], 'NORMAL')
        self.assertTrue(all(a['resolved'] for a in s['alerts']))
    def test_allowlist(self):
        with self.assertRaises(ValueError): app.act('propose', {'command':'DELETE_ALL'})
    def test_link_recovery(self):
        app.act('scenario', {'name':'link_outage'})
        app.act('propose', {'command':'RESTART_LINK'})
        s = app.act('approve', {'id':app.state['commands'][0]['id']})
        self.assertEqual(s['link'], 'ONLINE')

    def test_unauthorized_requests_create_security_alert_without_command(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/api/action'
            for _ in range(3):
                request = Request(url, data=json.dumps({'action':'propose', 'command':'ENTER_SAFE'}).encode(),
                                  headers={'Content-Type':'application/json', 'X-Mission-Token':'wrong'}, method='POST')
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 401)
            self.assertEqual(app.state['commands'], [])
            self.assertEqual(len(app.state['alerts']), 1)
            self.assertIn('unauthorized', app.state['alerts'][0]['message'])
            self.assertEqual(sum(e['kind'] == 'SECURITY' for e in app.state['events']), 3)
        finally:
            server.shutdown()
            server.server_close()

if __name__ == '__main__': unittest.main()
