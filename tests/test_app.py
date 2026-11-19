"""Python integration tests: python -m unittest discover -s tests -v."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4
from backend.app import create_app, COOKIE
from backend.domain import due, command, now
from backend.storage import Store


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / 'test.sqlite'), remote=False)
        self.app = create_app({'TESTING': True, 'STORE': self.store, 'DEMO_MODE': False})
        self.clients = {n: self.app.test_client() for n in ['Alice', 'Bob', 'Carol', 'Visitor']}
        for name, client in self.clients.items():
            r = self.post(client, 'auth/signup', name=name, email=name.lower() + '@example.test', password='correct-password-123', accepted=True)
            self.assertEqual(r.status_code, 200)

    def tearDown(self):
        self.temp.cleanup()

    def post(self, client, path, **body):
        return client.post('/api/' + path, json={'operationId': str(uuid4()), **body}, headers={'Origin': 'http://localhost'})

    def create(self, visibility='private'):
        r = self.post(self.clients['Alice'], 'circles', name='Community Circle', description='Shared savings commitment.', amount=200000, capacity=3, startDate=(date.today() + timedelta(days=2)).isoformat(), visibility=visibility, accepted=True, adult=True)
        self.assertEqual(r.status_code, 201)
        return r.json['circle']

    def ready(self):
        g = self.create()
        for name in ['Bob', 'Carol']:
            r = self.post(self.clients[name], f'circles/{g["id"]}/join', accepted=True, adult=True, inviteToken=g['inviteLinkToken'])
            self.assertEqual(r.status_code, 200)
            r = self.post(self.clients['Alice'], f'circles/{g["id"]}/review', applicationId=r.json['applicationId'], decision='approve')
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.post(self.clients['Alice'], f'circles/{g["id"]}/start').status_code, 200)
        return g['id']

    def pay(self, name, gid, r=None, scenario='approved'):
        body = {} if r is None else {'round': r}
        response = self.post(self.clients[name], f'circles/{gid}/checkout', **body)
        self.assertEqual(response.status_code, 200)
        oid = response.json['orderId']
        response = self.post(self.clients[name], f'circles/{gid}/confirm', orderId=oid, method='test_upi', scenario=scenario, otp='123456')
        self.assertEqual(response.status_code, 200)
        return oid

    def test_authentication_hashes_csrf_and_logout(self):
        anonymous = self.app.test_client()
        self.assertIsNone(anonymous.get('/api/me').json['user'])
        self.assertEqual(self.post(anonymous, 'circles').status_code, 401)
        self.assertEqual(self.clients['Alice'].post('/api/auth/logout', json={}, headers={'Origin': 'https://attacker.test'}).status_code, 403)
        self.assertTrue(self.clients['Alice'].get_cookie(COOKIE).http_only)
        stored = self.store.get('user:alice@example.test')['payload']['passwordHash']
        self.assertTrue(stored.startswith('scrypt:'))
        self.assertNotIn('correct-password', stored)
        self.post(self.clients['Alice'], 'auth/logout')
        self.assertIsNone(self.clients['Alice'].get('/api/me').json['user'])
        self.assertEqual(self.post(self.clients['Alice'], 'auth/login', email='alice@example.test', password='wrong').status_code, 401)
        self.assertEqual(self.post(self.clients['Alice'], 'auth/login', email='alice@example.test', password='correct-password-123').status_code, 200)

    def test_private_invitations_public_discovery_and_approval_permissions(self):
        g = self.create()
        self.assertEqual(self.clients['Visitor'].get('/api/circles/' + g['id']).status_code, 403)
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{g["id"]}/join', adult=True, accepted=True).status_code, 403)
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{g["id"]}/start').status_code, 403)
        self.assertEqual(self.post(self.clients['Alice'], f'circles/{g["id"]}/start').status_code, 409)
        public = self.create('public')
        result = self.clients['Bob'].get('/api/explore').json['circles']
        self.assertEqual([g['id'] for g in result], [public['id']])
        self.assertNotIn('members', result[0])
        self.assertNotIn('inviteSecret', result[0])

    def test_server_amount_ownership_wrong_otp_decline_retry(self):
        gid = self.ready()
        oid = self.post(self.clients['Bob'], f'circles/{gid}/checkout', amount=1).json['orderId']
        self.assertEqual(self.clients['Bob'].get(f'/api/circles/{gid}/checkout/{oid}').json['order']['amount'], 200000)
        self.assertEqual(self.clients['Carol'].get(f'/api/circles/{gid}/checkout/{oid}').status_code, 403)
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{gid}/confirm', orderId=oid, otp='000000', method='test_card', scenario='approved').status_code, 400)
        self.post(self.clients['Bob'], f'circles/{gid}/confirm', orderId=oid, otp='123456', method='test_card', scenario='declined')
        next_oid = self.pay('Bob', gid)
        self.assertNotEqual(oid, next_oid)
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{gid}/checkout').status_code, 409)
        self.assertEqual(self.clients['Visitor'].get(f'/api/circles/{gid}/receipts/{next_oid}').status_code, 403)

    def test_full_rotation_prepayment_no_double_funding(self):
        gid = self.ready()
        receipt = self.pay('Bob', gid, 1)
        self.assertEqual(self.post(self.clients['Alice'], f'circles/{gid}/payout', scenario='approved').status_code, 409)
        for r in range(3):
            for name in ['Alice', 'Bob', 'Carol']:
                if name == 'Bob' and r == 1:
                    self.assertEqual(self.post(self.clients[name], f'circles/{gid}/checkout').status_code, 409)
                else:
                    self.pay(name, gid)
            if r == 0:
                self.post(self.clients['Alice'], f'circles/{gid}/payout', scenario='declined')
                self.assertEqual(self.clients['Alice'].get(f'/api/circles/{gid}').json['circle']['round'], 0)
            self.post(self.clients['Alice'], f'circles/{gid}/payout', scenario='approved')
        g = self.clients['Alice'].get(f'/api/circles/{gid}').json['circle']
        self.assertEqual(g['status'], 'completed')
        payouts = [p for p in g['payouts'] if p['status'] == 'succeeded']
        self.assertEqual(len({p['userId'] for p in payouts}), 3)
        self.assertEqual(sum(p['amount'] for p in payouts), sum(p['amount'] for p in g['payments']))
        self.assertEqual(self.clients['Bob'].get(f'/api/circles/{gid}/receipts/{receipt}').json['transaction']['round'], 1)

    def test_concurrent_confirmations(self):
        gid = self.ready()
        oid = self.post(self.clients['Bob'], f'circles/{gid}/checkout').json['orderId']
        cookie = self.clients['Bob'].get_cookie(COOKIE).value
        def pay(_):
            client = self.app.test_client()
            client.set_cookie(COOKIE, cookie)
            return self.post(client, f'circles/{gid}/confirm', orderId=oid, otp='123456', method='test_card', scenario='approved').status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(pay, [1, 2])), [200, 200])
        g = self.clients['Alice'].get(f'/api/circles/{gid}').json['circle']
        self.assertEqual(len([p for p in g['payments'] if p['status'] == 'succeeded']), 1)

    def test_delete_preserves_receipts_cancels_pending_and_closes_invites(self):
        gid = self.ready()
        oid = self.pay('Bob', gid, 2)
        pending = self.post(self.clients['Carol'], f'circles/{gid}/checkout').json['orderId']
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{gid}/delete', confirmName='Community Circle').status_code, 403)
        self.assertEqual(self.post(self.clients['Alice'], f'circles/{gid}/delete', confirmName='Wrong').status_code, 400)
        self.assertEqual(self.post(self.clients['Alice'], f'circles/{gid}/delete', confirmName='Community Circle').status_code, 200)
        self.assertEqual(self.clients['Bob'].get(f'/api/circles/{gid}/receipts/{oid}').status_code, 200)
        self.assertEqual(self.clients['Carol'].get(f'/api/circles/{gid}/checkout/{pending}').json['order']['status'], 'cancelled')
        self.assertEqual(self.post(self.clients['Alice'], f'circles/{gid}/resume').status_code, 409)

    def test_pause_expiry_cancel_round_validation_and_replay(self):
        gid = self.ready()
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{gid}/checkout', round=True).status_code, 400)
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{gid}/checkout', round=3).status_code, 400)
        op = str(uuid4())
        oid = self.post(self.clients['Bob'], f'circles/{gid}/checkout', operationId=op).json['orderId']
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{gid}/checkout', operationId=op).json['orderId'], oid)
        user = self.store.get('user:bob@example.test')['payload']
        g = self.store.get('circle:' + gid)['payload']
        expired, _ = command(g, user, 'confirm', dict(operationId=str(uuid4()), orderId=oid, method='test_card', scenario='approved', otp='123456'), now() + timedelta(minutes=16))
        self.assertEqual(expired['payments'][0]['status'], 'expired')
        self.post(self.clients['Bob'], f'circles/{gid}/cancel', orderId=oid)
        self.assertEqual(self.clients['Bob'].get(f'/api/circles/{gid}/checkout/{oid}').json['order']['status'], 'cancelled')
        self.post(self.clients['Alice'], f'circles/{gid}/pause', reason='Waiting for a member.')
        self.assertEqual(self.post(self.clients['Bob'], f'circles/{gid}/checkout').status_code, 409)

    def test_demo_accounts_seed_once_and_login(self):
        app = create_app({'TESTING': True, 'STORE': self.store, 'DEMO_MODE': True})
        client = app.test_client()
        demo = client.get('/api/demo-accounts').json
        self.assertEqual(len(demo['accounts']), 4)
        for account in demo['accounts']:
            self.assertEqual(self.post(client, 'auth/login', email=account['email'], password=demo['password']).status_code, 200)
            self.assertEqual(client.get('/api/home').json['circles'][0]['memberCount'], 4)
        self.assertEqual(self.post(client, 'profile', name='Someone Else').status_code, 409)
        restart = create_app({'STORE': self.store, 'DEMO_MODE': True}).test_client()
        restart.get('/api/demo-accounts')
        self.assertEqual(len(self.store.scan('circle:')), 1)

    def test_restart_persistence_profile_and_frontend(self):
        circle = self.create()
        self.assertEqual(self.post(self.clients['Alice'], 'profile', name='Updated Organizer').status_code, 200)
        restarted = create_app({'STORE': self.store, 'DEMO_MODE': False}).test_client()
        restarted.set_cookie(COOKIE, self.clients['Alice'].get_cookie(COOKIE).value)
        self.assertEqual(restarted.get('/api/home').json['circles'][0]['id'], circle['id'])
        self.assertEqual(restarted.get('/api/me').json['user']['name'], 'Updated Organizer')
        with restarted.get('/assets/app.js') as response:
            js = response.get_data(as_text=True)
        for expected in ['Shehzeen Fatima', 'Prepay a round', 'backgroundRefresh', 'demoLogins']:
            self.assertIn(expected, js)
        self.assertEqual(restarted.get('/api/health').json['backend'], 'Python / Flask')
        self.assertEqual(due('2028-01-31', 1), '2028-02-29')

    def test_signup_duplicate_and_body_validation(self):
        self.assertEqual(self.post(self.app.test_client(), 'auth/signup', name='Alice', email='alice@example.test', password='password-123', accepted=True).status_code, 409)
        self.assertEqual(self.clients['Alice'].post('/api/circles', json=[], headers={'Origin': 'http://localhost'}).status_code, 400)
        self.assertEqual(self.clients['Alice'].post('/api/circles', data='x'*15000, content_type='application/json', headers={'Origin': 'http://localhost'}).status_code, 413)
        self.assertEqual(self.clients['Alice'].get('/api/auth/login').status_code, 405)


if __name__ == '__main__':
    unittest.main()
