import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.scheduling import DEFAULT_PROFILE, compatible, event_interval, propose, valid_slot
from app.interpretation import interpret, correct, Clarification

TZ = ZoneInfo('Asia/Jerusalem')
NOW = datetime(2026, 10, 26, 8, tzinfo=TZ)


def event(start, end, **extra):
    return dict(id='fixed', start={'dateTime': start.isoformat()}, end={'dateTime': end.isoformat()}, **extra)


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.profile = dict(DEFAULT_PROFILE)
        self.a = {'title': 'Call brother', 'duration': 30, 'category': 'personal'}
        self.start = NOW.replace(hour=10)

    def test_basic_proposal(self):
        p = propose(self.a, [], self.profile, NOW)
        self.assertEqual(datetime.fromisoformat(p['start']).hour, 10)

    def test_conflict_and_buffer(self):
        events = [event(self.start, self.start + timedelta(hours=1))]
        for minute in [0, 15, 30, 45]:
            self.assertFalse(valid_slot(self.start.replace(minute=minute), self.a, events, self.profile, NOW))
        self.assertFalse(valid_slot(self.start.replace(hour=11), self.a, events, self.profile, NOW))
        self.assertTrue(valid_slot(self.start.replace(hour=11, minute=15), self.a, events, self.profile, NOW))

    def test_all_day_event(self):
        e = {'id': 'holiday', 'start': {'date': '2026-10-26'}, 'end': {'date': '2026-10-27'}}
        self.a['date'] = '2026-10-26'
        self.assertIsNone(propose(self.a, [e], self.profile, NOW))

    def test_external_offsets(self):
        e = {'id': 'utc', 'start': {'dateTime': '2026-10-26T08:00:00Z'}, 'end': {'dateTime': '2026-10-26T09:00:00Z'}}
        self.assertFalse(valid_slot(self.start, self.a, [e], self.profile, NOW))

    def test_work_hours(self):
        self.a['category'] = 'work'
        self.assertFalse(valid_slot(self.start.replace(hour=18), self.a, [], self.profile, NOW))

    def test_personal_evening(self):
        self.assertTrue(valid_slot(self.start.replace(hour=19), self.a, [], self.profile, NOW))
        self.assertFalse(valid_slot(self.start.replace(hour=22), self.a, [], self.profile, NOW))

    def test_daily_capacity(self):
        self.a['date'] = '2026-10-26'
        self.profile['reserve_minutes'] = 15 * 60
        self.assertIsNone(propose(self.a, [], self.profile, NOW))

    def test_commitment_count(self):
        self.profile['max_commitments'] = 1
        events = [event(self.start.replace(hour=14), self.start.replace(hour=15))]
        self.assertFalse(valid_slot(self.start, self.a, events, self.profile, NOW))

    def test_deadline(self):
        self.a['deadline'] = self.start.replace(hour=9).isoformat()
        self.assertFalse(valid_slot(self.start, self.a, [], self.profile, NOW))

    def test_no_past_slots(self):
        self.assertFalse(valid_slot(NOW, self.a, [], self.profile, NOW))

    def test_date_constraint(self):
        self.a['date'] = '2026-10-27'
        p = propose(self.a, [], self.profile, NOW)
        self.assertTrue(p['start'].startswith('2026-10-27'))

    def test_no_room_means_no_proposal(self):
        self.a.update(date='2026-10-26', earliest_hour=16, latest_hour=17, duration=90)
        self.assertIsNone(propose(self.a, [], self.profile, NOW))

    def test_reschedule_excludes_own_event(self):
        e = event(self.start, self.start + timedelta(hours=1))
        self.assertTrue(valid_slot(self.start, self.a, [e], self.profile, NOW, exclude_id='fixed'))

    def test_transparent_cancelled(self):
        for extra in [{'transparency': 'transparent'}, {'status': 'cancelled'}]:
            self.assertTrue(valid_slot(self.start, self.a, [event(self.start, self.start + timedelta(hours=1), **extra)], self.profile, NOW))

    def test_unknown_overlap_is_blocked(self):
        self.assertFalse(compatible(None, {'passive': True}))
        self.assertFalse(compatible({'interruptible': True, 'attention': 1, 'location': 'home'}, {'attention': 1, 'location': 'home'}))

    def test_explicit_compatible_resources(self):
        passive = {'passive': True, 'attention': 0, 'location': 'home'}
        active = {'interruptible': True, 'attention': 1, 'location': 'home'}
        self.assertTrue(compatible(passive, active))
        self.assertFalse(compatible(passive, dict(active, continuity=True)))
        self.assertFalse(compatible(passive, dict(active, location='office')))

    def test_forbidden_window(self):
        self.a['forbidden_windows'] = [[9, 11]]
        self.assertFalse(valid_slot(self.start, self.a, [], self.profile, NOW))

    def test_occupancy_union(self):
        self.profile['reserve_minutes'] = 12 * 60
        events = [event(self.start.replace(hour=8), self.start.replace(hour=9)), event(self.start.replace(hour=8), self.start.replace(hour=9))]
        self.assertTrue(valid_slot(self.start, self.a, events, self.profile, NOW))


