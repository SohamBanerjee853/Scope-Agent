"""Record actual Scope terminal viewports and a deterministic local payment demo."""
from pathlib import Path
import argparse
import gzip
import json
import os
import shlex
import subprocess
import sys
import time
import traceback
import uuid

BASE = Path(__file__).resolve().parent
TMUX = '/usr/local/bin/tmux'
COMMAND = 'scope demo-adapter probe'

def put(path, value):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)

def worker(root):
    from scope import demo, experience, ipc, learning, log, receipt, ui
    from scope.demo_agent import BUGGY_KEY, FIXED_KEY
    from scope.smoke import _ALLOW
    from scope.tiers import classify
    project = root / 'checkout'
    def state(name): put(root / 'state.json', {'stage': name})
    def gate(name):
        state(name)
        deadline = time.monotonic() + 130
        while not (root / ('go-' + name)).exists():
            if time.monotonic() > deadline: raise TimeoutError(name)
            time.sleep(.02)
    def screen(title, lines):
        print('\033[2J\033[H\033[1;36m' + title + '\033[0m\n', flush=True)
        for line in lines: print(line, flush=True)
    def hook(command):
        payload = {'session_id': session, 'cwd': str(project), 'tool_name': 'Bash',
                   'tool_input': {'command': command, 'shell': 'posix', 'description': 'Scripted local checkout demo'}}
        result = subprocess.run([sys.executable, '-m', 'scope', 'hook'], input=json.dumps(payload),
                                text=True, capture_output=True, timeout=105)
        assert result.returncode == 0 and not result.stderr, result.stderr
        wire.append({'command': command, 'stdout': result.stdout, 'exit_code': result.returncode})
        return result.stdout
    def check(label):
        count = 0
        def ask(request):
            nonlocal count
            kind = request['kind']
            state(label + '-' + kind)
            result = ui.answer_request(request)
            count += 1
            return result
        result = experience.check(project, task['task_id'], {
            'question': 'After the first reply is lost, how many charges will this order create?',
            'citations': ['payment.py:31-33'], 'field': 'charge_count',
            'argv': ['scope', 'demo-adapter', 'probe'], 'shell': 'posix', 'timeout': 15},
            ask=ask, provenance='test_fixture')
        assert count == 2 and result['phase'] == 'completed', result
        return result
    try:
        prepared = demo.prepare(project)
        session = prepared['session_id']
        task = learning.start(project, 'Investigate duplicate charges after a lost checkout acknowledgement.', session_id=session)
        log.append(session, 'demo_fixture_start', scripted=True, provenance='test_fixture', human_answers=False)
        wire = []
        deadline = time.monotonic() + 10
        while ipc.exchange({'kind': 'ping'}, timeout=.3) != {'ready': True}:
            if time.monotonic() > deadline: raise RuntimeError('watcher unavailable')
            time.sleep(.1)
        screen('CHECKOUT BUG  /  One order, two charges?', [
            'A payment succeeds, but its acknowledgement is lost.',
            'Checkout retries the same order.', '',
            'payment.py:31-33', '',
            '  def retry_key(order_id, attempt):',
            '      return f"{order_id}-attempt-{attempt}"', '',
            'Each attempt receives a different payment identity.', '',
            'Task: investigate, fix, and verify the retry behavior.', '',
            'Local in-memory service. No real payments.',
            'The demo driver and review inputs are scripted.'])
        gate('permission')
        card = {'summary': 'Inspect the local checkout retry with a bounded probe',
                'commands': [COMMAND], 'domains': [], 'budget': 3}
        assert ipc.exchange({'kind': 'proposal', 'session_id': session, 'cwd': str(project), 'card': card}) == {'accepted': True}
        state('permission-waiting')
        assert hook(COMMAND) == _ALLOW
        screen('01  /  Bounded permission', [
            'Scope approved this exact command family:', '',
            '  scope demo-adapter probe', '',
            'Grant budget: 3 commands',
            'Remaining after this allowance: 2',
            'Expiry: 15 minutes', '',
            'Bound to this session, directory and shell.', '',
            'An allowance does not prove a command executed.',
            'The probe still needs separate execution consent.'])
        gate('hard-ask')
        assert classify('git push origin main', str(project), 'posix').name == 'T3'
        assert hook('git push origin main') == ''
        print('\n\033[1;33mSynthetic boundary check\033[0m', flush=True)
        print('  git push origin main\n  T3: no automatic approval. Returned to host.\n  This push was never executed.', flush=True)
        gate('before')
        before = check('before')
        assert before['observation']['status'] == 'mismatched' and before['observation']['actual'] == 2
        initial = demo._regressions(project, repaired=False)
        log.append(session, 'demo_regression', scripted=True, **initial)
        screen('02  /  Prediction meets evidence', [
            'Recorded prediction: 1 charge',
            'Actual local probe:  2 charges',
            'Comparison:          MISMATCHED', '',
            'Actual retry keys:',
            '  demo-order-attempt-1',
            '  demo-order-attempt-2', '',
            'The service sees two different payment identities.', '',
            'Packaged regression tests:',
            f"  {initial['failed']} failed, {initial['passed']} passed", '',
            'The failed test expects one charge for one order.',
            'The original test assertions remain unchanged.'])
        gate('next')
        def next_ask(request):
            state('next-waiting')
            return ui.answer_request(request)
        selected = experience.choose_next(project, task['task_id'], smaller=demo._REPAIR,
                    larger='Inspect retry identity across other checkout entry points.', ask=next_ask, provenance='test_fixture')
        assert selected['status'] == 'selected'
        agent = demo._FixtureAgent()
        delivered = experience.dispatch(project, task['task_id'], selected['handoff_id'], deliver=agent.deliver)
        assert delivered['status'] == 'dispatched'
        patch = agent.repair(project, selected['handoff_id'])
        log.append(session, 'demo_fixture_patch', **patch)
        learning.checkpoint(project, task['task_id'], note='Scripted local repair: use a stable order key across retries.')
        screen('03  /  Repair the cause', [
            'Selected next step: keep the order identity stable.', '',
            'Actual change in payment.py:', '',
            '\033[31m- return f"{order_id}-attempt-{attempt}"\033[0m',
            '\033[32m+ return order_id\033[0m', '',
            'The local fixture driver applied this exact repair.',
            'Source change recorded by Scope.', '',
            'Same order -> same key -> deduplicated retry.', '',
            'Next: make a fresh prediction and verify the fix.'])
        gate('after')
        assert hook(COMMAND) == _ALLOW
        after = check('after')
        assert after['observation']['status'] == 'matched' and after['observation']['actual'] == 1
        final = demo._regressions(project, repaired=True)
        log.append(session, 'demo_regression', scripted=True, **final)
        screen('04  /  Verify the repair', [
            'Same narrow scope reused. No second grant prompt.', '',
            'Fresh prediction:   1 charge',
            'Actual local probe: 1 charge',
            'Comparison:         MATCHED', '',
            'Actual retry keys:',
            '  demo-order',
            '  demo-order', '',
            f"Regression tests: {final['passed']} passed, {final['failed']} failed", '',
            'The same three tests ran before and after.',
            'Two separately approved executions are recorded.',
            'A matched prediction is evidence, not mastery.'])
        gate('revoke')
        # The recording driver presses Ctrl-r in the actual review pane.
        deadline = time.monotonic() + 10
        while not any(e['event'] == 'scopes_revoked' for e in log.read(session)):
            if time.monotonic() > deadline: raise RuntimeError('revocation not recorded')
            time.sleep(.05)
        state('revoked-request')
        assert hook(COMMAND) == ''
        assert ipc.exchange({'kind': 'stop', 'session_id': session}) == {'received': True}
        log.append(session, 'session_end', reason='scripted_video_fixture')
        document = receipt.write(session)
        expected = {'requests': 4, 'auto_allowed': 2, 'allowed_once': 0, 'denied': 0, 'hard_asks': 1, 'scopes_granted': 1}
        assert document['counts'] == expected, document['counts']
        summary = document['understanding']
        assert all(len(summary[k]) == n for k,n in {'tasks':1,'predictions':2,'executions':2,'observations':2,'next_tasks':1,'dispatches':1}.items())
        report = {'verified': True, 'mode': 'scripted_local_terminal_demo', 'session_id': session,
                  'before': before, 'after': after, 'initial_tests': initial, 'final_tests': final,
                  'wire': wire, 'receipt': document, 'patch': patch,
                  'limits': 'Local payment fixture and scripted reviewer/driver; no live coding model or real payment service.'}
        put(root / 'evidence.json', report)
        (root/'receipt.json').write_text(json.dumps(document, indent=2))
        screen('SESSION RECEIPT  /  What actually happened', [
            f"Permission requests       {document['counts']['requests']}",
            f"Narrow scope grants       {document['counts']['scopes_granted']}",
            f"Scoped allowances         {document['counts']['auto_allowed']}",
            f"T3 hard asks              {document['counts']['hard_asks']}",
            'Scope revoked             yes', '',
            f"Saved predictions         {len(summary['predictions'])}",
            f"Consented probe runs      {len(summary['executions'])}",
            f"Recorded observations     {len(summary['observations'])}",
            'Charges before -> after   2 -> 1',
            f"Final regression tests    {final['passed']} passed", '',
            'After revocation: no automatic allowance.',
            'The synthetic push was never executed.', '',
            'SCOPE',
            'Bound what runs. Check what you understand.'])
        state('complete')
        while not (root/'finish').exists(): time.sleep(.1)
    except Exception:
        (root/'failure.txt').write_text(traceback.format_exc())
        state('failed')
        raise

