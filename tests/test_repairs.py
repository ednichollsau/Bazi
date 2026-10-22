import os
import unittest
from unittest.mock import patch, Mock
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import api_server as api
import gcal_sync as sync


class CalendarTests(unittest.TestCase):
    def test_clock_changes(self):
        for instant, local in [('2026-03-28T18:00:00Z','2026-03-28T18:00:00+00:00'),
                               ('2026-03-29T17:00:00Z','2026-03-29T18:00:00+01:00'),
                               ('2026-10-24T17:00:00Z','2026-10-24T18:00:00+01:00'),
                               ('2026-10-25T18:00:00Z','2026-10-25T18:00:00+00:00'),
                               ('2027-03-28T17:00:00Z','2027-03-28T18:00:00+01:00')]:
            self.assertEqual(sync._to_gcal_dt(instant)['dateTime'],local)

    def test_ambiguous_or_nonexistent_times_rejected(self):
        for value in ['2026-03-29T01:30:00','2026-10-25T01:30:00']:
            with self.assertRaises(ValueError): sync._instant(value)
        self.assertNotEqual(sync._instant('2026-10-25T01:30:00+01:00'),sync._instant('2026-10-25T01:30:00+00:00'))

    def fixtures(self):
        clinic={'start_dt':'2026-10-24T17:00:00Z','end_dt':'2026-10-24T18:00:00Z','status':'confirmed'}
        event={'start':{'dateTime':'2026-10-24T18:00:00+01:00'},'end':{'dateTime':'2026-10-24T19:00:00+01:00'},
               'extendedProperties':{'private':{'enaStart':clinic['start_dt'],'enaEnd':clinic['end_dt']}}}
        return clinic,event

    def test_equal_instants_do_not_reschedule(self):
        clinic,event=self.fixtures();self.assertEqual(sync.decide(clinic,event),'unchanged')

    def test_console_edit_pushes_only(self):
        clinic,event=self.fixtures();clinic['end_dt']='2026-10-24T18:15:00Z'
        self.assertEqual(sync.decide(clinic,event),'push')

    def test_google_edit_pulls_only(self):
        clinic,event=self.fixtures();event['end']['dateTime']='2026-10-24T19:15:00+01:00'
        self.assertEqual(sync.decide(clinic,event),'pull')

    def test_simultaneous_edits_conflict(self):
        clinic,event=self.fixtures();clinic['end_dt']='2026-10-24T18:30:00Z'
        event['end']['dateTime']='2026-10-24T19:15:00+01:00'
        self.assertEqual(sync.decide(clinic,event),'conflict')

    def test_legacy_untracked_difference_conflicts(self):
        clinic,event=self.fixtures();event.pop('extendedProperties');clinic['end_dt']='2026-10-24T18:30:00Z'
        self.assertEqual(sync.decide(clinic,event),'conflict')

    def test_missing_event_never_cancels_booking(self):
        clinic,_=self.fixtures();self.assertEqual(sync.decide(clinic,None),'create')

    def test_calendar_deletion_does_not_cancel_booking(self):
        clinic,event=self.fixtures();event['status']='cancelled'
        self.assertEqual(sync.decide(clinic,event),'conflict')

    def test_pagination(self):
        events=Mock();events.list.return_value.execute.side_effect=[{'items':[{'id':'a'}],'nextPageToken':'next'},{'items':[{'id':'b'}]}]
        self.assertEqual([e['id'] for e in sync._all_events(events)],['a','b'])
        self.assertEqual(events.list.call_args.kwargs['pageToken'],'next')

    def test_disabled_does_not_connect(self):
        with patch.dict(os.environ,{'GCAL_SYNC_ENABLED':'false'}),patch.object(sync,'get_gcal_service') as service:
            self.assertEqual(sync.run_sync(),{'status':'disabled'});service.assert_not_called()


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(api.app)
        self.data={'name':'Fictional Test','year':1990,'month':1,'day':2,'hour':None,'handedness':'right'}

    def test_anonymous_generator_cannot_call_model(self):
        with patch.object(api,'_generate_reading') as generate:
            self.assertEqual(self.client.post('/generate',json=self.data).status_code,403)
            generate.assert_not_called()

    def test_generator_uses_real_calculations_without_delivery_or_patient_writes(self):
        model=Mock();model.__enter__=Mock(return_value=model);model.__exit__=Mock(return_value=False)
        model.responses.create.return_value=Mock(output_text='Fictional reading',status='completed')
        with patch.object(api,'DASHBOARD_TOKEN','test-token'),patch.dict(os.environ,{'OPENAI_API_KEY':'test-key'}),patch.object(api,'OpenAI',return_value=model),patch.object(api.httpx,'Client') as delivery,patch.object(api,'save_submission') as save,patch.object(api,'find_or_create_patient') as patient:
            response=self.client.post('/generate',json=self.data,headers={'Authorization':'Bearer test-token'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['protocol']['points'])
        self.assertEqual(response.json()['reading'],'Fictional reading')
        self.assertEqual(response.headers['cache-control'],'no-store')
        delivery.assert_not_called();save.assert_not_called();patient.assert_not_called()

    def test_public_reading_delivery_flow_preserved(self):
        model=Mock();model.__enter__=Mock(return_value=model);model.__exit__=Mock(return_value=False)
        model.responses.create.return_value=Mock(output_text='Fictional reading',status='completed')
        delivery=Mock();delivery.__enter__=Mock(return_value=delivery);delivery.__exit__=Mock(return_value=False)
        delivery.post.return_value.status_code=200
        delivery.post.return_value.json.return_value={'id':'mock-only'}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-key','RESEND_API_KEY':'test-key'}),patch.object(api,'OpenAI',return_value=model),patch.object(api.httpx,'Client',return_value=delivery),patch.object(api,'email_exists',return_value=True),patch.object(api,'submission_exists',return_value=False),patch.object(api,'find_or_create_patient',return_value=1),patch.object(api,'save_submission') as save:
            response=self.client.post('/reading',json={**self.data,'email':'fictional@example.invalid'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['success'])
        self.assertIn('pillars_data',response.json())
        self.assertEqual(delivery.post.call_count,1)
        self.assertEqual(delivery.post.call_args.kwargs['json']['to'],['fictional@example.invalid'])
        self.assertEqual(save.call_count,1)

    def test_missing_openai_key_does_not_send_email(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}),patch.object(api.httpx,'Client') as email:
            response=self.client.post('/reading',json={**self.data,'email':'fictional@example.invalid'})
        self.assertEqual(response.status_code,503)
        email.assert_not_called()

    def test_failed_or_incomplete_response_never_emails(self):
        for result in [Mock(output_text='Partial text',status='incomplete'),Mock(output_text='',status='completed'),RuntimeError('secret-provider-details')]:
            model=Mock();model.__enter__=Mock(return_value=model);model.__exit__=Mock(return_value=False)
            if isinstance(result,Exception):model.responses.create.side_effect=result
            else:model.responses.create.return_value=result
            with patch.dict(os.environ,{'OPENAI_API_KEY':'test-key'}),patch.object(api,'OpenAI',return_value=model),patch.object(api.httpx,'Client') as email:
                response=self.client.post('/reading',json={**self.data,'email':'fictional@example.invalid'})
            self.assertEqual(response.status_code,502)
            self.assertNotIn('secret-provider-details',response.text)
            email.assert_not_called()

    def test_openai_request_preserves_prompt_and_disables_response_storage(self):
        model=Mock();model.__enter__=Mock(return_value=model);model.__exit__=Mock(return_value=False)
        model.responses.create.return_value=Mock(output_text='Fictional reading',status='completed')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-key','OPENAI_READING_MODEL':'configured-model'}),patch.object(api,'OpenAI',return_value=model):
            api._generate_reading(api.GeneratorRequest(**self.data))
        request=model.responses.create.call_args.kwargs
        self.assertEqual(request['model'],'configured-model')
        self.assertEqual(request['instructions'],api.SYSTEM_PROMPT)
        self.assertFalse(request['store'])
        self.assertNotIn('email',request['input'].lower())
        self.assertIn('Fictional Test',request['input'])

    def test_invalid_date_rejected(self):
        self.data.update(month=2,day=31)
        self.assertEqual(self.client.post('/generate',json=self.data).status_code,422)

    def test_reading_still_requires_email(self):
        self.assertEqual(self.client.post('/reading',json=self.data).status_code,422)

    def test_token_not_interpolated_into_dashboard(self):
        response=self.client.get('/dashboard',params={'token':"';alert('unsafe');//"})
        self.assertEqual(response.status_code,200)
        self.assertNotIn("alert('unsafe')",response.text)

    def test_subscriber_export_rejects_anonymous(self):
        self.assertEqual(self.client.get('/api/marketing/reading-subscribers').status_code,401)

    def test_manual_sync_disabled(self):
        with patch.object(api,'DASHBOARD_TOKEN','test-token'),patch.dict(os.environ,{'GCAL_SYNC_ENABLED':'false'}):
            self.assertEqual(self.client.get('/api/gcal/sync',headers={'Authorization':'Bearer test-token'}).status_code,409)

if __name__=='__main__':unittest.main()