class InterpretationTests(unittest.TestCase):
    def test_hebrew_brother_and_notes(self):
        a = interpret('מחר אני צריך להתקשר לאחי\nלדבר על הצעת המחיר', DEFAULT_PROFILE, NOW)
        self.assertEqual(a['date'], '2026-10-27')
        self.assertEqual(a['duration'], 20)
        self.assertIn('הצעת המחיר', a['note'])

    def test_duration(self):
        self.assertEqual(interpret('Exercise tomorrow 45 minutes', DEFAULT_PROFILE, NOW)['duration'], 45)

    def test_correction_preserves_notes(self):
        a = correct('הוא בעבודה עד 16:00', {'title': 'Call brother', 'duration': 20, 'category': 'personal', 'note': 'Ask about sample'})
        self.assertEqual(a['earliest_hour'], 16)
        self.assertEqual(a['note'], 'Ask about sample')

    def test_recurrence_requires_clarification(self):
        with self.assertRaises(Clarification):
            interpret('Exercise every day', DEFAULT_PROFILE, NOW)

    def test_invalid_duration(self):
        with self.assertRaises(Clarification):
            interpret('Call 1000 minutes', DEFAULT_PROFILE, NOW)

    def test_unknown_correction_requires_clarification(self):
        with self.assertRaises(Clarification):
            correct('maybe another thing', {'title': 'Call', 'duration': 20})

class AdditionalConstraintTests(unittest.TestCase):
    def test_work_cannot_override_sleep(self):
        profile = dict(DEFAULT_PROFILE, work_start=5)
        a = {'title': 'Work', 'duration': 30, 'category': 'work'}
        self.assertFalse(valid_slot(NOW.replace(hour=6), a, [], profile, NOW.replace(hour=4)))

    def test_fractional_hour_is_clarified(self):
        with self.assertRaises(Clarification):
            interpret('Call tomorrow after 16:30', DEFAULT_PROFILE, NOW)
        with self.assertRaises(Clarification):
            correct('after 16:30', {'title': 'Call', 'duration': 20})

    def test_deadline_is_not_silently_dropped_in_basic_mode(self):
        with self.assertRaises(Clarification):
            interpret('Finish by tomorrow', DEFAULT_PROFILE, NOW)

    def test_scoped_preference_reused_without_overriding_explicit_request(self):
        profile = dict(DEFAULT_PROFILE, preferences={'Call brother': {'earliest_hour': 16}})
        self.assertEqual(interpret('Call brother tomorrow', profile, NOW)['earliest_hour'], 16)
        self.assertEqual(interpret('Call brother', profile, NOW)['earliest_hour'], 16)
        self.assertNotIn('earliest_hour', interpret('Exercise tomorrow', profile, NOW))
