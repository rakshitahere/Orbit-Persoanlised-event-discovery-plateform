import requests
r = requests.get('https://orbit-persoanlised-event-discovery.onrender.com/api/events/smoke-test-event')
print('STATUS', r.status_code)
print('TEXT', r.text)
try:
    print('JSON', r.json())
except Exception as e:
    print('NO JSON', e)
