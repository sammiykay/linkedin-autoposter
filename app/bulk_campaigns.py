"""Preview and atomically import paused campaign definitions from spreadsheets."""
import csv
import io
import json
import re
import secrets
import time
from flask import Blueprint, request, jsonify, Response, send_file
from app import db,lead_service as svc
from app.leads import read_sheet, mutation, require, LeadConflict

bp=Blueprint('campaign_imports',__name__)
FIELDS=['campaign_name','description','timezone','weekdays','reminder_time','step','delay_days','message','job_title_keywords','industries','company_sizes','countries','email_digest']
GUIDE={
 'campaign_name':'Required on every row. Rows with the same name form one campaign. Existing names are skipped, case-insensitively.',
 'description':'Optional campaign description. Set on the first step; later rows may leave it blank.',
 'timezone':'Required once per campaign: IANA name, e.g. Africa/Lagos. Later rows can be blank.',
 'weekdays':'Workdays separated by semicolons: Mon;Wed;Fri. Default Mon;Wed;Fri.',
 'reminder_time':'24-hour HH:MM. Default 09:00.',
 'step':'Required consecutive integer sequence starting at 1; maximum 10 steps per campaign.',
 'delay_days':'Whole days after the previous confirmed send, 0–365. First step must be 0. Follow-ups require an explicit delay.',
 'message':'Required plain-text message, maximum 5,000 characters. Supports the same {{placeholders}} as the campaign editor.',
 'job_title_keywords':'Optional semicolon-separated target role keywords.',
 'industries':'Optional semicolon-separated exact industry names.',
 'company_sizes':'Optional semicolon-separated bands: 1-10;11-50;51-200;201-500;501-1000;1001+.',
 'countries':'Optional semicolon-separated country names.',
 'email_digest':'true or false; default false. Imported campaigns remain paused regardless.',
}


@bp.errorhandler(LeadConflict)
def conflict(error):return jsonify(error=str(error)),409


def workbook(rows,fields,sheet_name,filename,guide):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    book=Workbook();sheet=book.active;sheet.title=sheet_name;sheet.append(fields)
    for row in rows:sheet.append([row.get(k,'') for k in fields])
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    for column,key in enumerate(fields,1):
        sheet.column_dimensions[get_column_letter(column)].number_format='@'
        sheet.column_dimensions[get_column_letter(column)].width=65 if key in ('message','notes') else 27
        sheet.cell(1,column).font=Font(bold=True,color='FFFFFF');sheet.cell(1,column).fill=PatternFill('solid',fgColor='2563EB')
        for line in range(2,sheet.max_row+1):sheet.cell(line,column).number_format='@';sheet.cell(line,column).alignment=Alignment(wrap_text=True,vertical='top')
    if 'company_size' in fields:
        from openpyxl.worksheet.datavalidation import DataValidation
        validation=DataValidation(type='list',formula1='"'+','.join(svc.SIZES)+'"',allow_blank=True)
        validation.errorTitle='Choose a company-size band';validation.error='Use one of the listed size bands.';validation.showErrorMessage=True
        sheet.add_data_validation(validation);letter=get_column_letter(fields.index('company_size')+1);validation.add(f'{letter}2:{letter}501')
    instructions=book.create_sheet('Field guide');instructions.append(['Field','Required','Instructions'])
    for item in guide:instructions.append(list(item))
    instructions.column_dimensions['A'].width=27;instructions.column_dimensions['B'].width=18;instructions.column_dimensions['C'].width=100
    for row in instructions:
        for cell in row:cell.alignment=Alignment(wrap_text=True,vertical='top')
    stream=io.BytesIO();book.save(stream);stream.seek(0)
    return send_file(stream,download_name=filename,as_attachment=True)


