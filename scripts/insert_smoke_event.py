from pymongo import MongoClient
import datetime

client = MongoClient('mongodb://localhost:27017/')
db = client['orbit']
events = db['events']

ev = {
    'event_id': 'smoke-test-event',
    'title': 'Smoke Test Event',
    'total_slots': 10,
    'registered_count': 0,
    'price': 100,
    'status': 'Active',
    'approval_status': 'Approved',
    'start_date': (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
    'end_date': (datetime.date.today() + datetime.timedelta(days=2)).strftime('%Y-%m-%d'),
    'organiser_email': 'organiser@example.com'
}
res = events.update_one({'event_id': ev['event_id']}, {'$set': ev}, upsert=True)
print('EVENT_UPSERT_DONE', getattr(res, 'upserted_id', None))
print(events.find_one({'event_id': ev['event_id']}))
