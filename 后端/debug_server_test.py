import json
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import uvicorn
from main import app

BASE = 'http://127.0.0.1:8082'


def request(path, method='GET', data=None):
    body = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
    headers = {'Content-Type': 'application/json'} if data is not None else {}
    req = Request(BASE + path, data=body, headers=headers)
    req.get_method = lambda: method
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode('utf-8')
    except HTTPError as err:
        return err.code, err.read().decode('utf-8')
    except Exception as exc:
        return None, str(exc)


def run_server():
    uvicorn.run(app, host='127.0.0.1', port=8082, log_level='info')


if __name__ == '__main__':
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    time.sleep(1)
    tests = [
        ('/api/health', 'GET', None),
        ('/api/init', 'POST', {'master_password': 'test1234'}),
        ('/api/accounts/', 'POST', {'name': '账号C', 'api_key': 'keyC', 'empire_rate': 0.7}),
        ('/api/accounts/', 'GET', None),
        ('/api/strategy', 'GET', None),
        ('/api/strategy', 'POST', {
            'account_name': '账号C',
            'buff_rate': 0.2,
            'min_deal_pct': 10.0,
            'max_loss_pct': -5.0,
            'auto_bid': True,
            'auto_buy': False,
            'max_bid_usd': 200.0,
            'max_buy_usd': 300.0,
            'min_item_price': 10.0,
            'max_item_price': 1500.0,
            'whitelist': '[]',
            'blacklist': '[]',
            'wear_filter': '[]',
            'bid_delay_ms': 400,
        }),
        ('/api/strategy?account_name=%E8%B4%A6%E5%8F%B7C', 'GET', None),
    ]
    for path, method, data in tests:
        status, body = request(path, method, data)
        print(path, method, status, body)
    print('done')
