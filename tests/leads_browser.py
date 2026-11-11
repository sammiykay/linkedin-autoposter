"""Real browser coverage of assisted outreach against temporary storage."""
import os
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from playwright.sync_api import sync_playwright,expect
    with tempfile.TemporaryDirectory(prefix='postdesk-leads-browser-') as folder:
        root=Path(folder)
        os.environ.update(APP_ENV_FILE=str(root/'.env'),APP_DATA_DIR=str(root/'data'),APP_MEDIA_DIR=str(root/'media'),APP_LOG_DIR=str(root/'logs'),APP_BACKUP_DIR=str(root/'backups'),DB_PATH=str(root/'data/posts.db'),TOKEN_PATH=str(root/'data/token.json'),APP_PASSWORD='test-leads-password',SMTP_HOST='',ALERT_TO='',COOKIE_SECURE='false')
        from app import config,db
        from app.dashboard import create_app
        from waitress import create_server
        server=create_server(create_app(),host='127.0.0.1',port=0)
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1440,'height':1000},permissions=['clipboard-read','clipboard-write'])
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.effective_port}')
                page.locator('#password').fill(config.APP_PASSWORD);page.get_by_role('button',name='Open your workspace').click()
                page.get_by_role('button',name='Leads',exact=True).click()
                page.get_by_role('button',name='Add contact',exact=True).click()
                page.locator('#lead-contact-editor-form-full_name').fill('Ada Okafor')
                page.locator('#lead-contact-editor-form-linkedin_url').fill('https://www.linkedin.com/in/ada-example')
                page.locator('#lead-contact-editor-form-first_name').fill('Ada')
                page.locator('#lead-contact-editor-form-company').fill('Northstar')
                page.locator('#lead-contact-editor-form-job_title').fill('Founder')
                page.get_by_role('button',name='Save contact',exact=True).click()
                expect(page.locator('#lead-contact-editor')).not_to_be_visible()
                expect(page.locator('#lead-content')).to_contain_text('Ada Okafor')
                page.get_by_role('tab',name='Campaigns',exact=True).click();page.get_by_role('button',name='Create campaign').click()
                page.locator('#lead-campaign-editor-form-name').fill('Founder conversations')
                page.locator('#lead-campaign-editor-form-zone_confirm').check()
                page.locator('#lead-campaign-editor-form-job_title').fill('Founder')
                page.get_by_role('button',name='Save campaign',exact=True).click()
                expect(page.locator('#lead-campaign-editor')).not_to_be_visible()
                page.get_by_role('button',name='Pause campaign',exact=True).click()
                expect(page.get_by_role('button',name='Resume campaign')).to_be_visible()
                page.get_by_role('button',name='Resume campaign').click()
                expect(page.get_by_role('button',name='Pause campaign')).to_be_visible()
                page.get_by_role('tab',name='Contacts',exact=True).click()
                page.get_by_role('checkbox',name='Select Ada Okafor').check()
                page.get_by_role('button',name='Enroll selected',exact=True).click()
                page.get_by_role('button',name='Confirm enrollment',exact=True).click()
                expect(page.locator('#lead-enroll')).not_to_be_visible()
                with db.conn() as c:c.execute('UPDATE lead_tasks SET due=0')
                page.get_by_role('tab',name='Due today',exact=True).click()
                page.get_by_role('button',name='Review message',exact=True).click()
                expect(page.locator('#lead-task-editor-form-text')).to_have_value('Hi Ada, I enjoyed learning about Northstar. Would you be open to a short conversation?')
                page.get_by_role('button',name='Copy message',exact=True).click()
                with db.conn() as c:assert c.execute("SELECT count(*) FROM lead_tasks WHERE state='sent'").fetchone()[0]==0
                page.locator('#lead-task-editor-form-confirmed').check()
                page.get_by_role('button',name='Mark as sent',exact=True).click()
                expect(page.locator('#lead-task-editor')).not_to_be_visible()
                page.get_by_role('tab',name='Contacts',exact=True).click()
                page.get_by_role('button',name='Record reply',exact=True).click()
                page.locator('#lead-reply-form-disposition').select_option('interested')
                page.locator('#lead-reply-form-note').fill('Please send more information.')
                page.locator('#lead-reply-form').get_by_role('button',name='Record reply',exact=True).click()
                expect(page.locator('#lead-reply')).not_to_be_visible()
                expect(page.locator('#lead-content')).to_contain_text('replied')
                page.get_by_role('tab',name='Import contacts',exact=True).click()
                page.locator('#lead-import-form-file').set_input_files({'name':'leads.csv','mimeType':'text/csv','buffer':b'full_name,linkedin_url,first_name,company\nSam Founder,https://www.linkedin.com/in/sam-founder,Sam,Studio\nNora Founder,,Nora,Studio\n'})
                page.get_by_role('button',name='Preview import',exact=True).click()
                page.get_by_role('button',name='Confirm import',exact=True).click()
                expect(page.get_by_role('tab',name='Contacts',exact=True)).to_have_attribute('aria-selected','true')
                expect(page.locator('#lead-content')).to_contain_text('Sam Founder')
                expect(page.locator('#lead-content')).to_contain_text('Nora Founder')
                page.get_by_role('checkbox',name='Select Nora Founder',exact=True).check()
                page.get_by_role('button',name='Enroll selected',exact=True).click()
                page.get_by_role('button',name='Confirm enrollment',exact=True).click()
                expect(page.locator('#lead-enroll')).not_to_be_visible()
                with db.conn() as c:c.execute("UPDATE lead_tasks SET due=0 WHERE state='pending'")
                page.get_by_role('tab',name='Due today',exact=True).click()
                page.get_by_role('button',name='Review message',exact=True).click()
                expect(page.get_by_role('link',name='Open LinkedIn inbox',exact=True)).to_have_attribute('href','https://www.linkedin.com/messaging/')
                expect(page.locator('#lead-task-editor')).to_contain_text('No profile URL saved')
                page.locator('#lead-task-editor').get_by_role('button',name='Close',exact=True).click()
                page.get_by_role('tab',name='Import campaigns',exact=True).click()
                with page.expect_download() as download:
                    page.get_by_role('link',name='Download campaign Excel template',exact=True).click()
                assert download.value.suggested_filename=='postdesk-campaigns-template.xlsx'
                page.locator('#campaign-import-form-file').set_input_files({'name':'campaigns.csv','mimeType':'text/csv','buffer':b'campaign_name,timezone,step,delay_days,message\nBulk founders,Africa/Lagos,1,0,Hi {{first_name}}\nBulk founders,,2,3,Following up {{first_name}}\n'})
                page.get_by_role('button',name='Preview campaigns',exact=True).click()
                expect(page.locator('#lead-content')).to_contain_text('1 valid campaigns')
                page.get_by_role('button',name='Confirm campaign import',exact=True).click()
                expect(page.get_by_role('tab',name='Campaigns',exact=True)).to_have_attribute('aria-selected','true')
                expect(page.locator('#lead-content')).to_contain_text('Bulk founders')
                expect(page.get_by_role('button',name='Resume campaign',exact=True)).to_be_visible()
                # A normal polling refresh must not invalidate an in-flight Leads tab load.
                page.get_by_role('tab',name='Campaigns',exact=True).click()
                expect(page.get_by_role('button',name='Create campaign')).to_be_visible()
                held=[]
                page.route('**/api/leads?*',lambda route:held.append(route))
                page.get_by_role('tab',name='Contacts',exact=True).click()
                page.wait_for_function("document.querySelector('#lead-content .sk') !== null")
                page.wait_for_timeout(300)
                page.evaluate('refresh({background:true})')
                assert held, 'Expected a gated contacts request'
                held[0].continue_()
                page.unroute('**/api/leads?*')
                expect(page.locator('#lead-content')).to_contain_text('Sam Founder')
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#lead-content').screenshot(path='/tmp/postdesk-leads-mobile.png')
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
                # Form edits survive normal refreshes and Escape asks before discarding.
                page.get_by_role('button',name='Edit',exact=True).first.click()
                page.locator('#lead-contact-editor-form-notes').fill('Unsaved research')
                page.once('dialog',lambda d:d.dismiss())
                page.keyboard.press('Escape')
                expect(page.locator('#lead-contact-editor')).to_be_visible()
                expect(page.locator('#lead-contact-editor-form-notes')).to_have_value('Unsaved research')
                page.once('dialog',lambda d:d.accept());page.keyboard.press('Escape')
                expect(page.locator('#lead-contact-editor')).not_to_be_visible()
                assert not errors,errors
                context.close();browser.close()
        finally:server.close()
    print('Leads browser workflows passed: contacts, campaigns, pause, enrollment, draft, copy, sent, reply, import, mobile and discard protection.')

if __name__=='__main__':main()
