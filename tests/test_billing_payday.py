from decimal import Decimal, ROUND_UP
from datetime import datetime, timedelta

import balanced
import mock
from nose.tools import assert_equals, assert_raises
from psycopg2 import IntegrityError

from aspen.utils import typecheck, utcnow
from gittip import billing, wireup
from gittip.billing.payday import FEE_CHARGE, Payday
from gittip.models import Payday as PaydayModel
from gittip.participant import Participant
from gittip.testing import Harness

from test_billing import TestBillingBase


class TestPaydayCharge(TestBillingBase):
    STRIPE_CUSTOMER_ID = 'cus_deadbeef'

    def setUp(self):
        super(TestBillingBase, self).setUp()
        self.postgres = wireup.db()
        self.payday = Payday(self.postgres)

    def get_numbers(self):
        """Return a list of 9 ints:

            nachs
            nach_failing
            ncc_failing
            ncc_missing
            ncharges
            nparticipants
            ntippers
            ntips
            ntransfers

        """
        payday = PaydayModel.query.first()  # There should be a single payday
        attrs = payday.attrs_dict()
        keys = [key for key in sorted(attrs) if key.startswith('n')]
        return [attrs[key] for key in keys]

    def test_charge_without_cc_details_returns_None(self):
        self.make_participant('alice')
        participant = self.postgres.fetchone("SELECT * FROM participants")
        self.payday.start()
        actual = self.payday.charge(participant, Decimal('1.00'))
        assert actual is None, actual

    def test_charge_without_cc_marked_as_failure(self):
        self.make_participant('alice')
        participant = self.postgres.fetchone("SELECT * FROM participants")
        self.payday.start()
        self.payday.charge(participant, Decimal('1.00'))
        actual = self.get_numbers()
        assert_equals(actual, [0, 0, 0, 1, 0, 0, 0, 0, 0])

    @mock.patch('gittip.billing.payday.Payday.charge_on_balanced')
    def test_charge_failure_returns_None(self, cob):
        cob.return_value = (Decimal('10.00'), Decimal('0.68'), 'FAILED')
        self.make_participant('bob', last_bill_result="failure",
                              balanced_account_uri=self.balanced_account_uri,
                              stripe_customer_id=self.STRIPE_CUSTOMER_ID,
                              is_suspicious=False)

        participant = self.postgres.fetchone("SELECT * FROM participants")
        self.payday.start()
        actual = self.payday.charge(participant, Decimal('1.00'))
        assert actual is None, actual

    @mock.patch('gittip.billing.payday.Payday.charge_on_balanced')
    def test_charge_success_returns_None(self, charge_on_balanced):
        charge_on_balanced.return_value = (Decimal('10.00'), Decimal('0.68'), "")
        self.make_participant('bob', last_bill_result="failure",
                              balanced_account_uri=self.balanced_account_uri,
                              stripe_customer_id=self.STRIPE_CUSTOMER_ID,
                              is_suspicious=False)

        participant = self.postgres.fetchone("SELECT * FROM participants")
        self.payday.start()
        actual = self.payday.charge(participant, Decimal('1.00'))
        assert actual is None, actual

    @mock.patch('gittip.billing.payday.Payday.charge_on_balanced')
    def test_charge_success_updates_participant(self, cob):
        cob.return_value = (Decimal('10.00'), Decimal('0.68'), "")
        bob = self.make_participant('bob', last_bill_result="failure",
                                    balanced_account_uri=self.balanced_account_uri,
                                    is_suspicious=False)
        participant = self.postgres.fetchone("SELECT * FROM participants")
        self.payday.start()
        self.payday.charge(participant, Decimal('1.00'))
        expected = {'balance': Decimal('9.32'), 'last_bill_result': ''}
        actual = {'balance': bob.balance,
                  'last_bill_result': bob.last_bill_result}
        assert_equals(actual, expected)

    @mock.patch('gittip.billing.payday.Payday.charge_on_balanced')
    def test_payday_moves_money(self, charge_on_balanced):
        charge_on_balanced.return_value = (Decimal('10.00'), Decimal('0.68'), "")
        day_ago = utcnow() - timedelta(days=1)
        bob = self.make_participant('bob', claimed_time=day_ago,
                                    last_bill_result='',
                                    is_suspicious=False)
        carl = self.make_participant('carl', claimed_time=day_ago,
                                     balanced_account_uri=self.balanced_account_uri,
                                     last_bill_result='',
                                     is_suspicious=False)
        Participant(u'carl').set_tip_to('bob', '6.00')  # under $10!
        self.payday.run()
        assert_equals(bob.balance, Decimal('6.00'))
        assert_equals(carl.balance, Decimal('3.32'))

    @mock.patch('gittip.billing.payday.Payday.charge_on_balanced')
    def test_payday_doesnt_move_money_from_a_suspicious_account(self, charge_on_balanced):
        charge_on_balanced.return_value = (Decimal('10.00'), Decimal('0.68'), "")
        day_ago = utcnow() - timedelta(days=1)
        bob = self.make_participant('bob', claimed_time=day_ago,
                                    last_bill_result='',
                                    is_suspicious=False)
        carl = self.make_participant('carl', claimed_time=day_ago,
                                     balanced_account_uri=self.balanced_account_uri,
                                     last_bill_result='',
                                     is_suspicious=True)
        Participant(u'carl').set_tip_to('bob', '6.00')  # under $10!
        self.payday.run()
        assert_equals(bob.balance, Decimal('0.00'))
        assert_equals(carl.balance, Decimal('0.00'))

    @mock.patch('gittip.billing.payday.Payday.charge_on_balanced')
    def test_payday_doesnt_move_money_to_a_suspicious_account(self, charge_on_balanced):
        charge_on_balanced.return_value = (Decimal('10.00'), Decimal('0.68'), "")
        day_ago = utcnow() - timedelta(days=1)
        bob = self.make_participant('bob', claimed_time=day_ago,
                                    last_bill_result='',
                                    is_suspicious=True)
        carl = self.make_participant('carl', claimed_time=day_ago,
                                     balanced_account_uri=self.balanced_account_uri,
                                     last_bill_result='',
                                     is_suspicious=False)
        Participant(u'carl').set_tip_to('bob', '6.00')  # under $10!
        self.payday.run()
        assert_equals(bob.balance, Decimal('0.00'))
        assert_equals(carl.balance, Decimal('0.00'))


