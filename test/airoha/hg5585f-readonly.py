#!/usr/bin/env python3
"""Exercise actual NAND primitives with stubbed hardware, and audit RAM config."""
import argparse
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'drivers/mtd/airoha-pnand/airoha_pnand.c').read_text()

def function(name):
    start = source.index('\nstatic int ' + name + '(') + 1
    pos = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[start:pos]

code = r'''
#include <stdint.h>
#include <errno.h>
typedef unsigned char u8;
typedef uint32_t u32;
typedef long long loff_t;
struct airoha_pnand_priv { unsigned char *pagebuf; unsigned fdm_bytes;
    struct { unsigned page_size; } info; };
static int calls, failure;
#define IS_ENABLED(x) (x)
#define SPI_NAND_FLASH_RTN_NO_ERROR 0
#define pr_err(...) do {} while (0)
static int parallel_nand_dma_write(int a, u32 b, unsigned long *c, unsigned d, u8 *e)
{ calls++; return failure; }
static int parallel_nand_erase(loff_t addr) { calls++; return failure; }
'''
code += function('airoha_pnand_write_page') + '\n'
code += function('airoha_pnand_erase_block') + '\n'
code += r'''
int main(void) {
    struct airoha_pnand_priv p = { .info.page_size = 2048 };
    int w = airoha_pnand_write_page(&p, 0, 0, 0);
    int e = airoha_pnand_erase_block(&p, 0);
    if (IS_ENABLED(CONFIG_AIROHA_PARALLEL_NAND_READ_ONLY))
        return !(w == -EROFS && e == -EROFS && calls == 0);
    if (w || e || calls != 2) return 1;
    failure = 1;
    return !(airoha_pnand_write_page(&p, 0, 0, 0) == -EIO &&
             airoha_pnand_erase_block(&p, 0) == -EIO && calls == 4);
}
'''
with tempfile.TemporaryDirectory(prefix='hg5585f-readonly-') as td:
    td = Path(td)
    (td / 'test.c').write_text(code)
    for ro in [0, 1]:
        binary = td / ('test' + str(ro))
        subprocess.run(['cc', '-Werror', '-o', str(binary),
                        '-DCONFIG_AIROHA_PARALLEL_NAND_READ_ONLY=' + str(ro),
                        str(td / 'test.c')], check=True)
        subprocess.run([str(binary)], check=True)
print('PASS: read-only primitives reject without hardware calls; normal paths preserved')

parser = argparse.ArgumentParser()
parser.add_argument('--config', type=Path)
args = parser.parse_args()
if args.config:
    text = args.config.read_text()
    values = dict(line.split('=', 1) for line in text.splitlines()
                  if line.startswith('CONFIG_') and '=' in line)
    for key in ['ENV_IS_NOWHERE', 'AIROHA_PARALLEL_NAND_READ_ONLY',
                'AIROHA_PARALLEL_NAND_GEOMETRY_SPARE', 'CMD_MTD']:
        assert values.get('CONFIG_' + key) == 'y', key
    for key in ['ENV_IS_IN_UBI', 'ENV_IS_IN_MTD', 'ENV_UBI_VOLUME_CREATE',
                'CMD_UBI', 'MTD_UBI', 'AIROHA_WEB_FAILSAFE', 'AUTOBOOT_MENU_SHOW']:
        assert values.get('CONFIG_' + key) != 'y', key
    assert values['CONFIG_BOOTDELAY'] == '-1'
    assert values['CONFIG_ENV_DEFAULT_ENV_TEXT_FILE'] == '"defenvs/an7581_fiberhome_hg5585f_ramdiag_env"'
    env = (root / 'defenvs/an7581_fiberhome_hg5585f_ramdiag_env').read_text()
    assert 'bootdelay=-1\n' in env
    assert all(word not in env for word in ['ubi ', 'saveenv', 'httpd', 'bootm '])
    print('PASS: resolved RAM diagnostic configuration has no UBI environment or automatic boot')
