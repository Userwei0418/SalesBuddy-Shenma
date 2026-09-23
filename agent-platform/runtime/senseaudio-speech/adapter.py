"""Private OpenAI TTS -> SenseAudio adapter. No stored credentials or request logs."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore
import requests

SLOTS = BoundedSemaphore(4)
MODELS = {'sensenova-tts-2.0', 'senseaudio-tts-1.5-260319'}


def payload(body):
    model, text = body.get('model'), body.get('input')
    if model not in MODELS or not isinstance(text, str) or not 0 < len(text) <= 10000:
        raise ValueError('Unsupported model or invalid text length')
    if body.get('response_format', 'mp3') != 'mp3':
        raise ValueError('Only mp3 is configured')
    voice = body.get('voice') or 'female_0033_b'
    if not isinstance(voice, str) or len(voice) > 128:
        raise ValueError('Invalid voice')
    return {'model': model, 'text': text, 'stream': False,
            'voice_setting': {'voice_id': voice, 'speed': 1, 'vol': 1, 'pitch': 0},
            'audio_setting': {'format': 'mp3', 'sample_rate': 32000, 'bitrate': 128000, 'channel': 1}}


def decode(result):
    if result.get('base_resp', {}).get('status_code') != 0:
        raise ValueError('SenseAudio synthesis failed')
    audio = bytes.fromhex(result['data']['audio'])
    if not audio:
        raise ValueError('Empty audio')
    return audio


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data, content_type='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply(200 if self.path == '/health' else 404, b'{}')

    def do_POST(self):
        if self.path != '/v1/audio/speech':
            return self.reply(404, b'{}')
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or len(auth) < 12:
            return self.reply(401, b'{"error":"Missing API key"}')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 100000:
                raise ValueError()
            self.connection.settimeout(15)
            request = payload(json.loads(self.rfile.read(length)))
        except (ValueError, TypeError, AttributeError):
            return self.reply(400, b'{"error":"Invalid synthesis request"}')
        if not SLOTS.acquire(blocking=False):
            return self.reply(429, b'{"error":"Speech service busy"}')
        try:
            r = requests.post('https://api.senseaudio.cn/v1/t2a_v2',
                              headers={'Authorization': auth}, json=request,
                              timeout=(10, 180), allow_redirects=False)
            if r.status_code != 200:
                return self.reply(r.status_code if r.status_code in (400,401,403,429) else 502,
                                  b'{"error":"SenseAudio upstream request failed"}')
            self.reply(200, decode(r.json()), 'audio/mpeg')
        except Exception:
            self.reply(502, b'{"error":"SenseAudio synthesis failed"}')
        finally:
            SLOTS.release()


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8099), Handler).serve_forever()
