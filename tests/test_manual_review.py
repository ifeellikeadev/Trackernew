"""Offline fixtures only. No real job listings or network requests."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from openpyxl import Workbook, load_workbook
from src import main, matching, tracker, website, review
ROOT=Path(__file__).resolve().parent.parent
PROFILE=yaml.safe_load((ROOT/'config/cv_profile.yaml').read_text())
def job(**kw):
    return dict(dict(title='Project Manager',location='Munich',city='Munich',company='Example',url='https://example.org/job/123',description='',posted_date=''),**kw)
class ManualReviewTests(unittest.TestCase):
    def setUp(self):
        global PROFILE
        self.original_profile=PROFILE
        PROFILE=copy.deepcopy(PROFILE)
        PROFILE['related_roles']['enabled']=True
    def tearDown(self):
        global PROFILE
        PROFILE=self.original_profile

    def test_direct_no_description(self):self.assertTrue(matching.role_decision(job(),PROFILE)['keep'])
    def test_related_no_description(self):self.assertTrue(matching.role_decision(job(title='Product Owner'),PROFILE)['keep'])
    def test_related_one_group(self):self.assertTrue(matching.role_decision(job(title='Consultant',description='project delivery'),PROFILE)['keep'])
    def test_hospitality_title(self):self.assertFalse(matching.role_decision(job(title='Project Manager Hospitality'),PROFILE)['keep'])
    def test_hospitality_required(self):self.assertFalse(matching.role_decision(job(description='Experience in hospitality is required.'),PROFILE)['keep'])
    def test_required_first(self):self.assertFalse(matching.role_decision(job(description='Must have experience in hospitality.'),PROFILE)['keep'])
    def test_optional_kept(self):self.assertTrue(matching.role_decision(job(description='Hospitality experience is preferred.'),PROFILE)['keep'])
    def test_employer_sector_kept(self):self.assertTrue(matching.role_decision(job(description='We build software for hospitality businesses.'),PROFILE)['keep'])
    def test_junior_excluded(self):self.assertFalse(matching.role_decision(job(title='Junior Project Manager'),PROFILE)['keep'])
    def test_unrelated_title_excluded(self):self.assertFalse(matching.role_decision(job(title='Chef'),PROFILE)['keep'])
    def test_review_no_score(self):self.assertIsNotNone(review.review_candidate(job(title='Delivery Coordinator'),PROFILE,{'name':'Example'}))
    def test_score_never_prunes_tracker(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tracker.xlsx'
            tracker.update_tracker(p,[job(match_type='Related role',relevance_score=1)],min_score=10)
            wb=load_workbook(p);self.assertEqual(wb['Jobs'].max_row,2)
            self.assertNotIn('Relevance Score (1-10)',[c.value for c in wb['Jobs'][1]]);wb.close()
            tracker.update_tracker(p,[],min_score=10)
            wb=load_workbook(p);self.assertEqual(wb['Jobs'].max_row,2);wb.close()
    def test_legacy_workbook_migration(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tracker.xlsx';wb=Workbook();ws=wb.active;ws.title='Jobs'
            ws.append(['Job Posted','Company','City','Job Title','Relevance Score (1-10)','Location','URL','Match Type','Match Reason'])
            ws.append(['','Example','Munich','Consultant',1,'Munich','https://example.org/job/123','Related role','Review']);wb.save(p);wb.close()
            tracker.update_tracker(p,[],min_score=10)
            wb=load_workbook(p);self.assertEqual(wb['Jobs'].max_row,2);self.assertEqual(wb['Jobs'].cell(2,7).value,'Related role');wb.close()
    def test_website_no_score_and_low_related_visible(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tracker.xlsx';out=Path(d)/'index.html'
            tracker.update_tracker(p,[job(match_type='Related role',relevance_score=1)])
            cv=Path(d)/'profile.yaml';cv.write_text(yaml.safe_dump(PROFILE))
            with patch.object(website,'CV_PROFILE_FILE',cv):website.generate(p,out)
            text=out.read_text();self.assertIn('Project Manager',text);self.assertIn('Why included',text);self.assertNotIn('/10</span>',text)
    def test_pipeline_no_score_even_stale_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=dict(PROFILE,main_min_score=10)
            with patch.object(main,'ROOT',Path(d)),patch.object(main,'HEALTH_FILE',Path(d)/'health.json'),patch.object(main,'scrape_company',return_value=[job(title='Product Owner')]),patch.object(main,'fetch_job_details',return_value=({},'')),patch.object(main.time,'sleep'),patch.object(main,'score_jobs',side_effect=AssertionError('Scoring must not run')):
                jobs,_,reports=main.scrape_all_any_city([{'name':'Example','city':'Munich','careers_url':'https://example.org','priority':1}],cfg)
            self.assertEqual(len(jobs),1);self.assertEqual(reports[0]['score_rejected'],0);self.assertTrue(reports[0]['funnel_balanced'])
    def test_pipeline_rechecks_enriched_requirements(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(main,'ROOT',Path(d)),patch.object(main,'HEALTH_FILE',Path(d)/'health.json'),patch.object(main,'scrape_company',return_value=[job()]),patch.object(main,'fetch_job_details',return_value=({},'Experience in hospitality is required.')),patch.object(main.time,'sleep'):
                jobs,_,reports=main.scrape_all_any_city([{'name':'Example','city':'Munich','careers_url':'https://example.org','priority':1}],PROFILE)
            self.assertFalse(jobs);self.assertTrue(reports[0]['funnel_balanced'])
if __name__=='__main__':unittest.main()
