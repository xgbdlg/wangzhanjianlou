import json
from urllib.request import Request, urlopen

base = 'http://127.0.0.1:8080'

req = Request(f'{base}/api/health')
with urlopen(req) as resp:
    print('health', resp.read().decode())

body = json.dumps({'master_password': 'test1234'}).encode('utf-8')
req = Request(f'{base}/api/init', data=body, headers={'Content-Type': 'application/json'}, method='POST')
with urlopen(req) as resp:
    print('init', resp.read().decode())
