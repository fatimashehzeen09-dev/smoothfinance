"""Flask API. All authority, membership and payment checks execute in Python."""
import hashlib
import os
import re
import secrets
import time
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, g
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import HTTPException
from .domain import AppError, check, command, member, new_circle, now, stamp, summary, text, uid, invite, view
from .storage import Store
from .demo import ACCOUNTS, PASSWORD, seed

ROOT = Path(__file__).resolve().parent.parent
COOKIE = 'hcf_python_session'


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


def public_user(user):
    return {k: user.get(k) for k in ['id', 'name', 'email', 'createdAt', 'notificationsReadAt', 'demo']}


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=14000, PRODUCTION=os.environ.get('HCF_PRODUCTION') == '1', DEMO_MODE=os.environ.get('HCF_DEMO_MODE', '1') == '1', PUBLIC_URL=os.environ.get('HCF_PUBLIC_URL', ''))
    if config:
        app.config.update(config)
    store = app.config.get('STORE') or Store()
    app.store = store
    seeded = False
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32))

    def circle(gid):
        check(re.fullmatch(r'[0-9a-fA-F-]{36}', gid) is not None, 'Invalid circle reference.')
        row = store.get('circle:' + gid)
        check(row is not None, 'Circle not found.', 404)
        return row['payload']

    def session_user():
        token = request.cookies.get(COOKIE, '')
        row = store.get('session:' + hashed(token)) if token else None
        if not row or row['payload'].get('expires', 0) <= time.time():
            return None
        profile = store.get(row['payload']['userKey'])
        return profile['payload'] if profile and profile['payload'].get('sessionVersion', 0) == row['payload'].get('sessionVersion', 0) else None

    def login(user):
        token = secrets.token_urlsafe(32)
        store.put('session:' + hashed(token), dict(userKey='user:' + user['email'], sessionVersion=user.get('sessionVersion', 0), expires=time.time() + 7 * 86400))
        g.new_session = token

    def rate(category, limit):
        # A versioned SQL row gives atomic limits across concurrent server instances.
        key = 'rate:' + hashed(category + ':' + (request.remote_addr or 'unknown'))
        def increment(value):
            if value.get('expires', 0) <= time.time():
                value = dict(attempts=0, expires=time.time() + 900)
            check(value['attempts'] < limit, 'Too many attempts. Try again in 15 minutes.', 429)
            value['attempts'] += 1
            return value
        store.update(key, increment, initial={})

    def auth(action, body):
        if action in ['signup', 'login', 'recover', 'reset']:
            rate('auth:' + action, 60 if action == 'login' else 20)
        if action == 'signup':
            name = text(body.get('name'), 2, 60, 'Name')
            email = body.get('email', '')
            check(isinstance(email, str), 'Enter a valid email.')
            email = email.strip().lower()
            check(len(email) <= 254 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email), 'Enter a valid email.')
            password = body.get('password')
            check(isinstance(password, str) and 10 <= len(password) <= 128, 'Use a password of 10–128 characters.')
            check(body.get('accepted') is True, 'Accept the account terms.')
            check(not email.endswith('@demo.halalchit.test'), 'This address is reserved for shared demo accounts.')
            user = dict(id=uid(), name=name, email=email, passwordHash=generate_password_hash(password), createdAt=stamp(), notificationsReadAt=None, demo=False)
            check(store.put('user:' + email, user), 'This email already has an account.', 409)
            login(user)
            return dict(requiresConfirmation=False, message='Your account is ready. This showcase does not verify email ownership.')
        if action == 'login':
            email, password = body.get('email'), body.get('password')
            check(isinstance(email, str) and isinstance(password, str) and len(email) <= 254 and len(password) <= 128, 'Enter your email and password.')
            row = store.get('user:' + email.strip().lower())
            user = row['payload'] if row else None
            valid = check_password_hash(user['passwordHash'] if user else dummy_hash, password)
            check(valid and user is not None, 'Invalid email or password.', 401)
            login(user)
            return dict(user=public_user(user))
        if action == 'logout':
            token = request.cookies.get(COOKIE, '')
            if token:
                key = 'session:' + hashed(token)
                if store.get(key):
                    store.update(key, lambda v: {**v, 'expires': 0})
            g.clear_session = True
            return dict(ok=True)
        if action == 'refresh':
            return dict(ok=True)
        if action == 'recover':
            check(not app.config['PRODUCTION'], 'Email recovery is not configured on this university showcase. Use a demo login or contact the project team.', 503)
            email = body.get('email', '')
            check(isinstance(email, str) and len(email) <= 254, 'Enter your email.')
            row = store.get('user:' + email.strip().lower())
            if row and not row['payload'].get('demo'):
                token = secrets.token_urlsafe(32)
                store.put('recovery:' + hashed(token), dict(userKey='user:' + email.strip().lower(), expires=time.time() + 1800))
                folder = ROOT / 'instance/outbox'
                folder.mkdir(parents=True, exist_ok=True)
                file = folder / (uid() + '.txt')
                file.write_text(request.host_url + '#recovery_token=' + token)
                file.chmod(0o600)
            return dict(message='If the account exists, a local reset link is saved in instance/outbox. Shared demo passwords cannot be reset.')
        if action == 'reset':
            token, password = text(body.get('token'), 8, 1500, 'Recovery token'), body.get('password')
            check(isinstance(password, str) and 10 <= len(password) <= 128, 'Use a password of 10–128 characters.')
            key = 'recovery:' + hashed(token)
            def consume(value):
                check(value.get('expires', 0) > time.time(), 'Reset link is invalid or expired.')
                return {**value, 'expires': 0}
            recovery = store.update(key, consume)
            user = store.update(recovery['userKey'], lambda v: {**v, 'passwordHash': generate_password_hash(password), 'sessionVersion': v.get('sessionVersion', 0) + 1})
            login(user)
            return dict(ok=True)
        raise AppError('Account action not found.', 404)

    @app.route('/api/<path:path>', methods=['GET', 'POST'])
    def api(path):
        nonlocal seeded
        if request.method == 'POST':
            # Vercel terminates HTTPS; use only its forwarded scheme and the request host.
            scheme = request.headers.get('x-forwarded-proto', request.scheme) if app.config['PRODUCTION'] else request.scheme
            expected = app.config['PUBLIC_URL'] or scheme + '://' + request.host
            check(request.headers.get('Origin') == expected and request.headers.get('Sec-Fetch-Site') != 'cross-site', 'Request origin is not allowed.', 403)
            check(request.is_json, 'Use JSON for requests.', 415)
            body = request.get_json(silent=True)
            check(isinstance(body, dict), 'Invalid request JSON.')
        else:
            body = {}
        if app.config['DEMO_MODE'] and not seeded:
            seed(store)
            seeded = True
        if path == 'health':
            store.get('meta:demo-v1')
            return jsonify(status='ready', backend='Python / Flask', database='PostgreSQL' if store.remote else 'SQLite', payments='test-only', demoAccounts=app.config['DEMO_MODE'])
        if path == 'demo-accounts' and request.method == 'GET':
            return jsonify(accounts=[dict(name=n, email=e, role='Organizer' if i == 0 else 'Member') for i, (n, e) in enumerate(ACCOUNTS)] if app.config['DEMO_MODE'] else [], password=PASSWORD if app.config['DEMO_MODE'] else None)
        if path.startswith('auth/'):
            check(request.method == 'POST', 'Method not allowed.', 405)
            data = auth(path[5:], body)
            response = jsonify(data)
            if getattr(g, 'new_session', None):
                response.set_cookie(COOKIE, g.new_session, httponly=True, secure=app.config['PRODUCTION'], samesite='Lax', max_age=7 * 86400, path='/')
            elif getattr(g, 'clear_session', False):
                response.delete_cookie(COOKIE, path='/', secure=app.config['PRODUCTION'], httponly=True, samesite='Lax')
            return response
        user = session_user()
        if path == 'me' and request.method == 'GET':
            return jsonify(user=public_user(user) if user else None)
        check(user is not None, 'Please sign in to continue.', 401)
        if path == 'home' and request.method == 'GET':
            circles = []
            for row in store.scan('circle:'):
                c = row['payload']
                apps = [a for a in c['applications'] if a['userId'] == user['id']]
                if member(c, user['id']) or apps:
                    try:
                        circles.append(view(c, user))
                    except AppError as error:
                        if error.status != 403:
                            raise
                        circles.append(dict(**summary(c), role='visitor', preview=True, members=[], applications=[], payments=[], payouts=[], events=[], myApplication=apps[-1]))
            return jsonify(user=public_user(user), circles=circles, serverTime=stamp())
        if path == 'explore' and request.method == 'GET':
            return jsonify(circles=[summary(r['payload']) for r in store.scan('circle:') if r['payload']['visibility'] == 'public' and r['payload']['status'] == 'forming' and not member(r['payload'], user['id'])])
        if path == 'circles' and request.method == 'POST':
            c = new_circle(user, body)
            prior = store.get('circle:' + c['id'])
            if not prior:
                check(sum(r['payload']['organizerId'] == user['id'] for r in store.scan('circle:')) < 50, 'Your account has reached the circle limit.')
                store.put('circle:' + c['id'], c)
            return jsonify(circle=view((store.get('circle:' + c['id']))['payload'], user)), 200 if prior else 201
        if path.startswith('invitations/') and request.method == 'GET':
            token = path[len('invitations/'):]
            c = circle(token.split('.')[0])
            check(invite(c, token), 'Invalid invitation.', 404)
            check(c['status'] == 'forming', 'Circle is no longer accepting members.', 409)
            return jsonify(circle=view(c, user, token), inviteToken=token)
        parts = path.split('/')
        if parts[0] == 'circles' and len(parts) >= 2:
            c = circle(parts[1])
            if request.method == 'GET' and len(parts) == 2:
                return jsonify(circle=view(c, user, request.args.get('invite')))
            if request.method == 'GET' and len(parts) == 4 and parts[2] in ['checkout', 'receipts']:
                if parts[2] == 'checkout':
                    check(member(c, user['id']), 'Only members can access checkout.', 403)
                    order = next((p for p in c['payments'] if p['id'] == parts[3]), None)
                    check(order is not None, 'Order not found.', 404)
                    check(order['userId'] == user['id'], 'This payment belongs to another member.', 403)
                    return jsonify(order=order, circle=view(c, user))
                payments = [dict(p, type='contribution') for p in c['payments']] + [dict(p, type='payout') for p in c['payouts']]
                p = next((p for p in payments if p['id'] == parts[3]), None)
                check(p is not None, 'Receipt not found.', 404)
                check(p['userId'] == user['id'] or c['organizerId'] == user['id'], 'You cannot access this receipt.', 403)
                check(p['status'] == 'succeeded', 'Only completed transactions have receipts.', 409)
                return jsonify(transaction=p, accountName=p['name'], circle={k: c[k] for k in ['id', 'name', 'organizerName']})
            if request.method == 'POST' and len(parts) == 3:
                result = {}
                def apply(value):
                    updated, output = command(value, user, parts[2], body)
                    result.clear()
                    result.update(output)
                    return updated
                c = store.update('circle:' + c['id'], apply)
                return jsonify(circle=view(c, user, body.get('inviteToken')), **result)
        if path == 'notifications/read' and request.method == 'POST':
            store.update('user:' + user['email'], lambda v: {**v, 'notificationsReadAt': stamp()})
            return jsonify(ok=True)
        if path == 'profile' and request.method == 'POST':
            check(not user.get('demo'), 'Shared demo account names are fixed. Create a personal test account to edit your profile.', 409)
            name = text(body.get('name'), 2, 60, 'Name')
            updated = store.update('user:' + user['email'], lambda v: {**v, 'name': name})
            return jsonify(user=public_user(updated))
        raise AppError('Not found.', 404)

    @app.errorhandler(AppError)
    def app_error(error):
        return jsonify(error=str(error)), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.error('Application request failed: %s', type(error).__name__)
        return jsonify(error='We could not complete this request. Try again shortly.'), 503

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'SAMEORIGIN', 'Referrer-Policy': 'strict-origin-when-cross-origin', 'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"})
        return response

    @app.get('/')
    def index():
        return send_from_directory(ROOT / 'frontend', 'index.html')

    @app.get('/assets/<path:name>')
    def assets(name):
        return send_from_directory(ROOT / 'frontend', name)

    @app.get('/favicon.svg')
    def favicon():
        return app.response_class('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#285a46"/><text x="32" y="40" text-anchor="middle" fill="white" font-size="21">HCF</text></svg>', mimetype='image/svg+xml')

    return app
