"""Read-only HTTP audit of all configured UAE career pages."""
import csv
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
import requests
import yaml
from bs4 import BeautifulSoup


def classify(code, url, title, text):
    if code in (401, 403, 429, 999):
        return 'blocked_or_rate_limited'
    if code in (404, 410):
        return 'not_found'
    if code != 200:
        return 'http_error'
    if any(word in title.casefold() for word in ('not found', '404', 'page unavailable')):
        return 'possible_soft_404'
    if any(word in (title+' '+text[:2000]).casefold() for word in ('request blocked by sandbox policy', 'verify you are human', 'access denied', 'checking your browser')):
        return 'blocked_or_challenge'
    if any(word in urlparse(url).path.casefold().split('/') for word in ('login', 'signin', 'sign-in')):
        return 'sign_in_page'
    return 'reachable_extraction_unverified'


def check(board):
    url = board['careers_url']
    row = dict(employer=board['name'], adapter=board.get('ats', ''), requested_url=url,
               final_url='', http_status='', result='', page_title='', notes='')
    try:
        with requests.get(url, timeout=(5, 9), stream=True, allow_redirects=True) as r:
            probe = b''
            if r.status_code == 200:
                for chunk in r.iter_content(16384):
                    probe += chunk
                    if len(probe) >= 131072:
                        break
            soup = BeautifulSoup(probe, 'html.parser')
            title = soup.title.get_text(' ', strip=True) if soup.title else ''
            row.update(final_url=r.url, http_status=r.status_code, page_title=title[:200],
                       result=classify(r.status_code, r.url, title, soup.get_text(' ', strip=True)),
                       notes='Redirected; confirm destination' if r.history else '')
    except requests.exceptions.Timeout:
        row.update(result='timeout')
    except requests.exceptions.RequestException as exc:
        row.update(result='connection_or_redirect_error', notes=str(exc)[:240])
    return row


def main():
    boards = yaml.safe_load(Path('config/job_boards.yaml').read_bytes())['companies']
    boards = [b for b in boards if b.get('city') == 'UAE']
    if not boards:
        raise SystemExit('No UAE boards configured')
    target = Path('diagnostics/uae-board-url-audit.csv')
    target.parent.mkdir(exist_ok=True)
    totals = Counter()
    with ThreadPoolExecutor(max_workers=8) as pool, target.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = None
        for row in pool.map(check, boards):
            if writer is None:
                writer = csv.DictWriter(stream, fieldnames=row.keys())
                writer.writeheader()
            writer.writerow({k: "'"+v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for k,v in row.items()})
            totals[row['result']] += 1
    for status, count in sorted(totals.items()):
        print(f'{status}: {count}')
    print('URL results are not proof of job extraction or full vacancy coverage.')


if __name__ == '__main__':
    main()
