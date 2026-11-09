"""Deployment-only operations; never invoked by browser requests."""
import sys
from app import db

if __name__=='__main__':
    if sys.argv[1:]!=['migrate']:
        raise SystemExit('Expected migrate')
    db.init()
    db.set_control(actor='deploy',deploy_paused=1)
