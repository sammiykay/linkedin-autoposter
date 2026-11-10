"""Regenerate public example spreadsheets using a temporary authenticated app."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='postdesk-templates-') as folder:
        path=Path(folder)
        os.environ.update(APP_ENV_FILE=str(path/'.env'),APP_DATA_DIR=str(path/'data'),APP_MEDIA_DIR=str(path/'media'),APP_LOG_DIR=str(path/'logs'),APP_BACKUP_DIR=str(path/'backups'),DB_PATH=str(path/'data/posts.db'),TOKEN_PATH=str(path/'data/token.json'),APP_PASSWORD='temporary-template-generation-only')
        from app.dashboard import create_app
        client=create_app({'TESTING':True}).test_client();csrf=client.get('/api/session').json['csrf']
        assert client.post('/api/login',json={'password':os.environ['APP_PASSWORD']},headers={'X-CSRF-Token':csrf}).status_code==200
        output=root/'docs/templates';output.mkdir(exist_ok=True)
        for format in ('csv','xlsx'):
            for layout in ('simple','full'):
                response=client.get(f'/api/lead-imports/template?format={format}&layout={layout}')
                assert response.status_code==200
                (output/f'leads-{layout}.{format}').write_bytes(response.data)
            response=client.get('/api/campaign-imports/template?format='+format);assert response.status_code==200
            (output/f'campaigns.{format}').write_bytes(response.data)
    print('Generated six example templates in docs/templates; no runtime data used.')

if __name__=='__main__':main()