def record(scale=1):
    from scope.demo import _scripted_environment, _HOMES
    root = BASE / ('take-' + uuid.uuid4().hex[:8])
    root.mkdir()
    for child in set(_HOMES.values()): (root/child).mkdir()
    env = _scripted_environment(root, 'posix')
    env.pop('NO_COLOR', None)
    env.update(TERM='xterm-256color', COLORTERM='truecolor')
    server = 'scope-video-' + uuid.uuid4().hex[:8]
    def tmux(*args):
        return subprocess.check_output([TMUX,'-L',server,'-f',os.devnull,*args], env=env).decode()
    def keys(*args, literal=False): tmux('send-keys','-t',right,*(['-l'] if literal else []),*args)
    def snapshot(pane): return tmux('capture-pane','-p','-e','-t',pane)
    events=[]
    try:
        left=tmux('new-session','-d','-x','148','-y','34','-s','video','-P','-F','#{pane_id}',
                  shlex.join([sys.executable,__file__,'--worker',str(root)])).strip()
        tmux('set-option','-g','status','off')
        tmux('set-option','-g','remain-on-exit','on')
        right=tmux('split-window','-h','-l','81','-t',left,'-P','-F','#{pane_id}',
                   shlex.join([str(Path(sys.executable).parent/'scope'),'watch'])).strip()
        start=time.monotonic()
        # All inputs target the isolated fixture watcher; timing is editorial.
        actions=[
            (10,'permission',lambda:(root/'go-permission').touch()),
            (13,'permission-waiting',lambda:keys('F2')),
            (21,'permission-waiting',lambda:keys('Tab','Tab')),
            (24,'permission-waiting',lambda:keys('Enter')),
            (29,'hard-ask',lambda:(root/'go-hard-ask').touch()),
            (40,'before',lambda:(root/'go-before').touch()),
            (42,'before-prediction',lambda:keys('F2')),
            (46,'before-prediction',lambda:keys('1',literal=True)),
            (47,'before-prediction',lambda:keys('Tab')),
            (48,'before-prediction',lambda:keys('One order should keep the same payment identity.',literal=True)),
            (52,'before-prediction',lambda:keys('Tab','Tab')),
            (54,'before-prediction',lambda:keys('Enter')),
            (56,'before-probe_approval',lambda:keys('F2')),
            (60,'before-probe_approval',lambda:keys('Tab')),
            (62,'before-probe_approval',lambda:keys('Enter')),
            (73,'next',lambda:(root/'go-next').touch()),
            (74,'next-waiting',lambda:keys('F2')),
            (77,'next-waiting',lambda:keys('Tab')),
            (79,'next-waiting',lambda:keys('Enter')),
            (84,'after',lambda:(root/'go-after').touch()),
            (86,'after-prediction',lambda:keys('F2')),
            (87,'after-prediction',lambda:keys('1',literal=True)),
            (88,'after-prediction',lambda:keys('Tab')),
            (89,'after-prediction',lambda:keys('Both retries now use the stable order ID.',literal=True)),
            (90,'after-prediction',lambda:keys('Tab','Tab')),
            (91,'after-prediction',lambda:keys('Enter')),
            (93,'after-probe_approval',lambda:keys('F2')),
            (95,'after-probe_approval',lambda:keys('Tab')),
            (96,'after-probe_approval',lambda:keys('Enter')),
            (102,'revoke',lambda:keys('C-r')),
            (103,'revoke',lambda:(root/'go-revoke').touch()),
            (105,'revoked-request',lambda:keys('F2')),
            (108,'revoked-request',lambda:keys('Enter')),
        ]
        index=0
        with gzip.open(root/'terminal-frames.jsonl.gz','wt',encoding='utf-8') as output:
            frame=0
            while time.monotonic()-start < 120*scale:
                elapsed=(time.monotonic()-start)/scale
                state=json.loads((root/'state.json').read_text())['stage'] if (root/'state.json').exists() else None
                if state=='failed': raise RuntimeError((root/'failure.txt').read_text())
                if index<len(actions) and elapsed>=actions[index][0]:
                    at,expected,callback=actions[index]
                    if state==expected:
                        # Verify the actual UI is at the expected form before
                        # typing answers, instead of trusting a scheduled time.
                        callback()
                        events.append({'at':elapsed,'planned':at,'stage':state,'action_index':index})
                        index+=1
                    elif elapsed>at+10:
                        raise RuntimeError(f'At {elapsed:.1f}s expected {expected}, got {state}; action {index}')
                output.write(json.dumps({'t':elapsed,'left':snapshot(left),'right':snapshot(right),'stage':state})+'\n')
                frame+=1
                target=start+frame*.1*scale
                if target>time.monotonic(): time.sleep(target-time.monotonic())
        assert index==len(actions) and json.loads((root/'state.json').read_text())['stage']=='complete'
        evidence=json.loads((root/'evidence.json').read_text())
        assert evidence['verified']
        put(root/'recording.json',{'recorded_seconds':time.monotonic()-start,'timeline_scale':scale,
             'terminal_columns':148,'terminal_rows':34,'left_columns':66,'right_columns':81,
             'inputs':'scripted_fixture','events':events,'frame_count':frame,'evidence_verified':True})
        (root/'finish').touch()
        print(root,flush=True)
    finally:
        subprocess.run([TMUX,'-L',server,'kill-server'],env=env,capture_output=True)
    return root

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--scale',type=float,default=1)
    args=parser.parse_args()
    worker(args.worker) if args.worker else record(args.scale)
