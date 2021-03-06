# Usage:
# $ py.test

import os.path
import pytest
import re
import shutil
import subprocess
from subprocess import check_output, check_call
import tempfile


@pytest.fixture
def repo():
    # type: () -> str
    d = tempfile.mkdtemp(prefix='tmp.test-git-rbr.')
    git_dir = os.environ.get('GIT_DIR')
    os.environ['GIT_DIR'] = os.path.join(d, '.git')
    cwd = os.getcwd()
    os.chdir(d)
    check_call(['git', 'init', d])

    this_dir = os.path.dirname(os.path.realpath(__file__))
    rbr_root = os.path.dirname(this_dir)
    os.environ['PATH'] = os.pathsep.join([rbr_root, os.environ['PATH']])

    return d

    # Pytest 2.6.1 which I have handy doesn't support this.  Forget it for now.
    # yield d

    os.chdir(cwd)
    if git_dir is None:
        del os.environ['GIT_DIR']
    else:
        os.environ['GIT_DIR'] = git_dir
    shutil.rmtree(d)


def shell(cmds):
    # type: (str) -> None
    try:
        check_output(['sh', '-ec', cmds])
    except subprocess.CalledProcessError as e:
        cmds_fmtd = re.sub('^', '  ', cmds.strip('\n'), flags=re.M) + '\n'
        raise RuntimeError('Shell commands exited with code %s:\n%s'
                           % (e.returncode, cmds_fmtd))


def expect_error(desc, signature, cmd):
    # type: (str, str, str) -> None
    '''Expect `cmd` to fail with a matching message.'''
    try:
        out = check_output(cmd, stderr=subprocess.STDOUT)
        print out
        raise RuntimeError('Expected %s; none happened' % (desc,))
    except subprocess.CalledProcessError as e:
        if signature not in e.output:
            print e.output
            raise RuntimeError('Expected %s; got different message' % (desc,))


def expect_conflict(cmd):
    # type: (str) -> None
    expect_error('conflict', 'git rbr --continue', cmd)


def setup_shell(cmds):
    # type: (str) -> None
    preamble = '''
testci () {
  # args: message [filename [contents]]
  # optional args default to message
  mkdir -p "$(dirname "${2:-$1}")" &&
  echo "${3:-$1}" >"${2:-$1}" &&
  git add "${2:-$1}" &&
  git commit -m "$1"
}
'''
    shell(preamble + cmds)


def describe_for_error(commitish):
    return check_output(
        ['git', 'log', '-n1', '--pretty=format:%h \'%s\'%d', commitish])


def show_for_error(revlist_args):
    return check_output(
        ['git', 'log', '--oneline', '--graph', '--decorate', '--boundary']
        + revlist_args)


def show_repo_for_error():
    return show_for_error(['--all', 'HEAD'])


def show_range_for_error(revrange):
    return show_for_error([revrange])


def range_subjects(revrange):
    # type: (str) -> None
    return check_output(['git', 'log', '--pretty=format:%s', '--reverse',
                         revrange]).strip('\n').split('\n')
    # '%s..%s' % (upstream, branch)


def all_branches():
    # type: () -> Set[str]
    return set(check_output(
        ['git', 'for-each-ref', '--format=%(refname:short)', 'refs/heads/']
    ).strip().split('\n'))


class RepoError(RuntimeError):
    def __init__(self, msg):        
        super(RepoError, self).__init__(
            msg.rstrip('\n') + '\n' + show_repo_for_error()
        )


def assert_range_subjects(revrange, subjects):
    # type: (str, str) -> None
    assert ' '.join(range_subjects(revrange)) == subjects


def assert_atop(upstream, branch):
    if '0' != check_output(
        ['git', 'rev-list', '--count', '--max-count=1',
         upstream, '--not', branch, '--']).strip():
        raise RepoError('Commit %s not atop %s:' % (branch, upstream,))


