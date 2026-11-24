import os
import re
import json
import subprocess
from datetime import datetime, timezone, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
from weekend_defaults import DEFAULT_HOME_PLAYS, DEFAULT_KIDS_MEALS
from enrich_with_kakao_gemini import enrich_weekend_data

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, 'docs')
TEMPLATES_DIR = os.path.join(REPO_ROOT, 'templates')
MASTER_HTML_PATH = os.path.join(TEMPLATES_DIR, 'master_dashboard.html')
FOLDER_ID = '19jx-SYkJqEz02VG4iLLSzPupxhluBLfN'  # dashboard_incoming/weekend-curator
KST = timezone(timedelta(hours=9))


def get_target_weekend_dates(now_kst):
    days_until_sat = (5 - now_kst.weekday()) % 7
    if days_until_sat == 0 and now_kst.weekday() != 5:
        days_until_sat = 7
    sat = now_kst + timedelta(days=days_until_sat)
    sun = sat + timedelta(days=1)
    return f"{sat.strftime('%m.%d')}~{sun.strftime('%m.%d')}"


def resolve_child_ages(now_kst):
    girl_by = os.environ.get('GIRL_BIRTH_YEAR', '').strip()
    boy_by = os.environ.get('BOY_BIRTH_YEAR', '').strip()
    girl_age = (now_kst.year - int(girl_by) + 1) if girl_by.isdigit() else 7
    boy_age = (now_kst.year - int(boy_by) + 1) if boy_by.isdigit() else 3
    return girl_age, boy_age


def is_within_friday_grace_window(now_kst):
    """Wait 1h 25m (16:00~17:25 KST) on Friday schedule runs for Gemini App upload."""
    if os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch':
        return False
    hm = now_kst.hour * 60 + now_kst.minute
    return now_kst.weekday() == 4 and (960 <= hm < 1045)


def last_commit_epoch(filepath):
    try:
        out = subprocess.check_output(
            ['git', 'log', '-1', '--format=%ct', '--', filepath],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return int(out) if out else 0
    except Exception:
        return 0


def pick_target_file(files, now_kst):
    is_dispatch = os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    if is_dispatch:
        return files[0]
    min_date = (now_kst - timedelta(days=2)).strftime('%Y-%m-%d')
    for f in files:
        date_part = f['name'].replace('-weekend.json', '')
        if date_part >= min_date:
            return f
    return None


def main():
    sa_json = os.environ.get('GDRIVE_SA_KEY')
    if not sa_json:
        raise ValueError("GDRIVE_SA_KEY environment variable is missing.")

    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json),
        scopes=['https://www.googleapis.com/auth/drive.readonly'],
    )
    service = build('drive', 'v3', credentials=creds)

    res = service.files().list(
        q=f"'{FOLDER_ID}' in parents and name contains '-weekend.json' and trashed = false",
        orderBy='modifiedTime desc',
        pageSize=5,
        fields='files(id, name, modifiedTime)',
    ).execute()
    files = res.get('files', [])
    if not files:
        raise FileNotFoundError("No *-weekend.json found in dashboard_incoming/weekend-curator.")

    now_kst = datetime.now(KST)
    target = pick_target_file(files, now_kst)
    if not target:
        if is_within_friday_grace_window(now_kst):
            print("Waiting for this week's Gemini App upload (*-weekend.json) within grace window...")
            return
        raise FileNotFoundError("Missing this week's *-weekend.json in dashboard_incoming/weekend-curator.")

    date_part = target['name'].replace('-weekend.json', '')
    month_part = date_part[:7]
    out_dir = os.path.join(DOCS_DIR, month_part)
    out_file = os.path.join(out_dir, f"{date_part}.html")

    if os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' and os.path.exists(out_file):
        mod_str = target.get('modifiedTime', '')
        drive_epoch = 0
        if mod_str:
            try:
                drive_epoch = int(datetime.fromisoformat(mod_str.replace('Z', '+00:00')).timestamp())
            except Exception:
                drive_epoch = 0
        if drive_epoch and drive_epoch <= last_commit_epoch(out_file):
            print(f"Weekly dashboard already up to date at {out_file}; skipping.")
            return

    print(f"Fetching {target['name']} (ID: {target['id']})...")
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=target['id']))
    done = False
    while not done:
        _, done = downloader.next_chunk()

    data = json.loads(buf.getvalue().decode('utf-8'))
    girl_age, boy_age = resolve_child_ages(now_kst)
    weekend_label = get_target_weekend_dates(now_kst)

    if not data.get('homePlays'):
        data['homePlays'] = [dict(p) for p in DEFAULT_HOME_PLAYS]
    if not data.get('kidsMeals'):
        data['kidsMeals'] = [dict(m) for m in DEFAULT_KIDS_MEALS[:6]]
    elif len(data['kidsMeals']) > 6:
        data['kidsMeals'] = data['kidsMeals'][:6]

    data = enrich_weekend_data(data, girl_age, boy_age)

    with open(MASTER_HTML_PATH, 'r', encoding='utf-8') as f:
        tpl = f.read()

    html = tpl.replace('{{WEEKEND_LABEL}}', weekend_label)
    html = html.replace('{{GIRL_AGE}}', str(girl_age))
    html = html.replace('{{BOY_AGE}}', str(boy_age))
    html = html.replace('{{WEATHER_PILLS_HTML}}', data['weatherPillsHtml'])
    html = html.replace('{{SPOTS_JSON}}', json.dumps(data['spots'], ensure_ascii=False))
    html = html.replace('{{HOME_PLAYS_JSON}}', json.dumps(data['homePlays'], ensure_ascii=False))
    html = html.replace('{{KIDS_MEALS_JSON}}', json.dumps(data['kidsMeals'], ensure_ascii=False))
    if not re.search(r'<meta\s+name=["\']robots["\']', html, re.IGNORECASE):
        html = re.sub(r'(<head[^>]*>)', r'\1\n  <meta name="robots" content="noindex, nofollow" />', html, count=1, flags=re.IGNORECASE)

    os.makedirs(out_dir, exist_ok=True)
    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Generated: {out_file}")


if __name__ == '__main__':
    main()
