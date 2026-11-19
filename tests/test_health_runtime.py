"""Offline regression fixtures for health/runtime fixes. No live vacancies."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import yaml
from src import scrapers, diagnostics, main, matching, runtime

ROOT=Path(__file__).resolve().parent.parent
class HealthRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.guard=patch('requests.sessions.Session.request',side_effect=AssertionError('Unexpected network'))
        self.guard.start()
    def tearDown(self):self.guard.stop()
    def test_production_related_roles_remain_disabled(self):
        profile=yaml.safe_load((ROOT/'config/cv_profile.yaml').read_text())
        self.assertIs(profile['related_roles']['enabled'],False)
        for title in ('Consultant','Product Owner'):
            self.assertFalse(matching.role_decision({'title':title},profile)['keep'])
    def test_disabled_html_attribute_is_not_clickable(self):
        item=Mock();item.is_visible.return_value=True;item.is_enabled.return_value=True
        item.inner_text.return_value='Next'
        item.get_attribute.side_effect=lambda key: '' if key=='disabled' else None
        found=Mock();found.count.return_value=1;found.nth.return_value=item
        context=Mock();context.locator.return_value=found
        self.assertIsNone(scrapers.next_public_control(context))
    def test_enabled_next_control(self):
        item=Mock();item.is_visible.return_value=True;item.is_enabled.return_value=True
        item.get_attribute.return_value=None
        found=Mock();found.count.return_value=1;found.nth.return_value=item
        context=Mock();context.locator.return_value=found
        self.assertIs(scrapers.next_public_control(context),item)
    def test_unrelated_weiterlesen_link_is_not_pagination(self):
        item=Mock();item.is_visible.return_value=True;item.is_enabled.return_value=True
        item.get_attribute.return_value=None;item.inner_text.return_value='Weiterlesen about our culture'
        empty=Mock();empty.count.return_value=0
        found=Mock();found.count.return_value=1;found.nth.return_value=item
        context=Mock();context.locator.side_effect=lambda s:found if ':has-text(' in s else empty
        self.assertIsNone(scrapers.next_public_control(context))
    def test_empty_html_retains_pagination(self):
        response=Mock(text='<html></html>',url='https://example.org/careers')
        response.cookies.get_dict.return_value={}
        with patch.object(scrapers,'browse_public_board',return_value=([],{'completion':'no_next_control_unverified'})):
            result=scrapers.scrape_html_board('Example',response.url,lambda *a,**k:response)
        self.assertEqual(result.pagination['pages'],1)
        self.assertEqual(result.pagination['completion'],'no_next_link_unverified')
    def test_empty_greenhouse_does_not_trigger_fallback(self):
        with patch.object(scrapers,'scrape_greenhouse',return_value=scrapers.SourceResult([],{'completion':'single_feed_complete'})),patch.object(scrapers,'scrape_generic') as generic:
            result=scrapers.scrape_company({'name':'Example','ats':'greenhouse','careers_url':'https://example.org'})
        generic.assert_not_called();self.assertEqual(result.pagination['completion'],'single_feed_complete')
    def test_auto_empty_feed_does_not_trigger_fallback(self):
        with patch.dict(scrapers._PLATFORM_SCRAPERS,{'greenhouse':lambda *a:scrapers.SourceResult([],{'completion':'single_feed_complete'})}),patch.object(scrapers,'scrape_generic') as generic:
            scrapers.scrape_company({'name':'Example','ats':'auto','careers_url':'https://job-boards.greenhouse.io/example'})
        generic.assert_not_called()
    def test_metadata_only_rate_limit_is_diagnosed(self):
        row=diagnostics.new_row({'name':'Example'});row['pagination']={'completion':'http_429'}
        diagnostics.finish(row)
        self.assertEqual(row['diagnosis'],'RATE_LIMITED');self.assertEqual(row['status'],'FAILED_OR_BLOCKED')
    def test_funnel_mismatch_is_visible(self):
        row=diagnostics.new_row({'name':'Example'});row.update(raw_candidates=1,eligible=1,funnel_balanced=False)
        diagnostics.finish(row)
        self.assertEqual(row['status'],'PARTIAL_OR_WARNING')
        self.assertIn('Candidate funnel accounting is unbalanced',row['issues'])
    def test_invalid_budget_does_not_abort_other_sources(self):
        companies=[{'name':'Bad','source_seconds':-1},{'name':'Good','careers_url':'https://example.org','detail_fetch_mode':'none'}]
        with tempfile.TemporaryDirectory() as d,patch.object(main,'ROOT',Path(d)),patch.object(main,'HEALTH_FILE',Path(d)/'health.json'),patch.object(main,'scrape_company',return_value=[]),patch.object(main.time,'sleep'):
            _,_,reports=main.scrape_all_any_city(companies,{'title_must_match':['project manager']})
        self.assertEqual(len(reports),2);self.assertEqual(reports[0]['status'],'ERROR')
        self.assertEqual(reports[0]['runtime_budget'],{})
    def test_workday_start_date_is_not_publication_date(self):
        response=Mock();response.json.return_value={'jobPostingInfo':{'title':'Project Manager','location':'Munich','startDate':'2000-01-01'}}
        with patch.object(scrapers,'_safe_get',return_value=response):
            details,_=scrapers.fetch_job_details('https://example.wd3.myworkdayjobs.com/wday/cxs/example/jobs/job/1')
        self.assertNotIn('posted_date',details)
    def test_amazon_checkpoints_before_second_page(self):
        response=Mock();response.json.return_value={'hits':2,'jobs':[{'id':1,'title':'Project Manager','job_path':'/en/jobs/1/pm','location':'Munich'}]}
        def get(*a,**k):
            if k['params']['offset']:raise diagnostics.SourceDeadline()
            return response
        with diagnostics.source_budget(30) as budget:
            result=budget.call(scrapers.scrape_amazon,'Example','https://www.amazon.jobs/en/search',get,default=None)
        self.assertIsNone(result);self.assertEqual(len(budget.candidates),1)
    def test_all_bundled_modules_compile(self):
        import src
        for name,source in src._SOURCES.items():compile(source,'src/'+name+'.py','exec')
if __name__=='__main__':unittest.main()
