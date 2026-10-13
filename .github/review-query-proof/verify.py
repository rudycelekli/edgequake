#!/usr/bin/env python3
"""Exact-source native crate gate; no production substitution or test filtering."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def evaluate(text, exit_code):
    if exit_code != 0:
        raise ValueError('Native Cargo producer did not succeed')
    if 'Doc-tests edgequake_query' not in text or 'Running unittests' not in text:
        raise ValueError('Complete crate unit and documentation targets were not reached')
    summaries = re.findall(
        r'^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',
        text, re.M,
    )
    if not summaries or any(s[0] != 'ok' or int(s[2]) != 0 or int(s[5]) != 0 for s in summaries):
        raise ValueError('Missing, failed or unexpectedly filtered native execution summary')
    total = {name: sum(int(s[i]) for s in summaries)
             for i, name in enumerate(('passed', 'failed', 'ignored', 'measured', 'filtered'), start=1)}
    if total['passed'] <= 0:
        raise ValueError('No native tests passed')
    return {'targets_with_execution_summaries': len(summaries), **total}


def self_test():
    good = ('Running unittests src/lib.rs\n'
            'test result: ok. 7 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.1s\n'
            'Doc-tests edgequake_query\n'
            'test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.1s\n')
    assert evaluate(good, 0)['passed'] == 9
    faults = [(good, 1), ('', 0), (good.replace('Doc-tests edgequake_query', ''), 0),
              (good.replace('7 passed', '0 passed').replace('2 passed', '0 passed'), 0),
              (good.replace('0 failed', '1 failed'), 0),
              (good.replace('0 filtered out', '1 filtered out'), 0)]
    for text, code in faults:
        try:
            evaluate(text, code)
        except ValueError:
            continue
        raise AssertionError('Evaluator accepted fault')
    print(json.dumps({'accepted_healthy_controls': 1, 'rejected_fault_controls': len(faults)}))


def main():
    root = Path.cwd()
    proof = Path(__file__).resolve().parent
    manifest = json.loads((proof / 'source.json').read_text())
    out = Path(os.environ['PROOF_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    receipt = {'status': 'RUNNING', 'source': manifest, 'command': ['cargo', 'test', '--locked', '-p', 'edgequake-query']}

    def save():
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')

    def guard():
        assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == manifest['head']
        assert subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip() == manifest['tree']
        assert not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
        for name, wanted in manifest['sha256'].items():
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == wanted, name

    save()
    try:
        guard()
        receipt['sourceGuardBefore'] = 'PASS'
        receipt['freeBytesBefore'] = shutil.disk_usage(root).free
        assert receipt['freeBytesBefore'] >= 1024 ** 3
        for name, command in [('rustc', ['rustc', '--version', '--verbose']), ('cargo', ['cargo', '--version'])]:
            text = subprocess.check_output(command, text=True)
            (out / (name + '.txt')).write_text(text)
            receipt[name] = text.strip()
        assert receipt['rustc'].startswith('rustc 1.95.0 ')
        env = os.environ.copy()
        removed = []
        for name in list(env):
            if name.endswith(('_API_KEY', '_ACCESS_KEY', '_SECRET_ACCESS_KEY')) or name in ('OPENAI_KEY', 'ANTHROPIC_KEY'):
                removed.append(name)
                env.pop(name)
        receipt['removedCredentialVariableNames'] = sorted(removed)
        env['EDGEQUAKE_PERF_REAL_LLM'] = '0'
        env['DATABASE_URL'] = ''
        save()
        with (out / 'cargo-test.log').open('w') as log:
            process = subprocess.Popen(receipt['command'], cwd=root / 'edgequake', env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                log.write(line)
                print(line, end='', flush=True)
            code = process.wait()
        receipt['producerExit'] = code
        receipt['nativeExecution'] = evaluate((out / 'cargo-test.log').read_text(), code)
        guard()
        receipt['sourceGuardAfter'] = 'PASS'
        receipt['freeBytesAfter'] = shutil.disk_usage(root).free
        receipt['status'] = 'PASS'
    except Exception as error:
        receipt['status'] = 'FAIL'
        receipt['error'] = str(error)
        raise
    finally:
        save()
        hashes = {file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                  for file in out.iterdir() if file.is_file() and file.name != 'artifact-file-hashes.json'}
        (out / 'artifact-file-hashes.json').write_text(json.dumps(hashes, indent=2) + '\n')


if __name__ == '__main__':
    self_test() if sys.argv[1:] == ['--self-test'] else main()
