"""Offline regression tests. All job records here are synthetic test fixtures."""
import csv
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock
import yaml
from src import runtime, scrapers, onboard_company, main, website
from src.source_quality import quality
from src.review import review_candidate, save_review

ROOT = Path(__file__).resolve().parent.parent

def setUpModule():
    # Every HTTP interaction in this suite must be explicitly mocked.
    global NETWORK_GUARD
    NETWORK_GUARD=patch('requests.sessions.Session.request',side_effect=AssertionError('Unexpected real network call in offline test'))
    NETWORK_GUARD.start()

def tearDownModule():
    NETWORK_GUARD.stop()

def job(**kwargs):
    return dict(dict(title='Senior Project Manager', location='Munich, Germany',
                     url='https://example.org/jobs/project-manager-123', description='project delivery governance',
                     posted_date=datetime.now(timezone.utc).date().isoformat()), **kwargs)

def response(data, status=200):
    result=Mock(status_code=status)
    result.json.return_value=data
    return result

def profile():
    return dict(title_must_match=['project manager'], title_must_not_match=['intern'],
                scoring_keywords=[{'term':'project','weight':10}], score_ceiling=10, main_min_score=1,
                review_candidates={'enabled':True,'min_description_score':7,'terms':['project','delivery']})

class PolicyTests(unittest.TestCase):
    def test_default_full_coverage(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(runtime.selected({'priority':3}))
    def test_disabled(self):
        self.assertFalse(runtime.selected({'enabled':False},mode='all'))
    def test_scheduled_reads_yaml(self):
        monday=datetime(2026,11,16)
        self.assertTrue(runtime.selected({'priority':3},{'priority_3':{'weekdays':[0]}},'scheduled',monday))
        self.assertFalse(runtime.selected({'priority':3},{'priority_3':{'weekdays':[6]}},'scheduled',monday))
    def test_yaml_budget(self):
        self.assertEqual(runtime.source_seconds({'priority':2},{'priority_2':{'source_seconds':12}},mode='scheduled'),12)
    def test_full_run_budget_preserved(self):
        self.assertEqual(runtime.source_seconds({'priority':3},mode='all'),180)
    def test_invalid_mode(self):
        with self.assertRaises(ValueError):runtime.selected({},mode='typo')
    def test_invalid_budget(self):
        with self.assertRaises(ValueError):runtime.source_seconds({'source_seconds':-1})
    def test_page_limit_context(self):
        with runtime.company_options({'max_pages':2}):self.assertEqual(runtime.page_limit(100),2)
        self.assertEqual(runtime.page_limit(100),100)

class QualityTests(unittest.TestCase):
    def test_complete_pass(self):
        self.assertTrue(quality([job()],{'completion':'reported_total_reached','advertised_total':1})['passes'])
    def test_unknown_coverage_fails(self):
        self.assertFalse(quality([job()])['passes'])
    def test_empty_never_promoted(self):
        self.assertFalse(quality([])['passes'])
    def test_location_gate(self):
        result=quality([job(location='')],{'completion':'single_feed_complete'})
        self.assertIn('explicit_location_below_80_percent',result['reasons'])
    def test_raw_denominator(self):
        self.assertFalse(quality([job()],{'completion':'single_feed_complete','raw_candidates':2})['passes'])
    def test_expected_location_is_not_evidence(self):
        result=quality([job(location='')],{'completion':'single_feed_complete'},['Munich'])
        self.assertEqual(result['expected_location_matches'],0)
        self.assertFalse(result['passes'])

class AdapterTests(unittest.TestCase):
    def test_detection(self):
        self.assertEqual(scrapers.platform_from_url('https://acme.recruitee.com/')[1:3],('recruitee','acme'))
        self.assertEqual(scrapers.platform_from_url('https://apply.workable.com/acme/')[1:3],('workable','acme'))
    def test_recruitee_multiple_locations(self):
        data={'offers':[{'id':1,'title':'Senior Project Manager','careers_url':'https://acme.recruitee.com/o/pm','locations':[{'city':'Munich','country':'Germany'},{'city':'Berlin'}]}]}
        with patch.object(scrapers,'_safe_get',return_value=response(data)):
            jobs=scrapers.scrape_recruitee('Acme','acme')
        self.assertIn('Munich',jobs[0]['location']);self.assertIn('Berlin',jobs[0]['location'])
        self.assertTrue(quality(jobs)['passes'])
    def test_recruitee_empty_retains_stats(self):
        with patch.object(scrapers,'_safe_get',return_value=response({'offers':[]})):
            result=scrapers.scrape_recruitee('Acme','acme')
        self.assertEqual(result.pagination['advertised_total'],0)
    def test_workable_details(self):
        data={'title':'Project Manager','description':'<p>Project delivery</p>','location':{'city':'Munich'}}
        with patch.object(scrapers,'_safe_get',return_value=response(data)):
            details,body=scrapers.fetch_job_details('https://apply.workable.com/api/v2/accounts/acme/jobs/ABC')
        self.assertEqual(details['location'],'Munich');self.assertIn('Project delivery',body)
    def test_greenhouse_complete_metadata(self):
        data={'jobs':[{'title':'Project Manager','location':{'name':'Munich'},'absolute_url':'https://job-boards.greenhouse.io/acme/jobs/123'}]}
        with patch.object(scrapers,'_safe_get',return_value=response(data)):
            result=scrapers.scrape_greenhouse('Acme','acme')
        self.assertTrue(quality(result)['passes'])
    def test_workable_two_pages(self):
        item=lambda n: {'title':'Senior Project Manager','shortcode':str(n),'location':{'city':'Munich'}}
        with patch.object(scrapers.requests,'post',side_effect=[response({'results':[item(1)],'nextPage':'next','total':2}),response({'results':[item(2)],'total':2})]) as post:
            result=scrapers.scrape_workable('Acme','acme')
        self.assertEqual(len(result),2)
        self.assertEqual(post.call_args_list[1].kwargs['json']['token'],'next')
        self.assertTrue(quality(result)['passes'])
    def test_workable_page_limit(self):
        with runtime.company_options({'max_pages':1}),patch.object(scrapers.requests,'post',return_value=response({'results':[{'title':'Project Manager','shortcode':'1','location':{'city':'Munich'}}],'nextPage':'more','total':2})):
            result=scrapers.scrape_workable('Acme','acme')
        self.assertEqual(result.pagination['completion'],'page_limit');self.assertFalse(quality(result)['passes'])
    def test_workable_no_retry_rate_limit(self):
        with patch.object(scrapers.requests,'post',return_value=response({},429)) as post:
            result=scrapers.scrape_workable('Acme','acme')
        self.assertEqual(post.call_count,1);self.assertEqual(result.pagination['completion'],'http_429')
    def test_workable_repeated_page(self):
        data={'results':[{'title':'Project Manager','shortcode':'1'}],'nextPage':'same'}
        with patch.object(scrapers.requests,'post',return_value=response(data)):
            result=scrapers.scrape_workable('Acme','acme')
        self.assertEqual(result.pagination['completion'],'repeated_page')

class OnboardingTests(unittest.TestCase):
    def test_unknown_platform_retains_url(self):
        with patch.object(onboard_company,'discover_real_careers_url',return_value=(None,None,None)),patch.object(onboard_company,'scrape_company',return_value=[]):
            entry,result=onboard_company.propose('Acme','https://example.org/careers')
        self.assertEqual(entry['careers_url'],'https://example.org/careers')
        self.assertNotIn('verified_on',entry);self.assertFalse(result['passes'])
    def test_invalid_url(self):
        with self.assertRaises(ValueError):onboard_company.propose('Acme','not a url')
    def test_failed_promotion_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'companies.yaml';p.write_text('companies: []\n')
            with self.assertRaises(ValueError):onboard_company.promote({}, {'passes':False},p)
            self.assertEqual(p.read_text(),'companies: []\n')
    def test_success_promotion_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'companies.yaml';p.write_text('companies: []\n')
            onboard_company.promote({'name':'Acme'},{'passes':True},p)
            self.assertEqual(len(yaml.safe_load(p.read_text())['companies']),1)
            self.assertTrue(p.with_suffix('.yaml.bak').exists())

class ReviewTests(unittest.TestCase):
    def test_strong_unusual_title_review(self):
        self.assertIsNotNone(review_candidate(job(title='Delivery Coordinator'),profile(),{'name':'Acme'}))
    def test_direct_role_not_duplicated(self):
        self.assertIsNone(review_candidate(job(),profile(),{'name':'Acme'}))
    def test_outside_not_reviewed(self):
        self.assertIsNone(review_candidate(job(title='Delivery Coordinator',location='Berlin'),profile(),{'name':'Acme'}))
    def test_excluded_title_not_reviewed(self):
        self.assertIsNone(review_candidate(job(title='Junior Delivery Coordinator'),profile(),{'name':'Acme'}))
    def test_csv_dedup_and_formula_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'review.csv';row={'company':'=BAD','url':'https://example.org/jobs/123'}
            save_review([row,row],p)
            with p.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),1);self.assertTrue(rows[0]['company'].startswith("'="))

