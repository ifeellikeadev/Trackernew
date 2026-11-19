"""Coverage regression tests. HTML/JSON below are synthetic fixtures, not live jobs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from src import scrapers, runtime, main, matching

class CoverageFixTests(unittest.TestCase):
    def setUp(self):
        self.guard=patch('requests.sessions.Session.request',side_effect=AssertionError('Unmocked network'))
        self.guard.start()
    def tearDown(self):self.guard.stop()
    def response(self,data,status=200):
        r=Mock(status_code=status);r.json.return_value=data;return r
    def test_hensoldt_explicit_location_and_date(self):
        html='<table><tr><td><a class="jobTitle-link" href="/job/FFB-Project-Manager/123/">Projektmanager Special Mission Aircraft</a></td><td><span class="jobLocation">Fuerstenfeldbruck, DE</span></td><td><span class="jobDate">Nov 17, 2026</span></td></tr></table>'
        jobs=scrapers.extract_page(html,'https://jobs.hensoldt.net/go/Germany/9460255/')
        self.assertEqual(len(jobs),1);self.assertIn('Fuerstenfeldbruck',jobs[0]['location'])
        self.assertEqual(jobs[0]['posted_date'],'Nov 17, 2026')
    def test_quantum_table_location(self):
        html='<table><thead><tr><th>Job title</th><th>Work model</th><th>Location</th></tr></thead><tbody><tr><td><a href="/o/project-manager">Project Manager</a></td><td>On-site</td><td>QFI Gilching, Bayern, Germany</td></tr></tbody></table>'
        jobs=scrapers.extract_page(html,'https://career.quantum-systems.com/')
        self.assertEqual(len(jobs),1);self.assertIn('Gilching',jobs[0]['location'])
        self.assertEqual(matching.location_decision(jobs[0])['status'],'confirmed')
    def test_no_location_from_footer(self):
        html='<div><a href="/o/project-manager">Project Manager</a></div><footer>Gilching</footer>'
        self.assertFalse(scrapers.extract_page(html,'https://career.quantum-systems.com/')[0]['location'])
    def test_hensoldt_numeric_pagination(self):
        url='https://jobs.hensoldt.net/go/Germany/9460255/?startrow=25'
        html='<a href="?startrow=0">1</a><a href="?startrow=50">3</a><a href="?startrow=75">4</a>'
        self.assertTrue(scrapers.next_link(html,url).endswith('startrow=50'))
    def test_numeric_pagination_same_host_only(self):
        self.assertIsNone(scrapers.next_link('<a href="https://example.org/?startrow=25">2</a>','https://jobs.hensoldt.net/'))
    def test_workday_searches_and_deduplication(self):
        data={'total':1,'jobPostings':[{'title':'Project Manager','externalPath':'/job/Taufkirchen/PM_JR1','locationsText':'Taufkirchen','postedOn':'Posted Today'}]}
        with runtime.company_options({'search_queries':['Taufkirchen','Munich']}),patch.object(scrapers.requests,'post',return_value=self.response(data)) as post:
            jobs=scrapers.scrape_workday('Airbus','https://ag.wd3.myworkdayjobs.com/Airbus')
        self.assertEqual(len(jobs),1);self.assertEqual(post.call_count,2)
        self.assertEqual(post.call_args_list[0].kwargs['json']['searchText'],'Taufkirchen')
        self.assertIn('/wday/cxs/ag/Airbus/job/',jobs[0]['_detail_api_url'])
        self.assertEqual(jobs.pagination['scope'],'configured_searches')
    def test_workday_rate_limit_no_fallback(self):
        with patch.object(scrapers.requests,'post',return_value=self.response({},429)) as post,patch.object(scrapers,'scrape_generic') as generic:
            jobs=scrapers.scrape_company({'name':'Airbus','ats':'workday','careers_url':'https://ag.wd3.myworkdayjobs.com/Airbus'})
        self.assertEqual(post.call_count,1);generic.assert_not_called()
        self.assertEqual(jobs.pagination['completion'],'http_429')
    def test_workday_detail_multiple_locations(self):
        data={'jobPostingInfo':{'title':'Project Manager','location':'Berlin','additionalLocations':['Taufkirchen'],'jobDescription':'<p>Project delivery and budget management</p>','startDate':'2026-11-17'}}
        with patch.object(scrapers,'_safe_get',return_value=self.response(data)):
            details,body=scrapers.fetch_job_details('https://ag.wd3.myworkdayjobs.com/wday/cxs/ag/Airbus/job/PM_JR1')
        self.assertIn('Taufkirchen',details['location']);self.assertIn('budget',body)
    def test_workday_partial_page_retained(self):
        data={'total':2,'jobPostings':[{'title':'Project Manager','externalPath':'/job/PM_JR1','locationsText':'Munich'}]}
        with patch.object(scrapers.requests,'post',side_effect=[self.response(data),self.response({},429)]):
            jobs=scrapers.scrape_workday('Airbus','https://ag.wd3.myworkdayjobs.com/Airbus')
        self.assertEqual(len(jobs),1);self.assertEqual(jobs.pagination['completion'],'http_429')
    def test_pipeline_enriches_direct_role_body(self):
        profile={'title_must_match':['project manager'],'main_min_score':3,'score_ceiling':10,'scoring_keywords':[{'term':'budget','weight':10}]}
        job={'title':'Project Manager','location':'Munich','url':'https://example.org/job/123','description':'','posted_date':''}
        company={'name':'Test','city':'Munich','careers_url':'https://example.org','priority':1}
        with tempfile.TemporaryDirectory() as tmp,patch.object(main,'scrape_company',return_value=[job]),patch.object(main,'fetch_job_details',return_value=({},'budget management')),patch.object(main,'HEALTH_FILE',Path(tmp)/'health.json'),patch.object(main,'ROOT',Path(tmp)),patch.object(main.time,'sleep'):
            jobs,_,_=main.scrape_all_any_city([company],profile)
        self.assertEqual(len(jobs),1);self.assertNotIn('relevance_score',jobs[0])
    def test_unknown_location_goes_to_review(self):
        profile={'title_must_match':['project manager'],'main_min_score':1}
        job={'title':'Project Manager','location':'','url':'https://example.org/job/123','description':'','posted_date':''}
        company={'name':'Test','city':'Munich','careers_url':'https://example.org','priority':1,'detail_fetch_mode':'none'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(main,'scrape_company',return_value=[job]),patch.object(main,'HEALTH_FILE',Path(tmp)/'health.json'),patch.object(main,'ROOT',Path(tmp)),patch.object(main.time,'sleep'):
            jobs,_,reports=main.scrape_all_any_city([company],profile)
            review=(Path(tmp)/'data/review_candidates.csv').read_text(encoding='utf-8-sig')
        self.assertFalse(jobs);self.assertIn('explicit workplace',review);self.assertTrue(reports[0]['funnel_balanced'])

if __name__=='__main__':unittest.main()