class TestBillingCharges(Harness):
    BALANCED_ACCOUNT_URI = u'/v1/marketplaces/M123/accounts/A123'
    BALANCED_TOKEN = u'/v1/marketplaces/M123/accounts/A123/cards/C123'
    STRIPE_CUSTOMER_ID = u'cus_deadbeef'

    def setUp(self):
        super(TestBillingCharges, self).setUp()
        self.postgres = wireup.db()
        self.payday = Payday(self.postgres)
        self.alice = self.make_participant('alice')

    def test_mark_missing_funding(self):
        self.payday.start()
        before = PaydayModel.query.first().attrs_dict()
        missing_count = before['ncc_missing']

        self.payday.mark_missing_funding()

        after = PaydayModel.query.first().attrs_dict()
        self.assertEqual(after['ncc_missing'], missing_count + 1)

    def test_mark_charge_failed(self):
        self.payday.start()
        before = PaydayModel.query.first().attrs_dict()
        fail_count = before['ncc_failing']

        with self.postgres.get_connection() as conn:
            cur = conn.cursor()
            self.payday.mark_charge_failed(cur)
            conn.commit()

        after = PaydayModel.query.first().attrs_dict()
        self.assertEqual(after['ncc_failing'], fail_count + 1)

    def test_mark_charge_success(self):
        self.payday.start()
        charge_amount, fee = 4, 2

        with self.postgres.get_connection() as conn:
            cur = conn.cursor()
            self.payday.mark_charge_success(cur, charge_amount, fee)
            conn.commit()

        # verify paydays
        actual = PaydayModel.query.first().attrs_dict()
        self.assertEqual(actual['ncharges'], 1)

    @mock.patch('stripe.Charge')
    def test_charge_on_stripe(self, ba):
        amount_to_charge = Decimal('10.00')  # $10.00 USD
        expected_fee = (amount_to_charge + FEE_CHARGE[0]) * FEE_CHARGE[1]
        expected_fee = (amount_to_charge - expected_fee.quantize(
            FEE_CHARGE[0], rounding=ROUND_UP)) * -1
        charge_amount, fee, msg = self.payday.charge_on_stripe(
            self.alice.id, self.STRIPE_CUSTOMER_ID, amount_to_charge)

        assert_equals(charge_amount, amount_to_charge + fee)
        assert_equals(fee, expected_fee)
        self.assertTrue(ba.find.called_with(self.STRIPE_CUSTOMER_ID))
        customer = ba.find.return_value
        self.assertTrue(
            customer.debit.called_with(int(charge_amount * 100), self.alice.id)
        )

    @mock.patch('balanced.Account')
    def test_charge_on_balanced(self, ba):
        amount_to_charge = Decimal('10.00')  # $10.00 USD
        expected_fee = (amount_to_charge + FEE_CHARGE[0]) * FEE_CHARGE[1]
        expected_fee = (amount_to_charge - expected_fee.quantize(
            FEE_CHARGE[0], rounding=ROUND_UP)) * -1
        charge_amount, fee, msg = self.payday.charge_on_balanced(
            self.alice.id, self.BALANCED_ACCOUNT_URI, amount_to_charge)
        self.assertEqual(charge_amount, amount_to_charge + fee)
        self.assertEqual(fee, expected_fee)
        self.assertTrue(ba.find.called_with(self.BALANCED_ACCOUNT_URI))
        customer = ba.find.return_value
        self.assertTrue(
            customer.debit.called_with(int(charge_amount * 100), self.alice.id)
        )

    @mock.patch('balanced.Account')
    def test_charge_on_balanced_small_amount(self, ba):
        amount_to_charge = Decimal('0.06')  # $0.06 USD
        expected_fee = Decimal('0.68')
        expected_amount = Decimal('10.00')
        charge_amount, fee, msg = \
            self.payday.charge_on_balanced(self.alice.id,
                                           self.BALANCED_ACCOUNT_URI,
                                           amount_to_charge)
        assert_equals(charge_amount, expected_amount)
        assert_equals(fee, expected_fee)
        customer = ba.find.return_value
        self.assertTrue(
            customer.debit.called_with(int(charge_amount * 100), self.alice.id)
        )

    @mock.patch('balanced.Account')
    def test_charge_on_balanced_failure(self, ba):
        amount_to_charge = Decimal('0.06')  # $0.06 USD
        error_message = 'Woah, crazy'
        ba.find.side_effect = balanced.exc.HTTPError(error_message)
        charge_amount, fee, msg = self.payday.charge_on_balanced(
            self.alice.id, self.BALANCED_ACCOUNT_URI, amount_to_charge)
        assert_equals(msg, error_message)


