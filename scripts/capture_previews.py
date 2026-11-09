"""Capture README screenshots using an explicitly fictional, isolated demo.

Run: venv/bin/python scripts/capture_previews.py
Requires requirements-dev.txt and Chrome/Chromium. No real token, worker,
publishing request, or production database is used. Connection state is simulated.
"""
import os
import sys
import tempfile
import threading
from datetime import datetime,timedelta
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
FOUNDER_POST=(
    'This week, we watched new customers try our onboarding.\n\n'
    'Four conversations revealed the same sticking point. So we removed two steps '
    'and rewrote the first screen.\n\n'
    'A useful reminder: the next improvement might already be in your support inbox.\n\n'
    'What have your customers taught you this week?'
)


def main():
    from playwright.sync_api import sync_playwright,expect
    with tempfile.TemporaryDirectory(prefix='postdesk-preview-') as folder:
        runtime=Path(folder)
        os.environ.update(APP_ENV_FILE=str(runtime/'.env'),APP_DATA_DIR=str(runtime/'data'),
            APP_MEDIA_DIR=str(runtime/'media'),APP_LOG_DIR=str(runtime/'logs'),APP_BACKUP_DIR=str(runtime/'backups'),
            DB_PATH=str(runtime/'data/posts.db'),TOKEN_PATH=str(runtime/'data/token.json'),
            APP_PASSWORD='isolated-demo-password',COOKIE_SECURE='false',RESEND_API_KEY='',SMTP_HOST='',ALERT_TO='')
        from app import config,db,linkedin
        from app.dashboard import create_app
        from waitress import create_server
        # Explicit demo fixture, not a credential or a claim about a real LinkedIn account.
        linkedin.token_days_left=lambda:42
        app=create_app()
        now=datetime.now()
        published=[
            'Our first ten customer conversations changed our product roadmap. Here are the three questions we keep asking.',
            'A small onboarding improvement we nearly overlooked: showing people their next step instead of another empty dashboard.',
            'Building in public means sharing the small decisions too. This week, we chose clarity over one more feature.',
            'Customer support is part of product discovery. We now review recurring questions before planning our next sprint.',
            'We started writing release notes for the people using the product, rather than the people building it. The difference is worth the effort.',
            'A Friday reflection: shipping a smaller improvement and learning from real use helped us make a better decision for next week.',
        ]
        future=[FOUNDER_POST,
            'A question for other founders: how do you decide which customer requests deserve a place on your roadmap?',
            'Behind this week’s release: a faster first-run experience, clearer labels, and one less setting to configure.',
            'Our monthly product review starts with what customers found confusing. Here is how we turn those notes into improvements.']
        with db.conn() as c:
            for index,text in enumerate(published):
                stamp=(now-timedelta(days=12-index*2)).replace(hour=9,minute=0,second=0).strftime(db.FMT)
                cur=c.execute('INSERT INTO posts(text,status,scheduled_at,posted_at,created_at) VALUES(?,?,?,?,?)',
                    (text,'posted',stamp,stamp,stamp))
                c.execute('INSERT INTO audit_events(action,post_id,detail,actor,created_at) VALUES(?,?,?,?,?)',
                    ('attempt.posted',cur.lastrowid,'Published to your LinkedIn profile','worker',stamp))
            for index,text in enumerate(future):
                stamp=(now+timedelta(days=1+index*2)).replace(hour=9,minute=0,second=0).strftime(db.FMT)
                c.execute('INSERT INTO posts(text,status,scheduled_at) VALUES(?,?,?)',(text,'scheduled',stamp))
            for text in ['The customer interview questions we keep coming back to.',
                         'Three lessons from simplifying our pricing page.',
                         'A look inside our weekly product review.']:
                c.execute("INSERT INTO posts(text,status) VALUES(?,'draft')",(text,))
        db.heartbeat()
        stop=threading.Event()
        def heartbeat():
            while not stop.wait(1):db.heartbeat()
        pulse=threading.Thread(target=heartbeat,daemon=True)
        server=create_server(app,host='127.0.0.1',port=0)
        pulse.start()
        serving=threading.Thread(target=server.run,daemon=True);serving.start()
        try:
            with sync_playwright() as p:
                chrome=os.getenv('CHROME_BIN','/usr/bin/google-chrome')
                browser=p.chromium.launch(executable_path=chrome if Path(chrome).exists() else None,headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.effective_port}')
                page.locator('#password').fill(config.APP_PASSWORD)
                page.locator('#login-form button').click()
                expect(page.locator('#workspace')).to_be_visible()
                expect(page.locator('#stat-posted')).to_have_text('6')
                # Keep the illustrative activity feed focused on publishing, not this capture login.
                with db.conn() as c:
                    c.execute("DELETE FROM audit_events WHERE action LIKE 'session.%'")
                page.locator('#refresh').click()
                expect(page.locator('#recent-events .event-row').first).to_contain_text('Post published')
                expect(page.locator('#worker-indicator')).to_have_text('Worker connected')
                expect(page.locator('#worker-badge')).to_have_text('online')
                expect(page.locator('#notice')).to_be_hidden()
                expect(page.locator('#stat-posted')).to_have_text('6')
                expect(page.locator('#stat-scheduled')).to_have_text('4')
                expect(page.locator('#stat-draft')).to_have_text('3')
                expect(page.locator('#stat-attention')).to_have_text('0')
                expect(page.locator('#next-post')).to_contain_text('Four conversations revealed the same sticking point.')
                output=PROJECT/'docs'
                page.screenshot(path=str(output/'dashboard-preview.png'),full_page=True)
                page.locator('#new-post').click()
                page.locator('#post-text').fill(FOUNDER_POST)
                expect(page.locator('#preview-text')).to_have_text(FOUNDER_POST)
                page.screenshot(path=str(output/'composer-preview.png'),full_page=True)
                page.once('dialog',lambda dialog:dialog.accept())
                page.locator('#close-editor').click()
                page.locator('.nav-item[data-view="calendar"]').click()
                expect(page.locator('.calendar-day')).to_have_count(42)
                page.screenshot(path=str(output/'calendar-preview.png'),full_page=True)
                assert not errors,errors
                browser.close()
                print('Captured healthy demo overview (6 published / 4 scheduled / 3 drafts), founder-style composer, and calendar. No external API calls or live data.')
        finally:
            stop.set();pulse.join(timeout=5)
            server.close();serving.join(timeout=5)


if __name__=='__main__':main()
