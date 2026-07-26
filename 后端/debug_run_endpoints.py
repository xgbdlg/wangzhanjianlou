import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = 'http://127.0.0.1:8081'

def req(path, method='GET', data=None):
    body = json.dumps(data).encode('utf-8') if data is not None else None
    headers = {'Content-Type': 'application/json'} if data is not None else {}
    request = Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request) as resp:
            return resp.status, resp.read().decode()
    except HTTPError as err:
        return err.code, err.read().decode()

if __name__ == '__main__':
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
        ('/api/strategy?account_name=账号C', 'GET', None),
    ]

    for path, method, data in tests:
        status, body = req(path, method, data)
        print(path, method, status, body)
