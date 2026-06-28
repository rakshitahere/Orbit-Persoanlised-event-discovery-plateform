from pymongo import MongoClient
import datetime

client = MongoClient('mongodb://localhost:27017/')
db = client['orbit']
users = db['users']

email = 'smokeuser@example.com'
doc = {
    'email': email,
    'name': 'Smoke User',
    'created_at': datetime.datetime.utcnow().isoformat(),
    'status': 'Active'
}
res = users.update_one({'email': email}, {'$setOnInsert': doc}, upsert=True)
print('UPSERT_DONE', getattr(res, 'upserted_id', None))
print(users.find_one({'email': email}))