class TestPrepHit(Harness):
    # XXX Consider turning _prep_hit in to a class method
    @classmethod
    def setUpClass(cls):
        Harness.setUpClass()
        cls.payday = Payday(mock.Mock())  # Mock out the DB connection

    def prep(self, amount):
        """Given a dollar amount as a string, return a 3-tuple.

        The return tuple is like the one returned from _prep_hit, but with the
        second value, a log message, removed.

        """
        typecheck(amount, unicode)
        out = list(self.payday._prep_hit(Decimal(amount)))
        out = [out[0]] + out[2:]
        return tuple(out)

    def test_prep_hit_basically_works(self):
        actual = self.payday._prep_hit(Decimal('20.00'))
        expected = (2110,
                    u'Charging %s 2110 cents ($20.00 + $1.10 fee = $21.10) on %s ' u'... ',
                    Decimal('21.10'), Decimal('1.10'))
        assert actual == expected, actual

    def test_prep_hit_full_in_rounded_case(self):
        actual = self.payday._prep_hit(Decimal('5.00'))
        expected = (1000,
                    u'Charging %s 1000 cents ($9.32 [rounded up from $5.00] + ' u'$0.68 fee = $10.00) on %s ... ',
                    Decimal('10.00'), Decimal('0.68'))
        assert actual == expected, actual

    def test_prep_hit_at_ten_dollars(self):
        actual = self.prep(u'10.00')
        expected = (1071, Decimal('10.71'), Decimal('0.71'))
        assert actual == expected, actual

    def test_prep_hit_at_forty_cents(self):
        actual = self.prep(u'0.40')
        expected = (1000, Decimal('10.00'), Decimal('0.68'))
        assert actual == expected, actual

    def test_prep_hit_at_fifty_cents(self):
        actual = self.prep(u'0.50')
        expected = (1000, Decimal('10.00'), Decimal('0.68'))
        assert actual == expected, actual

    def test_prep_hit_at_sixty_cents(self):
        actual = self.prep(u'0.60')
        expected = (1000, Decimal('10.00'), Decimal('0.68'))
        assert actual == expected, actual

    def test_prep_hit_at_eighty_cents(self):
        actual = self.prep(u'0.80')
        expected = (1000, Decimal('10.00'), Decimal('0.68'))
        assert actual == expected, actual

    def test_prep_hit_at_nine_fifteen(self):
        actual = self.prep(u'9.15')
        expected = (1000, Decimal('10.00'), Decimal('0.68'))
        assert actual == expected, actual

    def test_prep_hit_at_nine_thirty_one(self):
        actual = self.prep(u'9.31')
        expected = (1000, Decimal('10.00'), Decimal('0.68'))
        assert actual == expected, actual

    def test_prep_hit_at_nine_thirty_two(self):
        actual = self.prep(u'9.32')
        expected = (1000, Decimal('10.00'), Decimal('0.68'))
        assert actual == expected, actual

    def test_prep_hit_at_nine_thirty_three(self):
        actual = self.prep(u'9.33')
        expected = (1001, Decimal('10.01'), Decimal('0.68'))
        assert actual == expected, actual


