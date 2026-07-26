import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = 'http://127.0.0.1:8081'

def request(path, method='GET', data=None):
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = Request(BASE + path, data=body, headers=headers)
    req.get_method = lambda: method
    try:
        with urlopen(req) as resp:
            return resp.status, resp.read().decode('utf-8')
    except HTTPError as err:
        return err.code, err.read().decode('utf-8')

if __name__ == '__main__':
    print('strategy get global', request('/api/strategy', 'GET'))
    print('strategy save account', request('/api/strategy', 'POST', {
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
    }))
    print('strategy get account', request('/api/strategy?account_name=%E8%B4%A6%E5%8F%B7C', 'GET'))