@bp.get('/api/campaign-imports/template')
def template():
    first=dict.fromkeys(FIELDS,'');first.update(campaign_name='Founder conversations',description='A short sequence for software founders',timezone='Africa/Lagos',weekdays='Mon;Wed;Fri',reminder_time='09:00',step='1',delay_days='0',message='Hi {{first_name}}, would you be open to a conversation about {{company}}?',job_title_keywords='Founder;CEO',industries='Software',company_sizes='1-10;11-50',countries='Nigeria',email_digest='false')
    second=dict.fromkeys(FIELDS,'');second.update(campaign_name=first['campaign_name'],step='2',delay_days='3',message='Hi {{first_name}}, following up on my earlier note. Is this worth exploring?')
    rows=[first,second]
    if request.args.get('format')=='xlsx':return workbook(rows,FIELDS,'Campaigns','postdesk-campaigns-template.xlsx',[(k,'Required' if k in ('campaign_name','step','message') else 'See instructions',GUIDE[k]) for k in FIELDS])
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    return Response(stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=postdesk-campaigns-template.csv'})


def parse_campaigns(frame):
    if not {'campaign_name','step','message'}<=set(frame.columns):raise ValueError('Include campaign_name, step and message columns.')
    groups={};errors=[]
    for index,row in frame.iterrows():
        row={k:str(v).strip() for k,v in row.to_dict().items()};name=row.get('campaign_name','');line=int(index)+2
        if not name:errors.append({'row':line,'error':'campaign_name is required.'});continue
        group=groups.setdefault(name.casefold(),{'name':name,'rows':[]});group['rows'].append((line,row))
    if len(groups)>100:raise ValueError('Import up to 100 campaigns per file.')
    results=[]
    for group in groups.values():
        try:
            settings={};steps={}
            for line,row in group['rows']:
                for key in FIELDS:
                    if key in ('campaign_name','step','message','delay_days') or not row.get(key):continue
                    if key in settings and settings[key]!=row[key]:raise ValueError(f'Conflicting {key} values within this campaign.')
                    settings[key]=row[key]
                if not re.fullmatch(r'[1-9]\d*',row['step']):raise ValueError('step must be an integer starting at 1.')
                step=int(row['step'])
                if step in steps:raise ValueError(f'Duplicate step {step}.')
                raw=row.get('delay_days','') or ('0' if step==1 else '')
                if not re.fullmatch(r'\d+',raw):raise ValueError('Follow-ups require a whole-number delay_days.')
                if step==1 and int(raw)!=0:raise ValueError('The first step must have delay_days=0.')
                steps[step]={'template':row['message'],'delay_days':int(raw)}
            if sorted(steps)!=list(range(1,len(steps)+1)):raise ValueError('Steps must be consecutive: 1, 2, 3, and so on.')
            day_names=['mon','tue','wed','thu','fri','sat','sun'];days=[]
            for value in settings.get('weekdays','Mon;Wed;Fri').split(';'):
                if value.strip().lower() not in day_names:raise ValueError('Use weekdays such as Mon;Wed;Fri.')
                days.append(day_names.index(value.strip().lower()))
            digest=settings.get('email_digest','false').lower()
            if digest not in ('true','false'):raise ValueError('email_digest must be true or false.')
            values=svc.campaign({'name':group['name'],'description':settings.get('description',''),'timezone':settings.get('timezone',''),'time':settings.get('reminder_time','09:00'),'weekdays':days,'steps':[steps[i] for i in sorted(steps)],'criteria':{key:[v.strip() for v in settings.get(column,'').split(';') if v.strip()] for key,column in [('job_title','job_title_keywords'),('industry','industries'),('company_size','company_sizes'),('country','countries')]},'email_digest':digest=='true'})
            invalid=set(values['criteria']['company_size'])-set(svc.SIZES)
            if invalid:raise ValueError('Invalid company-size band: '+', '.join(sorted(invalid)))
            results.append(values)
        except ValueError as error:errors.append({'row':line,'campaign':group['name'],'error':str(error)})
    return results,errors


@bp.post('/api/campaign-imports/preview')
def preview():
    frame,digest=read_sheet();rows,errors=parse_campaigns(frame);duplicate_names=[]
    with db.conn() as c:
        existing={json.loads(r['data'])['name'].casefold() for r in c.execute('SELECT data FROM lead_campaigns')}
        duplicate_names=[r['name'] for r in rows if r['name'].casefold() in existing]
        previous=c.execute('SELECT result FROM campaign_imports WHERE digest=? AND result IS NOT NULL AND created>? ORDER BY created DESC LIMIT 1',(digest,time.time()-86400)).fetchone()
        token=secrets.token_urlsafe(24)
        if not errors:
            c.execute('DELETE FROM campaign_imports WHERE result IS NULL AND created<?',(time.time()-3600,))
            c.execute('INSERT INTO campaign_imports(id,digest,data,created) VALUES(?,?,?,?)',(token,digest,json.dumps(rows),time.time()))
    return jsonify(token=token if not errors else None,count=len(frame),campaigns=len(rows),new=len(rows)-len(duplicate_names),duplicates=len(duplicate_names),duplicate_names=duplicate_names,errors=errors,sample=[{'name':r['name'],'steps':len(r['steps']),'timezone':r['timezone'],'weekdays':r['weekdays'],'time':r['time']} for r in rows],previous=json.loads(previous['result']) if previous else None)


@bp.post('/api/campaign-imports/commit')
@mutation
def commit(c,data):
    row=require(c,'campaign_imports',str(data.get('token','')))
    if row['result']:return json.loads(row['result'])
    if time.time()-row['created']>3600:raise LeadConflict('Import preview expired. Upload again.')
    if c.execute('SELECT 1 FROM campaign_imports WHERE digest=? AND result IS NOT NULL AND created>?',(row['digest'],time.time()-86400)).fetchone() and data.get('confirm_duplicate') is not True:raise LeadConflict('This file was imported within 24 hours. Confirm importing it again.')
    existing={json.loads(r['data'])['name'].casefold() for r in c.execute('SELECT data FROM lead_campaigns')}
    result={'created':0,'skipped':0,'ids':[]}
    for values in json.loads(row['data']):
        name=values['name'].casefold()
        if name in existing:result['skipped']+=1;continue
        cid=c.execute('INSERT INTO lead_campaigns(data,paused) VALUES(?,1)',(json.dumps(values),)).lastrowid
        db.audit(c,'lead.campaign_imported',detail=f'Campaign {cid}; paused')
        existing.add(name);result['created']+=1;result['ids'].append(cid)
    c.execute('UPDATE campaign_imports SET result=? WHERE id=?',(json.dumps(result),row['id']))
    return result