class PipelineTests(unittest.TestCase):
    def test_mocked_pipeline_tracker_webpage_and_review(self):
        # Only the network extraction is mocked; filtering, scoring, tracker and HTML generation run.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config').mkdir();(root/'data').mkdir();(root/'docs').mkdir()
            cfg=root/'config/companies.yaml';cv=root/'config/cv_profile.yaml'
            cfg.write_text(yaml.safe_dump({'companies':[{'name':'Acme','city':'Munich','careers_url':'https://example.org/careers','priority':1,'detail_fetch_mode':'none'}]}))
            cv.write_text(yaml.safe_dump(profile()))
            tracker=root/'data/job_tracker.xlsx';html=root/'docs/index.html'
            with patch.multiple(main,ROOT=root,COMPANIES_FILE=cfg,CV_PROFILE_FILE=cv,JOB_BOARDS_FILE=root/'config/missing.yaml',TRACKER_FILE=tracker,HEALTH_FILE=root/'docs/scraper_health.json'),patch.object(main,'scrape_company',return_value=[job(),job(title='Delivery Coordinator',url='https://example.org/jobs/coordinator-124')]),patch.object(main,'generate_html',side_effect=lambda:website.generate(tracker,html)),patch.object(website,'CV_PROFILE_FILE',cv),patch.dict(os.environ,{'SCRAPE_TIER':'all','SCRAPE_COMPANIES':''}),patch.object(main.time,'sleep'):
                main.run()
            self.assertTrue(tracker.exists());self.assertTrue(html.exists())
            with (root/'data/review_candidates.csv').open(encoding='utf-8-sig',newline='') as f:reviews=list(csv.DictReader(f))
            self.assertEqual(len(reviews),1)
            health=json.loads((root/'docs/scraper_health.json').read_text())
            self.assertIn('Acme',str(health))
    def test_config_integrity(self):
        companies=yaml.safe_load((ROOT/'config/companies.yaml').read_text())['companies']
        self.assertGreater(len(companies),0)
        self.assertTrue(all(c.get('verification_status') == 'passed' for c in companies if c.get('verified_on')))
        self.assertEqual(len({c['name'].casefold() for c in companies}),len(companies))

if __name__=='__main__':unittest.main()
