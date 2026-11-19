"""Pure Python ROSCA rules. Integer paise; simulated funds only."""
import copy
import hashlib
import secrets
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def check(condition, message, status=400):
    if not condition:
        raise AppError(message, status)


def text(value, minimum, maximum, label):
    check(isinstance(value, str), label + ' is required.')
    value = ' '.join(value.split())
    check(minimum <= len(value) <= maximum, f'{label} must be {minimum}–{maximum} characters.')
    return value


def number(value, minimum, maximum, label):
    check(type(value) is int and minimum <= value <= maximum, f'{label} must be between {minimum} and {maximum}.')
    return value


def now():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or now()).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def due(start, round_number):
    d = date.fromisoformat(start)
    y, m = divmod(d.year * 12 + d.month - 1 + round_number, 12)
    return date(y, m + 1, min(d.day, monthrange(y, m + 1)[1])).isoformat()


def uid():
    return str(uuid.uuid4())


def member(g, user_id):
    return any(m['userId'] == user_id for m in g['members'])


def invite(g, token):
    return isinstance(token, str) and secrets.compare_digest(token, g['id'] + '.' + g['inviteSecret'])


def paid(g, r=None):
    return [p for p in g['payments'] if p['round'] == (g['round'] if r is None else r) and p['status'] == 'succeeded']


def funded(g):
    payments = paid(g)
    return len(payments) == g['capacity'] and {p['userId'] for p in payments} == {m['userId'] for m in g['members']} and sum(p['amount'] for p in payments) == g['amount'] * g['capacity']


def event(g, kind, title, detail, user, at, subject=None):
    g['events'].insert(0, dict(id=uid(), type=kind, title=title, detail=detail, actorId=user['id'], actorName=user['name'], subjectUserId=subject, at=stamp(at)))
    g['events'] = g['events'][:1000]


def new_circle(user, body, at=None):
    at = at or now()
    name = text(body.get('name'), 3, 60, 'Circle name')
    description = text(body.get('description'), 5, 220, 'Description')
    amount = number(body.get('amount'), 10000, 10000000, 'Contribution in paise')
    capacity = number(body.get('capacity'), 3, 12, 'Number of members')
    check(body.get('visibility') in ['private', 'public'], 'Choose private or public visibility.')
    try:
        start = date.fromisoformat(body.get('startDate', '')).isoformat()
    except (ValueError, TypeError):
        raise AppError('Choose a valid first contribution date.')
    today = at.astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat()
    check(today <= start <= due(today, 24), 'Choose a date within the next two years.')
    check(body.get('adult') is True and body.get('accepted') is True, 'Confirm you are an adult and accept the agreement.')
    op = text(body.get('operationId'), 8, 100, 'Operation reference')
    gid = str(uuid.UUID(hashlib.sha256((user['id'] + ':' + op).encode()).hexdigest()[:32]))
    g = dict(id=gid, name=name, description=description, amount=amount, capacity=capacity, startDate=start, status='forming', visibility=body['visibility'], organizerId=user['id'], organizerName=user['name'], inviteSecret=secrets.token_hex(16), members=[dict(userId=user['id'], name=user['name'], position=1, acceptedAt=stamp(at), agreementVersion=1)], applications=[], payments=[], payouts=[], events=[], operations=[], round=0, createdAt=stamp(at), updatedAt=stamp(at))
    event(g, 'circle.created', 'Circle created', name, user, at)
    return g


def summary(g):
    return {**{k: g[k] for k in ['id', 'name', 'description', 'amount', 'capacity', 'startDate', 'status', 'visibility', 'organizerName']}, 'memberCount': len(g['members'])}


def view(g, user, token=None):
    is_member = member(g, user['id'])
    organizer = g['organizerId'] == user['id']
    apps = [a for a in g['applications'] if a['userId'] == user['id']]
    check(is_member or any(a['status'] == 'pending' for a in apps) or g['visibility'] == 'public' and g['status'] == 'forming' or invite(g, token), 'You do not have access to this circle.', 403)
    result = dict(**summary(g), **{k: g[k] for k in ['organizerId', 'round', 'createdAt', 'updatedAt']}, role='organizer' if organizer else 'member' if is_member else 'visitor', myApplication=apps[-1] if apps else None, preview=not is_member, agreement=dict(version=1, amount=g['amount'], capacity=g['capacity'], total=g['amount'] * g['capacity'], fee=0, interest=0, order='approval_order', missedPaymentPolicy='Payouts wait until all contributions succeed. No late fees or interest.'), deletedAt=g.get('deletedAt'), pauseReason=g.get('pauseReason'), inviteLinkToken=g['id'] + '.' + g['inviteSecret'] if organizer else None)
    result.update({k: g[k] if is_member and (k != 'applications' or organizer) else [] for k in ['members', 'applications', 'payments', 'payouts', 'events']})
    return copy.deepcopy(result)