def assert_updated(branches=None):
    '''Assert each branch is atop its upstream.  If None, all branches but master.'''
    if branches is None:
        branches = all_branches() - set(['master'])

    for branch in branches:
        assert_atop(branch+'@{u}', branch)


def assert_linear(upstream, branch, subjects):
    # type: (str, str, str) -> None
    '''Assert upstream..branch is a linear history with the given subjects.

    `subjects` is a space-separated list.
    '''

    revrange = '%s..%s' % (upstream, branch)
    if any(len(line.split(' ')) != 2
           for line in check_output(['git', 'log', '--pretty=format:%h %p',
                                     revrange]).strip('\n').split('\n')):
        raise RepoError('Range %s not linear: has merge commit:'
                        % (revrange,))

    assert_atop(upstream, branch)

    assert_range_subjects(revrange, subjects)


def branch_values(branches=None):
    # type: (Optional[List[str]]) -> Dict[str, str]
    '''Returns the commit ID of each branch.  If None, all branches.'''
    data = check_output(['git', 'for-each-ref',
                         '--format=%(refname:short) %(objectname)', 'refs/heads/'])
    all_values = {
        branch: value
        for line in data.strip('\n').split('\n')
        for branch, value in (line.split(' '),)
    }
    if branches is None:
        return all_values
    return {branch: all_values[branch] for branch in branches}


@pytest.fixture
def repo_tree(repo):
    # master <- a <- b <- c
    #             <- d
    # master, a, b advanced
    # no conflicts
    setup_shell('''
testci master
git checkout -qtb a
testci a
git checkout -qtb b
testci b
git checkout -qtb c
testci c
git checkout a -qtb d
testci d
git checkout -q master
testci master2
git checkout -q a
testci a2
git checkout -q b
testci b2
git checkout -q a
''')


def test_tree(repo_tree):
    setup_shell('git checkout a')
    check_call(['git', 'rbr', '-v'])
    assert_updated()
    assert_linear('master^', 'c', 'master2 a a2 b b2 c')
    assert_linear('a^', 'd', 'a2 d')


def test_safety_checks(repo_tree):
    setup_shell('git checkout a')
    setup_shell('git tag t b')
    expect_error('non-branch error', 'are not branches', ['git', 'rbr', '-v'])
    setup_shell('git tag -d t')

    setup_shell('git branch b --unset-upstream')
    expect_error('unset-upstream error', 'have no upstream set', ['git', 'rbr', '-v'])
    setup_shell('git branch b -u master')
    expect_error('wild-upstream error', 'upstream pointing outside',
                 ['git', 'rbr', '-v'])
    setup_shell('git branch b -u c')
    expect_error('upstream-cycle error', 'are in a cycle', ['git', 'rbr', '-v'])
    setup_shell('git branch b -u a')

    check_call(['git', 'rbr', '-v'])
    assert_updated()


@pytest.fixture
def repo_conflicted(repo):
    # master <- a <- b <- ab <- c
    # master, a advanced
    # a, ab conflict
    setup_shell('''
testci master
git checkout -qtb a
testci a
git checkout -qtb b
testci b
git checkout -qtb ab
testci ab a
git checkout -qtb c
testci c
git checkout -q master
testci master2
git checkout -q a
testci aa a
''')


def test_continue(repo_conflicted):
    setup_shell('git checkout a')
    expect_conflict(['git', 'rbr', '-v'])
    check_call(['git', 'add', '-u'])
    check_call(['git', 'rbr', '--continue'])
    assert_updated()
    assert_linear('master^', 'c', 'master2 a aa b ab c')


def test_skip(repo_conflicted):
    setup_shell('git checkout a')
    expect_conflict(['git', 'rbr', '-v'])
    check_call(['git', 'rbr', '--skip'])
    assert_updated()
    assert_linear('master^', 'c', 'master2 a aa b c')


def test_abort(repo_conflicted):
    setup_shell('git checkout a')
    before = branch_values()
    expect_conflict(['git', 'rbr', '-v'])
    check_call(['git', 'rbr', '--abort'])
    assert before == branch_values()
