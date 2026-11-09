"""Real-browser checks for delayed requests, failures, races, and loading accessibility.
Run separately: venv/bin/python tests/loading_smoke.py
All server data is temporary; no publishing worker or external API is started.
"""
import os
import fnmatch
import sys
import tempfile
import threading
from pathlib import Path
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from playwright.sync_api import sync_playwright,expect
    with tempfile.TemporaryDirectory(prefix='postdesk-loading-') as folder:
        root=Path(folder)
        os.environ.update(APP_ENV_FILE=str(root/'.env'),APP_DATA_DIR=str(root/'data'),APP_MEDIA_DIR=str(root/'media'),
            APP_LOG_DIR=str(root/'logs'),APP_BACKUP_DIR=str(root/'backups'),DB_PATH=str(root/'data/posts.db'),
            TOKEN_PATH=str(root/'data/token.json'),APP_PASSWORD='isolated-loading-password',COOKIE_SECURE='false',
            SMTP_HOST='',RESEND_API_KEY='',ALERT_TO='')
        from app import db,config,linkedin
        from app.dashboard import create_app
        from waitress import create_server
        app=create_app();linkedin.token_days_left=lambda:42;db.heartbeat()
        gates={};failures=set()
        from flask import request,jsonify
        @app.before_request
        def delayed_response():
            target=request.path+('?' + request.query_string.decode() if request.query_string else '')
            if any(fnmatch.fnmatch(target,pattern) for pattern in failures):
                return jsonify(error='Temporary service interruption'),500
            event=next((event for pattern,event in list(gates.items()) if fnmatch.fnmatch(target,pattern)),None)
            if event is not None and not event.wait(45):
                return jsonify(error='Test request gate timed out'),503
        pid=db.add_post('A customer conversation that changed our onboarding.',(datetime.now()+timedelta(days=1)).strftime(db.FMT),'scheduled')
        server=create_server(app,host='127.0.0.1',port=0)
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                chrome=os.getenv('CHROME_BIN','/usr/bin/google-chrome')
                browser=p.chromium.launch(executable_path=chrome if Path(chrome).exists() else None,headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1050})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                base=f'http://127.0.0.1:{server.effective_port}'
                def gate(pattern):gates[pattern]=threading.Event()
                def release(pattern):
                    event=gates.pop(pattern,None)
                    if event is not None:event.set()
                gate('**/api/session')
                page.goto(base)
                expect(page.locator('#boot-state')).to_be_visible()
                expect(page.locator('#login')).not_to_be_visible()
                page.screenshot(path='/tmp/postdesk-loading-boot.png')
                release('**/api/session')
                expect(page.locator('#login')).to_be_visible()
                page.locator('#password').fill(config.APP_PASSWORD)
                gate('**/api/login')
                page.locator('#login-form button').click()
                expect(page.locator('#login-form button')).to_have_text('Signing in…')
                expect(page.locator('#login-form button')).to_be_disabled()
                gate('**/api/overview')
                release('**/api/login')
                expect(page.locator('#view-overview')).to_have_attribute('aria-busy','true')
                expect(page.locator('#view-overview .page-skeleton')).to_be_visible()
                expect(page.locator('#view-overview .view-content')).not_to_be_visible()
                page.screenshot(path='/tmp/postdesk-loading-overview.png',full_page=True)
                release('**/api/overview')
                expect(page.locator('#view-overview')).to_have_attribute('aria-busy','false')
                expect(page.locator('#stat-scheduled')).to_have_text('1')
                for view,endpoint in [('queue','**/api/posts?*'),('calendar','**/api/calendar?*'),('activity','**/api/activity?*'),('settings','**/api/system')]:
                    gate(endpoint)
                    page.locator(f'.nav-item[data-view="{view}"]').click()
                    expect(page.locator(f'#view-{view} .page-skeleton')).to_be_visible()
                    expect(page.locator(f'#view-{view}')).to_have_attribute('aria-busy','true')
                    page.screenshot(path=f'/tmp/postdesk-loading-{view}.png',full_page=True)
                    release(endpoint)
                    expect(page.locator(f'#view-{view}')).to_have_attribute('aria-busy','false')
                    expect(page.locator(f'#view-{view} .page-skeleton')).not_to_be_visible()
                gate('**/api/overview')
                page.locator('.nav-item[data-view="import"]').click()
                expect(page.locator('#view-import .page-skeleton')).to_be_visible()
                release('**/api/overview')
                expect(page.locator('#import-file')).to_be_visible()
                # Cached navigation and background polling never replace content with skeletons.
                page.locator('.nav-item[data-view="overview"]').click()
                expect(page.locator('#view-overview')).to_have_attribute('aria-busy','false')
                gate('**/api/overview')
                page.evaluate('void refresh({background:true})')
                expect(page.locator('#refresh')).to_have_attribute('aria-busy','true')
                expect(page.locator('#view-overview .page-skeleton')).not_to_be_visible()
                expect(page.locator('#view-overview .view-content')).to_be_visible()
                release('**/api/overview')
                expect(page.locator('#refresh')).not_to_have_attribute('aria-busy','true')
                # A late failed response from a page that was left must not corrupt the new page.
                gate('**/api/posts?*')
                page.locator('.nav-item[data-view="queue"]').click()
                expect(page.locator('#view-queue')).to_have_attribute('aria-busy','true')
                page.locator('.nav-item[data-view="calendar"]').click()
                release('**/api/posts?*')
                expect(page.locator('#view-calendar')).to_have_attribute('aria-busy','false')
                expect(page.locator('#page-error')).not_to_be_visible()
                # Failed foreground refresh retains old content and provides a working retry.
                failures.add('**/api/system')
                page.locator('.nav-item[data-view="settings"]').click()
                expect(page.locator('#page-error')).to_contain_text('Temporary service interruption')
                expect(page.locator('#view-settings .view-content')).to_be_visible()
                expect(page.locator('#view-settings')).to_have_attribute('aria-busy','false')
                failures.discard('**/api/system')
                page.locator('#page-error button').click()
                expect(page.locator('#page-error')).not_to_be_visible()
                # First-load failures replace the skeleton with a dedicated retry card, not fake empty data.
                expect(page.locator('#view-settings')).to_have_attribute('aria-busy','false')
                page.evaluate('loading.reset()')
                failures.add('**/api/system')
                page.locator('.nav-item[data-view="settings"]').click()
                expect(page.locator('#view-settings .load-failure')).to_be_visible()
                expect(page.locator('#view-settings .page-skeleton')).not_to_be_visible()
                failures.discard('**/api/system')
                page.locator('#view-settings .load-failure button').click()
                expect(page.locator('#view-settings .load-failure')).not_to_be_visible()
                expect(page.locator('#view-settings .view-content')).to_be_visible()
                # Detail requests get a cancellable skeleton; cancellation cannot open a late editor.
                page.locator('.nav-item[data-view="queue"]').click()
                expect(page.locator('.post-row')).to_have_count(1)
                detail=f'**/api/posts/{pid}'
                gate(detail)
                page.locator('.post-title').click()
                expect(page.locator('#post-loader')).to_be_visible()
                page.locator('#cancel-post-load').click()
                release(detail)
                expect(page.locator('#post-loader')).not_to_be_visible()
                expect(page.locator('#editor')).not_to_be_visible()
                # Save action: an honest spinner, duplicate-submission guard, and preserved edits.
                page.locator('#new-post').click()
                page.locator('#post-text').fill('A draft we want to preserve while the network is slow.')
                failures.add('**/api/posts')
                page.get_by_role('button',name='Save draft',exact=True).click()
                expect(page.locator('#editor-error')).to_contain_text('Temporary service interruption')
                expect(page.locator('#post-text')).to_be_enabled()
                expect(page.locator('#post-text')).to_have_value('A draft we want to preserve while the network is slow.')
                expect(page.locator('#post-form')).to_have_attribute('aria-busy','false')
                failures.discard('**/api/posts')
                gate('**/api/posts')
                page.get_by_role('button',name='Save draft',exact=True).click()
                expect(page.locator('#post-form')).to_have_attribute('aria-busy','true')
                expect(page.locator('#post-form button[value="draft"]')).to_have_text('Saving draft…')
                page.keyboard.press('Escape')
                expect(page.locator('#editor')).to_be_visible()
                expect(page.locator('#post-text')).to_have_value('A draft we want to preserve while the network is slow.')
                page.evaluate("document.querySelector('#post-form').requestSubmit(document.querySelector('#post-form button[value=draft]'))")
                release('**/api/posts')
                expect(page.locator('#editor')).not_to_be_visible()
                assert len([post for post in db.all_posts() if post['status']=='draft'])==1
                # Import progress is indeterminate and follows the real pending request.
                page.locator('.nav-item[data-view="import"]').click()
                page.locator('#import-file').set_input_files({'name':'drafts.csv','mimeType':'text/csv','buffer':b'text\nAn imported idea\n'})
                gate('**/api/import')
                page.locator('#import-form button[type=submit]').click()
                expect(page.locator('#import-progress')).to_be_visible()
                expect(page.locator('#import-progress [role=progressbar]')).not_to_have_attribute('aria-valuenow')
                expect(page.locator('#import-form button')).to_have_text('Importing posts…')
                page.locator('.nav-item[data-view="activity"]').click()
                release('**/api/import')
                expect(page.locator('#import-form')).to_have_attribute('aria-busy','false')
                expect(page.locator('#view-activity')).to_be_visible()
                expect(page.locator('#import-progress')).not_to_be_visible()
                page.locator('.nav-item[data-view="queue"]').click()
                # Icon-only delete controls keep a compact spinner and an accessible label.
                expect(page.locator('#view-queue')).to_have_attribute('aria-busy','false')
                draft=next(post for post in db.all_posts() if post['text'].startswith('A draft we want'))
                delete_path=f'**/api/posts/{draft["id"]}'
                gate(delete_path)
                page.once('dialog',lambda dialog:dialog.accept())
                delete_button=page.get_by_role('button',name='Delete post #'+str(draft['id']),exact=True)
                delete_button.click()
                expect(delete_button.locator('.action-spinner')).to_be_visible()
                expect(delete_button).to_be_disabled()
                release(delete_path)
                expect(page.locator('.post-row')).to_have_count(2)
                # Advance only the browser clock: a stalled request must time out and release its busy state.
                page.clock.install()
                gate('**/api/overview')
                page.locator('#refresh').click()
                expect(page.locator('#refresh')).to_have_attribute('aria-busy','true')
                page.clock.fast_forward(21000)
                expect(page.locator('#page-error')).to_contain_text('taking too long')
                expect(page.locator('#refresh')).to_be_enabled()
                release('**/api/overview')
                page.locator('#page-error button').click()
                expect(page.locator('#page-error')).not_to_be_visible()
                # Reduced-motion mode leaves a static skeleton and no horizontal overflow on mobile.
                expect(page.locator('#view-queue')).to_have_attribute('aria-busy','false')
                page.emulate_media(reduced_motion='reduce')
                page.set_viewport_size({'width':390,'height':844})
                page.evaluate("loading.reset()")
                gate('**/api/overview')
                page.locator('#menu-toggle').click();page.locator('.nav-item[data-view="calendar"]').click()
                expect(page.locator('#view-calendar .page-skeleton')).to_be_visible()
                assert page.locator('#view-calendar .sk').first.evaluate("e=>getComputedStyle(e,'::after').animationName")=='none'
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path='/tmp/postdesk-loading-mobile.png',full_page=True)
                release('**/api/overview')
                expect(page.locator('#view-calendar')).to_have_attribute('aria-busy','false')
                assert not errors,errors
                browser.close()
                print('PASS: session boot, all six page skeletons, cached/background refresh, cancellation/races, retry, detail loader, save/import actions, duplicate guard, reduced motion, mobile layout; no JS errors')
        finally:
            for event in gates.values():event.set()
            server.close();thread.join(timeout=5)


if __name__=='__main__':main()