class TestBillingPayday(Harness):
    BALANCED_ACCOUNT_URI = '/v1/marketplaces/M123/accounts/A123'

    def setUp(self):
        super(TestBillingPayday, self).setUp()
        self.postgres = wireup.db()
        self.payday = Payday(self.postgres)

    def test_assert_one_payday(self):
        with assert_raises(AssertionError):
            self.payday.assert_one_payday(None)
        with assert_raises(AssertionError):
            self.payday.assert_one_payday([1, 2])

    @mock.patch('gittip.participant.Participant.get_tips_and_total')
    def test_charge_and_or_transfer_no_tips(self, get_tips_and_total):
        alice = self.make_participant('alice', balance='1.00',
                                      balanced_account_uri=self.BALANCED_ACCOUNT_URI,
                                      is_suspicious=False)
        amount = Decimal('1.00')

        ts_start = self.payday.start()

        tips, total = [], amount

        initial_payday = PaydayModel.query.first().attrs_dict()
        self.payday.charge_and_or_transfer(ts_start, alice.attrs_dict(),
                                           tips, total)
        resulting_payday = PaydayModel.query.first().attrs_dict()

        assert_equals(initial_payday['ntippers'], resulting_payday['ntippers'])
        assert_equals(initial_payday['ntips'], resulting_payday['ntips'])
        assert_equals(initial_payday['nparticipants'] + 1,
                      resulting_payday['nparticipants'])

    @mock.patch('gittip.participant.Participant.get_tips_and_total')
    @mock.patch('gittip.billing.payday.Payday.tip')
    def test_charge_and_or_transfer(self, tip, get_tips_and_total):
        alice = self.make_participant('alice', balance='1.00',
                                      balanced_account_uri=self.BALANCED_ACCOUNT_URI,
                                      is_suspicious=False)
        ts_start = self.payday.start()
        now = datetime.utcnow()
        amount = Decimal('1.00')
        like_a_tip = {'amount': amount, 'tippee': 'mjallday', 'ctime': now,
                      'claimed_time': now}

        # success, success, claimed, failure
        tips = [like_a_tip, like_a_tip, like_a_tip, like_a_tip]
        total = amount

        ts_start = datetime.utcnow()

        return_values = [1, 1, 0, -1]
        return_values.reverse()

        def tip_return_values(*_):
            return return_values.pop()

        tip.side_effect = tip_return_values

        initial_payday = PaydayModel.query.first().attrs_dict()
        self.payday.charge_and_or_transfer(ts_start, alice.attrs_dict(), tips,
                                           total)
        resulting_payday = PaydayModel.query.first().attrs_dict()

        assert_equals(initial_payday['ntippers'] + 1,
                      resulting_payday['ntippers'])
        assert_equals(initial_payday['ntips'] + 2,
                      resulting_payday['ntips'])
        assert_equals(initial_payday['nparticipants'] + 1,
                      resulting_payday['nparticipants'])

    @mock.patch('gittip.participant.Participant.get_tips_and_total')
    @mock.patch('gittip.billing.payday.Payday.charge')
    def test_charge_and_or_transfer_short(self, charge, get_tips_and_total):
        alice = self.make_participant('alice', balance='1.00',
                                      balanced_account_uri=self.BALANCED_ACCOUNT_URI,
                                      is_suspicious=False)
        now = datetime.utcnow()
        amount = Decimal('1.00')
        like_a_tip = {'amount': amount, 'tippee': 'mjallday', 'ctime': now,
                      'claimed_time': now}

        # success, success, claimed, failure
        tips = [like_a_tip, like_a_tip, like_a_tip, like_a_tip]
        get_tips_and_total.return_value = tips, amount

        ts_start = datetime.utcnow()

        # In real-life we wouldn't be able to catch an error as the charge
        # method will swallow any errors and return false. We don't handle this
        # return value within charge_and_or_transfer but instead continue on
        # trying to use the remaining credit in the user's account to payout as
        # many tips as possible.
        #
        # Here we're hacking the system and throwing the exception so execution
        # stops since we're only testing this part of the method. That smells
        # like we need to refactor.

        charge.side_effect = Exception()
        with self.assertRaises(Exception):
            billing.charge_and_or_transfer(ts_start, alice.attrs_dict())
        self.assertTrue(charge.called_with(alice.id,
                                           self.BALANCED_ACCOUNT_URI,
                                           amount))

    @mock.patch('gittip.billing.payday.Payday.transfer')
    @mock.patch('gittip.billing.payday.log')
    def test_tip(self, log, transfer):
        alice = self.make_participant('alice', balance='1.00',
                                      balanced_account_uri=self.BALANCED_ACCOUNT_URI,
                                      is_suspicious=False)
        participant = alice.attrs_dict()
        amount = Decimal('1.00')
        invalid_amount = Decimal('0.00')
        tip = {'amount': amount, 'tippee': alice.id, 'claimed_time': utcnow()}
        ts_start = utcnow()

        result = self.payday.tip(participant, tip, ts_start)
        assert_equals(result, 1)
        result = transfer.called_with(participant['id'], tip['tippee'],
                                      tip['amount'])
        self.assertTrue(result)

        self.assertTrue(log.called_with('SUCCESS: $1 from mjallday to alice.'))

        # XXX: Should these tests be broken down to a separate class with the
        # common setup factored in to a setUp method.

        # XXX: We should have constants to compare the values to
        # invalid amount
        tip['amount'] = invalid_amount
        result = self.payday.tip(participant, tip, ts_start)
        assert_equals(result, 0)

        tip['amount'] = amount

        # XXX: We should have constants to compare the values to
        # not claimed
        tip['claimed_time'] = None
        result = self.payday.tip(participant, tip, ts_start)
        assert_equals(result, 0)

        # XXX: We should have constants to compare the values to
        # claimed after payday
        tip['claimed_time'] = utcnow()
        result = self.payday.tip(participant, tip, ts_start)
        assert_equals(result, 0)

        ts_start = utcnow()

        # XXX: We should have constants to compare the values to
        # transfer failed
        transfer.return_value = False
        result = self.payday.tip(participant, tip, ts_start)
        assert_equals(result, -1)

    @mock.patch('gittip.billing.payday.log')
    def test_start_zero_out_and_get_participants(self, log):
        self.make_participant('alice', balance=0, claimed_time=None,
                              pending=None,
                              balanced_account_uri=self.BALANCED_ACCOUNT_URI)
        self.make_participant('bob', balance=10, claimed_time=None,
                              pending=1,
                              balanced_account_uri=self.BALANCED_ACCOUNT_URI)
        self.make_participant('carl', balance=10, claimed_time=utcnow(),
                              pending=1,
                              balanced_account_uri=self.BALANCED_ACCOUNT_URI)

        ts_start = self.payday.start()

        self.payday.zero_out_pending(ts_start)
        participants = self.payday.get_participants(ts_start)

        expected_logging_call_args = [
            ('Starting a new payday.'),
            ('Payday started at {}.'.format(ts_start)),
            ('Zeroed out the pending column.'),
            ('Fetched participants.'),
        ]
        expected_logging_call_args.reverse()
        for args, _ in log.call_args_list:
            assert_equals(args[0], expected_logging_call_args.pop())

        log.reset_mock()

        # run a second time, we should see it pick up the existing payday
        second_ts_start = self.payday.start()
        self.payday.zero_out_pending(second_ts_start)
        second_participants = self.payday.get_participants(second_ts_start)

        self.assertEqual(ts_start, second_ts_start)
        participants = list(participants)
        second_participants = list(second_participants)

        # carl is the only valid participant as he has a claimed time
        assert_equals(len(participants), 1)
        assert_equals(participants, second_participants)

        expected_logging_call_args = [
            ('Picking up with an existing payday.'),
            ('Payday started at {}.'.format(second_ts_start)),
            ('Zeroed out the pending column.'),
            ('Fetched participants.')]
        expected_logging_call_args.reverse()
        for args, _ in log.call_args_list:
            assert_equals(args[0], expected_logging_call_args.pop())

    @mock.patch('gittip.billing.payday.log')
    def test_end(self, log):
        self.payday.start()
        self.payday.end()
        self.assertTrue(log.called_with('Finished payday.'))

        # finishing the payday will set the ts_end date on this payday record
        # to now, so this will not return any result
        result = PaydayModel.query.filter(PaydayModel.ts_end > '1970-01-01')\
                                  .count()
        assert_equals(result, 1)

    @mock.patch('gittip.billing.payday.log')
    @mock.patch('gittip.billing.payday.Payday.start')
    @mock.patch('gittip.billing.payday.Payday.payin')
    @mock.patch('gittip.billing.payday.Payday.end')
    def test_payday(self, end, payin, init, log):
        ts_start = utcnow()
        init.return_value = (ts_start,)
        greeting = 'Greetings, program! It\'s PAYDAY!!!!'

        self.payday.run()

        self.assertTrue(log.called_with(greeting))
        self.assertTrue(init.call_count)
        self.assertTrue(payin.called_with(init.return_value))
        self.assertTrue(end.call_count)


