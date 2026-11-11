"""Exercise cache publication, reuse, rebuild and failure without network installs."""
import subprocess
from pathlib import Path
import pytest
from deploy.prepare_env import prepare


@pytest.fixture
def project(tmp_path):
    root=tmp_path/'project';(root/'releases').mkdir(parents=True)
    return root


def release(root,name,lock='Example==1\n'):
    path=root/'releases'/name;path.mkdir();(path/'requirements.lock').write_text(lock);(path/'requirements.txt').write_text('Example>=1\n');return path


def fake_runner(commands,fail_install=False,fail_check=None):
    def run(command):
        commands.append(command)
        if command[1:3]==['-m','venv']:
            path=Path(command[3]);(path/'bin').mkdir();(path/'bin/python').write_text('test interpreter')
        if fail_install and 'install' in command:raise subprocess.CalledProcessError(1,command)
        if fail_check and command[-2:]==['pip','check'] and str(command[0]).startswith(str(fail_check)):raise subprocess.CalledProcessError(1,command)
    return run


def test_reuses_environment_and_preserves_previous_release(project):
    calls=[];a=release(project,'a');environment=prepare(project,a,runner=fake_runner(calls))
    assert a.joinpath('venv').resolve()==environment
    calls.clear();b=release(project,'b');assert prepare(project,b,runner=fake_runner(calls))==environment
    assert len(calls)==1 and calls[0][-2:]==['pip','check']
    assert a.joinpath('venv').resolve()==b.joinpath('venv').resolve()
    assert not any('install' in command for command in calls)


def test_changed_requirements_and_forced_rebuild_leave_old_env(project):
    calls=[];a=release(project,'a');old=prepare(project,a,runner=fake_runner(calls))
    b=release(project,'b','Example==2\n');new=prepare(project,b,runner=fake_runner(calls));assert new!=old and old.exists()
    c=release(project,'c','Example==2\n');forced=prepare(project,c,rebuild=True,runner=fake_runner(calls));assert forced!=new and new.exists()
    assert (a/'venv').resolve()==old and (b/'venv').resolve()==new


def test_failed_build_does_not_replace_prior_environment(project):
    calls=[];a=release(project,'a');old=prepare(project,a,runner=fake_runner(calls))
    b=release(project,'b')
    with pytest.raises(subprocess.CalledProcessError):prepare(project,b,rebuild=True,runner=fake_runner(calls,fail_install=True))
    assert not (b/'venv').exists() and old.exists()
    c=release(project,'c');assert prepare(project,c,runner=fake_runner(calls))==old


def test_broken_cache_gets_new_environment_without_mutating_old(project):
    calls=[];a=release(project,'a');old=prepare(project,a,runner=fake_runner(calls))
    b=release(project,'b');new=prepare(project,b,runner=fake_runner(calls,fail_check=old));assert new!=old
    assert old.exists() and (a/'venv').resolve()==old
    with pytest.raises(ValueError):prepare(project,b,runner=fake_runner(calls))
