"""Optional end-to-end checks: pip install -r requirements-dev.txt; python tests/browser_smoke.py.
Uses temporary storage and a local Waitress server; never starts a publishing worker.
"""
import os
import io
import sys
import tempfile
import threading
from datetime import datetime,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from playwright.sync_api import sync_playwright,expect
    with tempfile.TemporaryDirectory(prefix='postdesk-browser-') as folder:
        root=Path(folder)
        os.environ.update(APP_DATA_DIR=str(root/'data'),APP_MEDIA_DIR=str(root/'media'),APP_LOG_DIR=str(root/'logs'),
            APP_BACKUP_DIR=str(root/'backups'),DB_PATH=str(root/'data/posts.db'),TOKEN_PATH=str(root/'data/token.json'),
            APP_PASSWORD='browser-only-test-password',SMTP_HOST='',ALERT_TO='',COOKIE_SECURE='false')
        from app import config,db
        from app.dashboard import create_app
        from waitress import create_server
        server=create_server(create_app(),host='127.0.0.1',port=0)
        port=server.effective_port
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                executable=os.getenv('CHROME_BIN','/usr/bin/google-chrome')
                browser=p.chromium.launch(executable_path=executable if Path(executable).exists() else None,headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1050})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}')
                expect(page.locator('#login')).to_be_visible()
                page.screenshot(path='/tmp/postdesk-login.png',full_page=True)
                page.locator('#password').fill(config.APP_PASSWORD)
                page.get_by_role('button',name='Open your workspace').click()
                expect(page.locator('#workspace')).to_be_visible()
                page.get_by_role('button',name='Create post',exact=True).click()
                page.locator('#post-text').fill('A thoughtful update from our end-to-end browser check.')
                from PIL import Image
                image_buffer=io.BytesIO();Image.new('RGB',(24,24),'#3568ed').save(image_buffer,format='PNG')
                page.locator('#post-image').set_input_files({'name':'test.png','mimeType':'image/png','buffer':image_buffer.getvalue()})
                page.locator('#alt-text').fill('A blue test square')
                expect(page.locator('#preview-image')).to_be_visible()
                expect(page.locator('#preview-text')).to_have_text('A thoughtful update from our end-to-end browser check.')
                page.screenshot(path='/tmp/postdesk-composer.png',full_page=True)
                page.get_by_role('button',name='Save draft',exact=True).click()
                expect(page.locator('#editor')).not_to_be_visible()
                page.locator('.nav-item[data-view="queue"]').click()
                expect(page.locator('.post-row')).to_have_count(1)
                page.locator('.post-title').click()
                when=(datetime.now()+timedelta(days=1)).strftime('%Y-%m-%dT09:00')
                page.locator('#post-date').fill(when)
                page.get_by_role('button',name='Schedule post',exact=False).click()
                expect(page.locator('#editor')).not_to_be_visible()
                for n in range(24):db.add_post('Pagination draft '+str(n))
                page.locator('.nav-item[data-view="overview"]').click()
                expect(page.locator('#stat-scheduled')).to_have_text('1')
                expect(page.locator('#stat-draft')).to_have_text('24')
                page.screenshot(path='/tmp/postdesk-desktop.png',full_page=True)
                page.get_by_role('button',name='Pause publishing',exact=True).click()
                expect(page.locator('#pause-publishing')).to_have_text('Resume publishing')
                assert db.controls()['paused']
                page.get_by_role('button',name='Resume publishing',exact=True).click()
                expect(page.locator('#pause-publishing')).to_have_text('Pause publishing')
                page.locator('.nav-item[data-view="queue"]').click()
                expect(page.locator('.post-row')).to_have_count(20)
                page.locator('#queue-next').click()
                expect(page.locator('.post-row')).to_have_count(5)
                page.locator('#search').fill('Pagination draft 23')
                expect(page.locator('.post-row')).to_have_count(1)
                page.locator('.post-title').click()
                page.locator('#post-text').fill('An unsaved idea worth preserving')
                page.once('dialog',lambda dialog:dialog.dismiss())
                page.locator('#close-editor').click()
                expect(page.locator('#editor')).to_be_visible()
                expect(page.locator('#post-text')).to_have_value('An unsaved idea worth preserving')
                # Polling must not overwrite the active editor.
                page.evaluate('refresh()')
                expect(page.locator('#post-text')).to_have_value('An unsaved idea worth preserving')
                page.once('dialog',lambda dialog:dialog.accept())
                page.locator('#close-editor').click()
                expect(page.locator('#editor')).not_to_be_visible()
                page.locator('.nav-item[data-view="calendar"]').click()
                expect(page.locator('.calendar-day')).to_have_count(42)
                if not page.locator('.calendar-post').count():
                    page.locator('#month-next').click()
                expect(page.locator('.calendar-post')).to_have_count(1)
                page.locator('.calendar-post').click()
                expect(page.locator('#editor')).to_be_visible()
                page.locator('#close-editor').click()
                page.screenshot(path='/tmp/postdesk-calendar.png',full_page=True)
                uncertain=db.add_post('Was this post published?',db.now_str(),'uncertain')
                page.locator('.nav-item[data-view="queue"]').click()
                page.locator('#search').fill('Was this post')
                expect(page.locator('.post-row')).to_have_count(1)
                page.get_by_role('button',name='Was this post published?',exact=True).click()
                expect(page.locator('#recovery')).to_be_visible()
                page.locator('#checked-linkedin').check()
                page.get_by_role('button',name='Return to draft',exact=True).click()
                expect(page.locator('#recovery')).not_to_be_visible()
                assert db.get(uncertain)['status']=='draft'
                page.locator('.nav-item[data-view="import"]').click()
                page.locator('#import-file').set_input_files({'name':'posts.csv','mimeType':'text/csv','buffer':b'text,scheduled_at\nImported browser draft,\n'})
                page.locator('#import-form').get_by_role('button',name='Import posts',exact=True).click()
                expect(page.locator('#view-queue')).to_be_visible()
                # Confirm an intentional reimport, then lose its success response.
                # Retrying must reuse both the key and confirmation payload.
                page.locator('.nav-item[data-view="import"]').click()
                page.locator('#import-file').set_input_files({'name':'posts.csv','mimeType':'text/csv','buffer':b'text,scheduled_at\nImported browser draft,\n'})
                def drop_confirmed_response(route):
                    if 'confirm_duplicate' in (route.request.post_data or ''):
                        response=route.fetch()
                        assert response.status in (200,201)
                        route.abort('failed')
                    else:
                        route.continue_()
                page.route('**/api/import',drop_confirmed_response)
                page.once('dialog',lambda dialog:dialog.accept())
                page.locator('#import-form').get_by_role('button',name='Import posts',exact=True).click()
                expect(page.locator('#import-error')).to_contain_text('Cannot reach')
                imported_ids=[post['id'] for post in db.all_posts() if post['text']=='Imported browser draft']
                assert len(imported_ids)==2
                page.unroute('**/api/import',drop_confirmed_response)
                page.locator('#import-form').get_by_role('button',name='Import posts',exact=True).click()
                expect(page.locator('#view-queue')).to_be_visible()
                assert len([post for post in db.all_posts() if post['text']=='Imported browser draft'])==2
                page.locator('#search').fill('Imported browser draft')
                expect(page.locator('.post-row')).to_have_count(2)
                for remaining,pid in zip((1,0),imported_ids):
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.get_by_role('button',name='Delete post #'+str(pid),exact=True).click()
                    expect(page.locator('.post-row')).to_have_count(remaining)
                page.locator('.nav-item[data-view="activity"]').click()
                expect(page.locator('#activity-list .event-row')).to_have_count(20)
                page.locator('.nav-item[data-view="settings"]').click()
                expect(page.locator('#email-status')).to_have_text('Email alerts not configured')
                page.set_viewport_size({'width':390,'height':844})
                for view in ['overview','queue','calendar','settings']:
                    page.locator('#menu-toggle').click()
                    page.locator('.nav-item[data-view="'+view+'"]').click()
                    expect(page.locator('#view-'+view)).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow: '+view
                page.locator('#menu-toggle').click()
                expect(page.locator('#sidebar .nav-item.active')).to_be_focused()
                page.keyboard.press('Escape')
                expect(page.locator('#menu-toggle')).to_be_focused()
                page.locator('#menu-toggle').click();page.locator('.nav-item[data-view="overview"]').click()
                page.screenshot(path='/tmp/postdesk-mobile.png',full_page=True)
                page.get_by_role('button',name='Create post',exact=True).click()
                assert page.locator('label[for="post-text"]').count()==1
                page.locator('#post-text').focus();page.keyboard.press('Tab')
                assert page.locator('#post-image').evaluate('(e)=>e===document.activeElement')
                page.locator('#close-editor').click()
                page.locator('#menu-toggle').click();page.locator('#logout').click()
                expect(page.locator('#login')).to_be_visible()
                assert not errors,errors
                browser.close()
                print('PASS: sign-in, composer preview, draft, scheduling, pause/resume, pagination, search, unsaved changes, calendar, uncertain recovery, import, delete, activity, system, mobile layouts, keyboard focus, sign-out; no JS errors')
        finally:
            server.close()


if __name__=='__main__':
    main()