class TestBillingTransfer(Harness):
    def setUp(self):
        super(Harness, self).setUp()
        self.postgres = wireup.db()
        self.payday = Payday(self.postgres)
        self.payday.start()
        self.tipper = self.make_participant('lgtest')
        self.balanced_account_uri = '/v1/marketplaces/M123/accounts/A123'

    def test_transfer(self):
        amount = Decimal('1.00')
        sender = self.make_participant('test_transfer_sender', pending=0,
                                       balance=1)
        recipient = self.make_participant('test_transfer_recipient', pending=0,
                                          balance=1)

        result = self.payday.transfer(sender.id, recipient.id, amount)
        assert_equals(result, True)

        # no balance remaining for a second transfer
        result = self.payday.transfer(sender.id, recipient.id, amount)
        assert_equals(result, False)

    def test_debit_participant(self):
        amount = Decimal('1.00')
        subject = self.make_participant('test_debit_participant', pending=0,
                                        balance=1)

        initial_amount = subject.balance

        with self.postgres.get_connection() as connection:
            cursor = connection.cursor()

            self.payday.debit_participant(cursor, subject.id, amount)
            connection.commit()

        self.session.refresh(subject)

        expected = initial_amount - amount
        actual = subject.balance
        assert_equals(actual, expected)

        # this will fail because not enough balance
        with self.postgres.get_connection() as conn:
            cur = conn.cursor()

            with self.assertRaises(IntegrityError):
                self.payday.debit_participant(cur, subject.id, amount)
            conn.commit()

    def test_credit_participant(self):
        amount = Decimal('1.00')
        subject = self.make_participant('test_credit_participant', pending=0,
                                        balance=1)

        initial_amount = subject.pending

        with self.postgres.get_connection() as conn:
            cur = conn.cursor()
            self.payday.credit_participant(cur, subject.id, amount)
            conn.commit()

        self.session.refresh(subject)

        expected = initial_amount + amount
        actual = subject.pending
        assert_equals(actual, expected)

    def test_record_transfer(self):
        from gittip.models import Transfer
        amount = Decimal('1.00')
        subjects = ['jim', 'kate', 'bob']

        for subject in subjects:
            self.make_participant(subject, balance=1, pending=0)

        with self.postgres.get_connection() as conn:
            cur = conn.cursor()

            # Tip 'jim' twice
            for recipient in ['jim'] + subjects:
                self.payday.record_transfer(cur, self.tipper.id, recipient,
                                            amount)
            conn.commit()

        for subject in subjects:
            # 'jim' is tipped twice
            expected = amount * 2 if subject == 'jim' else amount
            transfers = Transfer.query.filter_by(tippee=subject).all()
            actual = sum(tip.amount for tip in transfers)
            assert_equals(actual, expected)

    def test_record_transfer_invalid_participant(self):
        amount = Decimal('1.00')

        with self.postgres.get_connection() as conn:
            cur = conn.cursor()
            with assert_raises(IntegrityError):
                self.payday.record_transfer(cur, 'idontexist', 'nori', amount)
            conn.commit()

    def test_mark_transfer(self):
        amount = Decimal('1.00')

        # Forces a load with current state in dict
        before_transfer = PaydayModel.query.first().attrs_dict()

        with self.postgres.get_connection() as conn:
            cur = conn.cursor()
            self.payday.mark_transfer(cur, amount)
            conn.commit()

        # Forces a load with current state in dict
        after_transfer = PaydayModel.query.first().attrs_dict()

        expected = before_transfer['ntransfers'] + 1
        actual = after_transfer['ntransfers']
        assert_equals(actual, expected)

        expected = before_transfer['transfer_volume'] + amount
        actual = after_transfer['transfer_volume']
        assert_equals(actual, expected)
