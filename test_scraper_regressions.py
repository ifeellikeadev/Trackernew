import json
import unittest
from unittest.mock import Mock, patch
from src import ats_scrapers as ats
from src import matcher

def response(data):
    r = Mock(status_code=200)
    r.json.return_value = data
    return r

class ScraperTests(unittest.TestCase):
    def test_smartrecruiters_string_ref_pagination(self):
        pages = [response({"totalFound": 2, "content": [{"id": "123", "name": "Project Manager", "ref": "https://api.smartrecruiters.com/detail/123", "location": {"city": "Munich"}}]}), response({"totalFound": 2, "content": [{"id": "456", "name": "Program Manager", "ref": "https://api.smartrecruiters.com/detail/456"}]})]
        with patch.object(ats, "_safe_get", side_effect=pages) as get, patch.object(ats.time, "sleep"):
            jobs = ats.scrape_smartrecruiters("Example", "example")
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["url"], "https://jobs.smartrecruiters.com/example/123")
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 1)
    def test_smartrecruiters_invalid_response(self):
        with patch.object(ats, "_safe_get", return_value=response(["bad"])):
            self.assertEqual(ats.scrape_smartrecruiters("Example"), [])
    def test_workday_site_with_and_without_locale(self):
        for url in ("https://example.wd5.myworkdayjobs.com/External/job/Munich/PM_123", "https://example.wd5.myworkdayjobs.com/en-US/External/job/Munich/PM_123"):
            self.assertEqual(ats._WORKDAY_URL_RE.match(url).groups(), ("example", "wd5", "External"))
    def test_workday_pagination(self):
        pages = [response({"total": 2, "jobPostings": [{"title": "Project Manager", "locationsText": "Munich", "externalPath": "/job/Munich/PM_123"}]}), response({"total": 2, "jobPostings": [{"title": "Program Manager", "locationsText": "Zurich", "externalPath": "/job/Zurich/PM_456"}]})]
        with patch.object(ats.requests, "post", side_effect=pages) as post, patch.object(ats.time, "sleep"):
            jobs = ats.scrape_workday("Example", "https://example.wd5.myworkdayjobs.com/External/job/Munich/PM_123")
        self.assertEqual(len(jobs), 2)
        self.assertIn("/wday/cxs/example/External/jobs", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["json"]["offset"], 1)
    def test_navigation_is_not_a_job(self):
        jobs = ats._extract_jobs("<nav><a href='/jobs'>Career opportunities</a></nav><a href='/jobs'>Search jobs</a><a href='job/123'>Project Manager</a>", "https://example.com/careers/")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://example.com/careers/job/123")
    def test_structured_location(self):
        value = {"@type": "JobPosting", "title": "Project Manager", "url": "/job/123", "jobLocation": {"address": {"addressLocality": "Munich"}}, "description": "<p>Budget</p>"}
        jobs = ats._extract_jobs('<script type="application/ld+json">' + json.dumps(value) + '</script>', "https://example.com/")
        self.assertEqual(jobs[0]["location"], "Munich")
        self.assertEqual(jobs[0]["description"], "Budget")
    def test_auto_does_not_guess_tokens(self):
        with patch.object(ats, "scrape_greenhouse") as gh, patch.object(ats, "scrape_generic", return_value=[]), patch.object(ats, "_recover_thin_result", return_value=[]):
            ats.scrape_company({"name": "Flip", "careers_url": "https://example.com/careers", "ats": "auto"})
        gh.assert_not_called()
    def test_eu_greenhouse_discovery(self):
        self.assertEqual(ats._PLATFORM_URL_PATTERNS["greenhouse"].search("https://job-boards.eu.greenhouse.io/example").group(1), "example")
    def test_missing_comma(self):
        self.assertEqual(matcher.find_matching_city("Freising"), "Munich")
        self.assertEqual(matcher.find_matching_city("Vaterstetten"), "Munich")
    def test_city_substring_false_positive(self):
        self.assertIsNone(matcher.find_matching_city("Bernau"))
        self.assertIsNone(matcher.find_matching_city("Haarlem"))
    def test_location_status_no_name_error(self):
        self.assertEqual(matcher.location_status("Basel", "Munich"), "mismatch")
        self.assertEqual(matcher.location_status("Remote", "Munich"), "unconfirmed")
    def test_headless_keeps_dom_on_timeout(self):
        import sys, types
        class Timeout(Exception): pass
        page = Mock(url="https://example.com/")
        page.goto.side_effect = Timeout("fixture timeout")
        page.content.return_value = "<a href='/job/123'>Project Manager</a>"
        browser = Mock(); browser.new_page.return_value = page
        pw = Mock(); pw.chromium.launch.return_value = browser
        context = Mock(); context.__enter__ = Mock(return_value=pw); context.__exit__ = Mock(return_value=False)
        module = types.ModuleType("playwright.sync_api"); module.sync_playwright = Mock(return_value=context); module.TimeoutError = Timeout
        with patch.dict(sys.modules, {"playwright.sync_api": module}), patch.object(ats, "_headless_time_used", 0):
            jobs = ats.scrape_headless("Example", "https://example.com/")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(page.goto.call_args.kwargs["wait_until"], "domcontentloaded")
        browser.close.assert_called_once()
