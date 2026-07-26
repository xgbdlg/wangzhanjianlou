import json
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = 'http://127.0.0.1:8082'


def request(path, method='GET', data=None):
    url = BASE.rstrip('/') + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = Request(url, data=body, headers=headers)
    req.get_method = lambda: method
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, json.load(resp)
    except HTTPError as err:
        try:
            return err.code, json.loads(err.read().decode('utf-8'))
        except Exception:
            return err.code, err.read().decode('utf-8')


def main():
    print('health', request('/api/health'))
    print('health slash', request('/api/health/'))
    print('init', request('/api/init', method='POST', data={'master_password': 'test1234'}))
    print('init slash', request('/api/init/', method='POST', data={'master_password': 'test1234'}))
    print('create account', request('/api/accounts', method='POST', data={'name': '账号A', 'api_key': 'api_key_xxx', 'empire_rate': 0.65}))
    print('create account slash', request('/api/accounts/', method='POST', data={'name': '账号B', 'api_key': 'api_key_xxx2', 'empire_rate': 0.75}))
    print('list accounts', request('/api/accounts'))
    print('list accounts slash', request('/api/accounts/'))
    print('switch', request(f'/api/accounts/{quote("账号A")}/switch', method='POST', data={}))
    print('switch slash', request(f'/api/accounts/{quote("账号A")}/switch/', method='POST', data={}))
    print('current', request('/api/accounts/current'))
    print('current slash', request('/api/accounts/current/'))
    print('get strategy global', request('/api/strategy'))
    print('get strategy global slash', request('/api/strategy/'))
    print('save strategy', request('/api/strategy', method='POST', data={
        'account_name': '账号A',
        'buff_rate': 0.138,
        'min_deal_pct': 15.0,
        'max_loss_pct': -5.0,
        'auto_bid': True,
        'auto_buy': False,
        'max_bid_usd': 500.0,
        'max_buy_usd': 500.0,
        'min_item_price': 5.0,
        'max_item_price': 2000.0,
        'whitelist': '[]',
        'blacklist': '[]',
        'wear_filter': '[]',
        'bid_delay_ms': 500,
    }))
    print('save strategy slash', request('/api/strategy/', method='POST', data={
        'account_name': '账号B',
        'buff_rate': 0.25,
        'min_deal_pct': 20.0,
        'max_loss_pct': -4.0,
        'auto_bid': True,
        'auto_buy': True,
        'max_bid_usd': 300.0,
        'max_buy_usd': 300.0,
        'min_item_price': 20.0,
        'max_item_price': 1800.0,
        'whitelist': '[]',
        'blacklist': '[]',
        'wear_filter': '[]',
        'bid_delay_ms': 450,
    }))
    print('get strategy account', request(f'/api/strategy?account_name={quote("账号A")}'))
    print('get strategy account slash', request(f'/api/strategy/?account_name={quote("账号B")}'))


if __name__ == '__main__':
    main()
