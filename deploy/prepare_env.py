"""Reuse completed, unchanged dependency environments without mutating releases."""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


def run(command):
    return subprocess.run(command,check=True)


def fingerprint(release):
    identity=[sys.implementation.name,sys.version,sys.executable,str(Path(sys.executable).resolve()),platform.platform(),platform.machine()]
    digest=hashlib.sha256(json.dumps(identity).encode())
    for name in ('requirements.lock','requirements.txt'):
        digest.update(name.encode());digest.update((release/name).read_bytes())
    return digest.hexdigest()


def prepare(root,release,rebuild=False,runner=run):
    root=Path(root).resolve();release=Path(release).resolve()
    if not release.is_relative_to(root/'releases'):raise ValueError('Release must be under the project releases directory.')
    destination=release/'venv'
    if destination.exists() or destination.is_symlink():raise ValueError('Release already has an environment; refusing to replace it.')
    cache=root/'.environments';cache.mkdir(mode=0o700,exist_ok=True)
    key=fingerprint(release);pointer=cache/key
    if not rebuild and pointer.is_symlink():
        candidate=pointer.resolve()
        if candidate.parent==cache and (candidate/'.complete').is_file() and (candidate/'.complete').read_text()==key and (candidate/'bin/python').exists():
            try:runner([str(candidate/'bin/python'),'-m','pip','check'])
            except (subprocess.CalledProcessError,OSError):pass
            else:
                destination.symlink_to(candidate,target_is_directory=True)
                print('Reusing tested dependencies: requirements and Python are unchanged.',flush=True)
                return candidate
    # Build at its permanent path: moving a virtualenv would break script shebangs.
    environment=cache/(key+'-'+uuid.uuid4().hex[:12]);environment.mkdir(mode=0o700)
    try:
        print('Preparing dependencies: new/changed requirements, Python, or requested rebuild.',flush=True)
        runner([sys.executable,'-m','venv',str(environment)])
        runner([str(environment/'bin/python'),'-m','pip','install','-r',str(release/'requirements.lock')])
        runner([str(environment/'bin/python'),'-m','pip','check'])
        (environment/'.complete').write_text(key)
        temporary=cache/('.pointer-'+uuid.uuid4().hex)
        temporary.symlink_to(environment,target_is_directory=True)
        try:os.replace(temporary,pointer)
        finally:temporary.unlink(missing_ok=True)
        destination.symlink_to(environment,target_is_directory=True)
        return environment
    except BaseException:
        # Only remove the new, unreferenced build. Prior environments are retained.
        if not destination.exists() and (not pointer.is_symlink() or pointer.resolve()!=environment):shutil.rmtree(environment)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--release',type=Path,required=True);parser.add_argument('--rebuild',action='store_true')
    args=parser.parse_args();prepare(args.root,args.release,args.rebuild)


if __name__=='__main__':main()
