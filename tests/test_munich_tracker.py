import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook, load_workbook
from src import main, matching, scrapers, tracker, website


class MunichTrackerTest(unittest.TestCase):
    profile = main.load_yaml(main.CV_PROFILE_FILE)
    candidate = dict(company='Test Employer', city='Munich', title='Senior Project Manager',
                     location='Munich', url='https://example.com/job/123', match_type='Direct match',
                     description=('Own end to end delivery and project planning, maintain the risk register and coordinate '
                                  'budget management, cross functional teams and vendor coordination. Lead platform '
                                  'implementation and process improvement for a grid operator in the energy sector. '
                                  'PMP preferred. Fluent German and solar PV experience are required.'))

    def test_scoring_and_missing_description(self):
        strong = dict(self.candidate)
        weak = dict(strong, description='The position covers administration of office materials. The person will maintain the filing system and arrange shared desk allocation each week.')
        missing = dict(strong, description='')
        matching.score_jobs([strong, weak, missing], self.profile)
        self.assertGreater(strong['relevance_score'], weak['relevance_score'])
        self.assertIn('German requirement', strong['relevance_notes'])
        self.assertIn('solar', strong['relevance_notes'])
        self.assertIn('supplier delivery', strong['relevance_evidence'])
        self.assertIsNone(missing['relevance_score'])
        self.assertFalse(self.profile['related_roles']['enabled'])

    def test_scope_keeps_explicit_local_locations_only(self):
        for city in ('Munich', 'München / Hamburg', 'Karlsfeld', 'Eresing', 'Neubiberg'):
            self.assertTrue(matching.allowed_job(dict(self.candidate, location=city)), city)
        for city in ('Dubai', 'Abu Dhabi', 'Germany', '', 'Berlin'):
            self.assertFalse(matching.allowed_job(dict(self.candidate, location=city)), city)
        self.assertTrue(main.excluded({'name':'test', 'city':'UAE'}))
        self.assertTrue(main.excluded({'name':'test', 'city':'Munich', 'source_region':'UAE'}))
        self.assertTrue(main.excluded({'name':'test', 'city':'Berlin'}))

    def test_score_is_written_and_updated_without_filtering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'job_tracker.xlsx'; page = Path(tmp)/'index.html'
            wb=Workbook(); ws=wb.active; ws.title='Jobs'
            ws.append(tracker.COLUMNS[:8]);ws.append(['','Old UAE','Dubai','Project Manager','Dubai','https://example.com/job/124','',''])
            wb.create_sheet('Abu Dhabi'); wb.save(path); wb.close()
            job = dict(self.candidate); matching.score_jobs([job],self.profile)
            unknown = dict(job, url='https://example.com/job/125', relevance_score=None, relevance_evidence='', relevance_notes='Job description unavailable or too short')
            tracker.update_tracker(path,[job,unknown],min_score=10,related_roles_enabled=False)
            tracker.update_tracker(path,[dict(job,relevance_score=2)],min_score=10,related_roles_enabled=False)
            wb=load_workbook(path)
            self.assertNotIn('Abu Dhabi',wb.sheetnames)
            self.assertEqual(wb['Jobs'].max_row,3)
            self.assertEqual(wb['Jobs'].cell(2,tracker.COLUMNS.index('Relevance Score (1-10)')+1).value,2)
            wb.close()
            website.generate(path,page)
            html=page.read_text(encoding='utf-8')
            self.assertIn('2/10',html);self.assertIn('Not scored',html);self.assertNotIn('Dubai',html)

    def test_pipeline_scores_only_after_match(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(main,'ROOT',Path(tmp)), \
             patch.object(main,'HEALTH_FILE',Path(tmp)/'health.json'), \
             patch.object(main,'scrape_company',return_value=[dict(self.candidate)]), \
             patch.object(main,'fetch_job_details',return_value=({},'')),patch.object(main.time,'sleep'):
            jobs,_,rows=main.scrape_all_any_city([{'name':'Test Employer','city':'Munich','careers_url':'https://example.com/jobs'}],self.profile)
        self.assertEqual(len(jobs),1)
        self.assertGreater(jobs[0]['relevance_score'],1)
        self.assertEqual(rows[0]['score_rejected'],0)

    def test_eu_greenhouse_uses_public_board(self):
        with patch.object(scrapers, 'scrape_html_board', return_value=[]) as reader:
            scrapers.scrape_greenhouse('enmacc','enmacc',eu=True)
        self.assertEqual(reader.call_args.args[1], 'https://job-boards.eu.greenhouse.io/enmacc')

    def test_eu_greenhouse_extracts_location_and_title_from_same_card(self):
        markup = '<a href="/constellrgmbh/jobs/123"><p class="body--medium">Space Program Manager</p><p class="body--metadata">Munich | Hybrid</p></a>'
        jobs = scrapers.extract_page(markup, 'https://job-boards.eu.greenhouse.io/constellrgmbh')
        self.assertEqual(len(jobs),1)
        self.assertEqual((jobs[0]['title'],jobs[0]['location']),('Space Program Manager','Munich | Hybrid'))

    def test_workday_rate_limit_pauses_details_for_same_host(self):
        from types import SimpleNamespace
        base = 'https://tenant.wd1.myworkdayjobs.com'
        scrapers.reset_headless_budget()
        self.addCleanup(scrapers.reset_headless_budget)
        with patch.object(scrapers.requests,'post',return_value=SimpleNamespace(status_code=429)) as post, patch.object(scrapers,'_safe_get') as get:
            result = scrapers.scrape_workday('Employer',base+'/Careers')
            self.assertEqual(result.pagination['completion'],'http_429')
            scrapers.scrape_workday('Employer',base+'/Careers')
            self.assertEqual(scrapers.fetch_job_details(base+'/Careers/job/Munich/Manager'),({},''))
            post.assert_called_once()
            get.assert_not_called()

    def test_affected_routes_are_included_in_audit(self):
        rows = {c['name']: c for c in main.load_yaml(main.COMPANIES_FILE)['companies']}
        for name in ('Constellr','yoummday GmbH','Munich Electrification','ARX Robotics'):
            self.assertEqual(rows[name]['verification_status'],'replacement_route_github_pending')

    def test_celonis_route_reads_feed_without_using_update_date(self):
        from types import SimpleNamespace
        from src.extraction import canonical_job_url
        rows = [dict(jobId=1, title='Project Manager', groupedLocation='Munich, Germany', updatedAt='2026-11-25'),
                dict(jobId=2, title='Program Manager', groupedLocation='Berlin, Germany')]
        company = next(c for c in main.load_yaml(main.COMPANIES_FILE)['companies'] if c['name']=='Celonis')
        with patch.object(scrapers,'_safe_get',return_value=SimpleNamespace(json=lambda: dict(jobs=rows,total=2))) as get:
            jobs = scrapers.scrape_company(company)
        get.assert_called_once_with('https://dxp-api.celonis.com/v1/jobs')
        self.assertEqual(jobs.pagination['completion'],'reported_total_reached')
        self.assertEqual(jobs[0]['location'],'Munich, Germany')
        self.assertFalse(jobs[0]['posted_date'])
        self.assertNotEqual(canonical_job_url(jobs[0]['url']),canonical_job_url(jobs[1]['url']))

    def test_celonis_incomplete_or_invalid_feed_is_not_marked_complete(self):
        from types import SimpleNamespace
        for payload, expected in ((dict(jobs=[],total=2),'feed_coverage_unverified'),
                                  (dict(jobs={}), 'invalid_feed')):
            with patch.object(scrapers,'_safe_get',return_value=SimpleNamespace(json=lambda: payload)):
                rows = scrapers.scrape_celonis('Celonis','https://careers.celonis.com/join-us/open-positions')
            self.assertEqual(rows.pagination['completion'],expected)

    def test_new_replacement_routes_are_in_existing_audit(self):
        companies = {c['name']: c for c in main.load_yaml(main.COMPANIES_FILE)['companies']}
        for name in ('Celonis','Fluence','TenneT','Linde'):
            self.assertEqual(companies[name]['verification_status'],'replacement_route_github_pending')
        self.assertIsNotNone(scrapers._WORKDAY_URL_RE.match(companies['Fluence']['careers_url']))
