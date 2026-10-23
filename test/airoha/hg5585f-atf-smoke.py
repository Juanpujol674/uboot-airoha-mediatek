#!/usr/bin/env python3
"""Exercise the real airoha.sh ATF patch/build integration with a mock compiler.

Usage: python3 test/airoha/hg5585f-atf-smoke.py ../atf-airoha [--shell bash]
This checks patch integration, not target compilation or physical NAND I/O.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('atf', type=Path)
    parser.add_argument('--shell', default='bash')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    relative = Path('plat/ecnt/en7523/bl2_boot_nand_ubi.c')
    original = (args.atf / relative).read_bytes()
    script = (repo / 'airoha.sh').read_text()
    body = script.split('build_atf_source() {', 1)[1].split('\n}\n', 1)[0]

    with tempfile.TemporaryDirectory(prefix='hg5585f-atf-', dir=repo.parent) as directory:
        root = Path(directory)
        atf = root / 'atf'
        source = atf / relative
        source.parent.mkdir(parents=True)
        source.write_bytes(original)
        builder = atf / 'build.sh'
        builder.write_text(
            '#!/bin/sh\nset -eu\nmkdir -p output/an7581\n'
            'printf mock-bl2 > output/an7581/an7581-bl2.bin\n'
            'printf mock-bl31 > output/an7581/an7581-bl31.lzma\n',
            newline='\n')
        builder.chmod(0o755)
        blobs = root / 'blobs'
        harness = '''
set -eu
step() { :; }
info() { :; }
error() { echo "$*" >&2; }
die() { error "$*"; exit 1; }
check_atf_tree() { :; }
# File-size display is unrelated to patch/build integration.
stat() { printf '0\\n'; }
STAGE=all BLOB_SOURCE=source ATF_SOC=an7581 SOC_FAMILY=an7581
SOC_UPPER=AN7581 BLOB_VARIANT=an7581_default
UBOOT_DIR="$1" ATF_DIR="$2" BLOBS_SOURCE_DIR="$3" BOARD="$4"
build_atf_source() {
''' + body + '\n}\nbuild_atf_source\n'

        def run(board='fiberhome_hg5585f', success=True):
            result = subprocess.run(
                [args.shell, '-c', harness, 'smoke', repo.as_posix(),
                 'atf', 'blobs', board], cwd=root,
                capture_output=True, text=True, encoding='utf-8', errors='replace')
            assert (result.returncode == 0) == success, result.stdout + result.stderr

        run()
        patched = source.read_bytes()
        assert patched != original
        assert b'nand_ubi_can_use_partial_read' in patched
        assert (blobs / 'an7581_default/bl2.bin').read_bytes() == b'mock-bl2'
        run()
        assert source.read_bytes() == patched, 'second build must be idempotent'
        print('PASS: patch applied, blobs staged, repeated build idempotent')

        source.write_bytes(original)
        run(board='evb-pnand')
        assert source.read_bytes() == original, 'other board must not apply patch'
        print('PASS: other board source remains unchanged')

        source.write_text('/* incompatible ATF revision */\n')
        shutil.rmtree(blobs)
        run(success=False)
        assert not blobs.exists(), 'incompatible ATF must fail before staging blobs'
        print('PASS: incompatible ATF fails before build/staging')


if __name__ == '__main__':
    main()
