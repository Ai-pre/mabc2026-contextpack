import urllib.request, json

data = json.dumps({'role': 'Backend Developer', 'task': 'fix partial refund'}).encode()
req = urllib.request.Request(
    'http://127.0.0.1:8000/analyze',
    data=data,
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    resp = urllib.request.urlopen(req, timeout=10)
    print('HTTP', resp.status)
    print(resp.read().decode()[:500])
except urllib.error.HTTPError as e:
    print('HTTPError', e.code, e.reason)
    print(e.read().decode()[:500])
except Exception as e:
    print('ERROR', type(e).__name__, str(e)[:300])
