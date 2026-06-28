import requests, json
body = {
    'email':'smokeuser@example.com',
    'event_id':'smoke-test-event',
    'tickets':1,
    'payment_status':'paid',
    'upi_reference':'SMOKEREF123',
    'user_upi_id':'test@upi',
    'amount_paid':100,
    'event_amount':100,
    'user_service_fee':0,
    'name':'Smoke User',
    'phone':'9999999999',
    'client_action_id':'smoke-1'
}
print('SENDING')
r = requests.post('https://orbit-persoanlised-event-discovery.onrender.com/register-event', json=body)
print('STATUS', r.status_code)
print('TEXT', r.text)
try:
    print('JSON', r.json())
except Exception as e:
    print('NO JSON', e)
