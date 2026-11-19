"""Idempotent shared showcase accounts. Never represents real identities or funds."""
import uuid
from datetime import timedelta
from werkzeug.security import generate_password_hash
from .domain import new_circle, now, stamp

PASSWORD = 'DemoHalal2026!'
ACCOUNTS = [
    ('Shehzeen Fatima', 'shehzeen@demo.halalchit.test'),
    ('Shaik Asefa', 'asefa@demo.halalchit.test'),
    ('maryam Mohd', 'maryam@demo.halalchit.test'),
    ('nabeehunnisa', 'nabeehunnisa@demo.halalchit.test'),
]


def seed(store):
    if store.get('meta:demo-v1'):
        return
    users = []
    for name, email in ACCOUNTS:
        key = 'user:' + email
        row = store.get(key)
        if row:
            user = row['payload']
        else:
            user = dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL, email)), name=name, email=email, passwordHash=generate_password_hash(PASSWORD), createdAt=stamp(), notificationsReadAt=None, demo=True)
            store.put(key, user)
        users.append(user)
    at = now()
    circle = new_circle(users[0], dict(name='Team savings · demo', description='Shared university showcase. All contributions and payouts are simulated.', amount=50000, capacity=4, startDate=(at + timedelta(days=1)).date().isoformat(), visibility='private', adult=True, accepted=True, operationId='seed-showcase-circle-v1'), at)
    circle['members'] = [dict(userId=u['id'], name=u['name'], position=i + 1, acceptedAt=stamp(at), agreementVersion=1) for i, u in enumerate(users)]
    circle.update(status='active', startedAt=stamp(at))
    store.put('circle:' + circle['id'], circle)
    store.put('meta:demo-v1', {'seededAt': stamp(), 'circleId': circle['id']})
