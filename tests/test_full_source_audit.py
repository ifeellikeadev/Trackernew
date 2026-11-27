import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from src import main, scrapers
from tools import full_source_audit as audit


class SourceAudit(TestCase):
    def test_all_companies_assigned_once_and_shared_board_stays_together(self):
        companies=main.load_yaml(main.COMPANIES_FILE)['companies']
        shards=audit.split_sources(companies,8)
        self.assertCountEqual([c['name'] for shard in shards for c in shard],[c['name'] for c in companies])
        self.assertTrue(any({'Serviceplan Group','Mediaplus'} <= {c['name'] for c in shard} for shard in shards))

    def test_merge_reports_missing_shard_instead_of_silent_success(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); part=root/'part';part.mkdir()
            (part/'audit-rows.json').write_text(json.dumps([{'company':'One','diagnosis':'FILTERED_RESULTS','status':'NO_MATCHES'}]))
            conf=[{'name':name,'careers_url':'https://example.org/'+name} for name in ('One','Two')]
            output=root/'output'
            self.assertEqual(audit.merge(conf,part,output),1)
            self.assertIn('SHARD_RESULT_MISSING',(output/'company-audit.csv').read_text(encoding='utf-8-sig'))

    def test_sap_listing_captures_location_and_date_from_same_row(self):
        html='<table><tr class="data-row"><td><a class="jobTitle-link" href="/job/Project-Manager/123/">Project Manager</a></td><td><span class="jobLocation">München, DE</span></td><td><span class="jobDate">2026-11-25</span></td></tr></table>'
        for url in ('https://careers.knorr-bremse.com/search/','https://jobs.telefonica.com/TelefonicaGermany/go/Jobs/9327101/'):
            jobs=scrapers.extract_page(html,url)
            self.assertEqual(len(jobs),1)
            self.assertEqual(jobs[0]['location'],'München, DE')
            self.assertEqual(jobs[0]['posted_date'],'2026-11-25')