def command(original, user, action, body, at=None):
    at = at or now()
    g = copy.deepcopy(original)
    operation = text(body.get('operationId'), 8, 100, 'Operation reference')
    prior = next((o for o in g['operations'] if o['id'] == operation and o['userId'] == user['id']), None)
    if prior:
        check(prior['action'] == action, 'Operation reference already used.', 409)
        return g, prior['result']
    check(g['status'] != 'deleted' or action == 'delete', 'Circle deleted. Payment history remains available.', 409)
    if action in ['delete', 'review', 'start', 'pause', 'resume', 'payout']:
        check(g['organizerId'] == user['id'], 'Only the circle organizer can do this.', 403)
    if action in ['checkout', 'confirm', 'cancel']:
        check(member(g, user['id']), 'Only members can do this.', 403)
    if action in ['join', 'review', 'start']:
        check(g['status'] == 'forming', 'Membership is locked once a circle starts.', 409)
    if action in ['pause', 'payout', 'checkout']:
        check(g['status'] == 'active', 'This circle is not active or is paused.', 409)
    result = {}
    if action == 'delete':
        check(body.get('confirmName') == g['name'], 'Type the exact circle name to confirm deletion.')
        if g['status'] != 'deleted':
            g.update(previousStatus=g['status'], status='deleted', deletedAt=stamp(at))
            for p in g['payments']:
                if p['status'] == 'pending':
                    p.update(status='cancelled', completedAt=stamp(at), reason='Circle deleted. No funds recorded.')
            for a in g['applications']:
                if a['status'] == 'pending':
                    a.update(status='rejected', reviewedAt=stamp(at))
            event(g, 'circle.deleted', 'Circle deleted', 'Past payments and receipts are retained.', user, at)
    elif action == 'join':
        check(g['visibility'] == 'public' or invite(g, body.get('inviteToken')), 'A valid invitation is required.', 403)
        check(not member(g, user['id']), 'Already a member.', 409)
        check(len(g['members']) < g['capacity'], 'This circle is full.', 409)
        check(not any(a['userId'] == user['id'] and a['status'] == 'pending' for a in g['applications']), 'Request already pending.', 409)
        check(body.get('adult') is True and body.get('accepted') is True, 'Accept the agreement and confirm adult status.')
        check(sum(a['status'] == 'pending' for a in g['applications']) < 100, 'Pending request limit reached.')
        a = dict(id=uid(), userId=user['id'], name=user['name'], status='pending', at=stamp(at), acceptedAt=stamp(at), agreementVersion=1)
        g['applications'].append(a)
        result['applicationId'] = a['id']
        event(g, 'membership.requested', 'Membership requested', user['name'], user, at, user['id'])
    elif action == 'review':
        a = next((a for a in g['applications'] if a['id'] == body.get('applicationId') and a['status'] == 'pending'), None)
        check(a is not None, 'Request already reviewed.', 409)
        check(body.get('decision') in ['approve', 'reject'], 'Choose approve or decline.')
        if body['decision'] == 'approve':
            check(len(g['members']) < g['capacity'] and not member(g, a['userId']), 'Circle full or already a member.', 409)
            g['members'].append(dict(userId=a['userId'], name=a['name'], position=len(g['members']) + 1, acceptedAt=a['acceptedAt'], agreementVersion=1))
        a.update(status='approved' if body['decision'] == 'approve' else 'rejected', reviewedAt=stamp(at))
        event(g, 'membership.reviewed', 'Membership reviewed', a['name'] + ' · ' + a['status'], user, at, a['userId'])
    elif action == 'start':
        check(len(g['members']) == g['capacity'] and all(m.get('acceptedAt') for m in g['members']), 'Fill all membership spots before starting.', 409)
        g.update(status='active', startedAt=stamp(at))
        event(g, 'circle.started', 'Circle activated', 'Membership and payout order are locked.', user, at)
    elif action == 'pause':
        g.update(status='paused', pauseReason=text(body.get('reason'), 5, 180, 'Pause reason'))
        event(g, 'circle.paused', 'Circle paused', g['pauseReason'], user, at)
    elif action == 'resume':
        check(g['status'] == 'paused', 'Circle is not paused.', 409)
        g['status'] = 'active'
        event(g, 'circle.resumed', 'Circle resumed', 'Test contributions can continue.', user, at)
    elif action == 'checkout':
        r = number(body.get('round', g['round']), g['round'], g['capacity'] - 1, 'Contribution round')
        check(not any(p['userId'] == user['id'] for p in paid(g, r)), 'Your contribution for this round is already paid.', 409)
        attempts = [p for p in g['payments'] if p['userId'] == user['id'] and p['round'] == r]
        order = next((p for p in attempts if p['status'] == 'pending' and datetime.fromisoformat(p['expiresAt'].replace('Z', '+00:00')) > at), None)
        if not order:
            check(len(attempts) < 30, 'Payment attempt limit reached.')
            order = dict(id=uid(), reference='HCF-TEST-' + secrets.token_hex(5).upper(), userId=user['id'], name=user['name'], round=r, amount=g['amount'], currency='INR', status='pending', mode='test', method=None, createdAt=stamp(at), expiresAt=stamp(at + timedelta(minutes=15)))
            g['payments'].append(order)
        result['orderId'] = order['id']
    elif action in ['confirm', 'cancel']:
        order = next((p for p in g['payments'] if p['id'] == body.get('orderId')), None)
        check(order is not None, 'Payment order not found.', 404)
        check(order['userId'] == user['id'], 'This payment belongs to another member.', 403)
        result['orderId'] = order['id']
        if order['status'] == 'pending':
            if action == 'cancel':
                order['status'] = 'cancelled'
            elif datetime.fromisoformat(order['expiresAt'].replace('Z', '+00:00')) <= at:
                order['status'] = 'expired'
            else:
                check(g['status'] == 'active', 'Circle is not active or is paused.', 409)
                check(g['round'] <= order['round'] < g['capacity'], 'Checkout round has passed.', 409)
                check(not any(p['userId'] == user['id'] for p in paid(g, order['round'])), 'Contribution already paid.', 409)
                check(body.get('method') in ['test_card', 'test_upi'], 'Choose a test payment method.')
                check(body.get('scenario') in ['approved', 'declined'], 'Choose a test gateway response.')
                check(body.get('otp') == '123456', 'Use the displayed test OTP: 123456.')
                order.update(method=body['method'], status='succeeded' if body['scenario'] == 'approved' else 'failed', reason='Test gateway declined. No funds recorded.' if body['scenario'] == 'declined' else None)
            order['completedAt'] = stamp(at)
            event(g, 'payment.' + order['status'], 'Test payment ' + order['status'], f'{order["name"]} · Round {order["round"] + 1} · {order["reference"]}', user, at)
    elif action == 'payout':
        check(funded(g), 'Every member must contribute before a payout is released.', 409)
        check(body.get('scenario') in ['approved', 'declined'], 'Choose a test payout response.')
        check(sum(p['round'] == g['round'] for p in g['payouts']) < 30, 'Payout attempt limit reached.')
        recipient = next(m for m in g['members'] if m['position'] == g['round'] + 1)
        p = dict(id=uid(), reference='HCF-PAYOUT-' + secrets.token_hex(5).upper(), round=g['round'], userId=recipient['userId'], name=recipient['name'], amount=g['amount'] * g['capacity'], currency='INR', status='succeeded' if body['scenario'] == 'approved' else 'failed', mode='test', createdAt=stamp(at), completedAt=stamp(at))
        g['payouts'].append(p)
        result['payoutId'] = p['id']
        event(g, 'payout.' + p['status'], 'Test payout ' + p['status'], recipient['name'], user, at, recipient['userId'])
        if p['status'] == 'succeeded':
            g['round'] += 1
            if g['round'] == g['capacity']:
                g['status'] = 'completed'
                event(g, 'circle.completed', 'Circle completed', 'Every member received one equal test payout.', user, at)
    else:
        raise AppError('Unknown action.', 404)
    g['updatedAt'] = stamp(at)
    g['operations'].append(dict(id=operation, userId=user['id'], action=action, result=result))
    g['operations'] = g['operations'][-200:]
    return g, result
