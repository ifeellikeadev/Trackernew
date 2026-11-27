from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from openpyxl import load_workbook
from src import matching, main, scrapers, tracker

DESC = ('Lead cross functional project delivery and manage governance, suppliers and risks. '
        'Create schedules, coordinate milestones, manage budgets and report progress to senior stakeholders.')

class MetadataRepair(TestCase):
    def test_existing_row_backfill_and_preserve_score(self):
        today = matching.today_local().isoformat()
        original = dict(title='Project Manager',location='Munich',city='Munich',company='Employer',url='https://example.org/jobs/1?utm_source=old')
        with TemporaryDirectory() as temporary:
            path = Path(temporary)/'tracker.xlsx'
            tracker.update_tracker(path,[original])
            refreshed = dict(original,url='https://example.org/jobs/1',posted_date=today,relevance_score=7,relevance_evidence='Delivery')
            result = tracker.update_tracker(path,[refreshed])
            self.assertEqual((result['added'],result['dates_backfilled'],result['scores_backfilled']),(0,1,1))
            tracker.update_tracker(path,[dict(original,relevance_score=None,relevance_evidence='')])
            wb = load_workbook(path)
            self.assertEqual((wb.active['A2'].value,wb.active['I2'].value,wb.active['J2'].value,wb.active.max_row),(today,7,'Delivery',2))
            wb.close()

    def test_only_description_from_vacancy_and_explicit_date(self):
        url='https://example.org/jobs/1'
        d=matching.today_local().isoformat()
        html=f'<main><h1>Project Manager</h1><h2>Responsibilities</h2><p>{DESC}</p><p>Posted on: {d}</p><footer><p>Posted on: 2020-01-01</p></footer></main>'
        with patch.object(scrapers,'_safe_get',return_value=SimpleNamespace(url=url,text=html)):
            detail,_=scrapers.fetch_job_details(url)
        self.assertEqual(detail['posted_date'],d)
        self.assertEqual(detail['description'],DESC)
        self.assertIsNotNone(matching.score_jobs([detail],{'relevance':{}})[0]['relevance_score'])

    def test_do_not_use_update_date_or_landing_page_text(self):
        url='https://example.org/jobs/1'
        html=f'<main><h1>Page not found</h1><time datetime="2026-01-01">Updated</time><p>{DESC}</p></main>'
        with patch.object(scrapers,'_safe_get',return_value=SimpleNamespace(url=url,text=html)):
            self.assertEqual(scrapers.fetch_job_details(url),({},''))
        self.assertIsNone(matching.publication_date({'posted_date':'2026-11-20','_date_kind':'update'}))

    def test_replace_invalid_listing_date(self):
        today=matching.today_local()
        job={'posted_date':'unparseable','_date_kind':'creation_unverified'}
        main.enrich(job,{'posted_date':today.isoformat(),'_date_kind':'publication'})
        self.assertEqual(matching.publication_date(job),today)
        self.assertEqual(matching.filter_recent_jobs([{'posted_date':(today+timedelta(days=1)).isoformat()}])[0]['posted_date'],'')

    def test_bsh_publication_column(self):
        html='<table><tr><th>Title</th><th>Locations</th><th>Publication date</th></tr><tr><td><a href="/123-project-manager/en/job.html">Project Manager</a></td><td>Munich</td><td>2026-11-19</td></tr></table>'
        jobs=scrapers.extract_page(html,'https://jobs.bsh-group.de/index/de')
        self.assertEqual([(j['title'],j['location'],j['posted_date']) for j in jobs],[('Project Manager','Munich','2026-11-19')])

    def test_only_safe_public_search_form(self):
        host='https://jobs.man.eu/'
        good='<form action="/search/" method="get"><input name="q"></form>'
        self.assertEqual(scrapers.sap_search_form(good,host),(host+'search/','get',[('q','')]))
        self.assertIsNone(scrapers.sap_search_form(good.replace('get','post'),host))
        self.assertIsNone(scrapers.sap_search_form(good.replace('/search/','https://other.example/search/'),host))

    def test_matching_detail_api_fallback_without_rating_a_guess(self):
        url='https://example.org/jobs/1'
        company={'name':'Employer','city':'Munich','careers_url':'https://example.org/jobs/','source_seconds':5}
        record={'title':'Project Manager','location':'Munich','url':url,'_detail_api_url':url+'/api','posted_date':''}
        for blocked in (False,True):
            with TemporaryDirectory() as tmp, patch.object(main,'ROOT',Path(tmp)), patch.object(main,'HEALTH_FILE',Path(tmp)/'health.json'), patch.object(main,'scrape_company',return_value=[record.copy()]), patch.object(main,'detail_access_blocked',return_value=blocked), patch.object(main,'fetch_job_details',side_effect=lambda u: ({'description':DESC,'posted_date':matching.today_local().isoformat()},'') if u==url else ({},'')) as fetch:
                jobs,_,reports=main.scrape_all_any_city([company],{'title_must_match':['project manager']})
            self.assertEqual(len(jobs),1)
            self.assertEqual(fetch.call_count,1 if blocked else 2)
            self.assertEqual(reports[0]['missing_both'],int(blocked))
