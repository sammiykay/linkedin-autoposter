"""Local operator tools; intentionally not available through browser commands."""
import argparse
import json
from app import db,deployment


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['status','recover-deployment'])
    args=parser.parse_args();db.init()
    if args.action=='recover-deployment':
        deployment.ready()
        db.set_control(actor='operator',deploy_paused=0,paused=1)
        print('Readiness passed. Deployment block cleared; manually resume publishing in the dashboard after review.')
    print(json.dumps(dict(controls=db.controls(),worker=db.worker_health()),indent=2))


if __name__=='__main__':
    main()
