#!/usr/bin/env python3
"""Run the production FIT lookup and OS partition fixup against real libfdt.

Requires a host C compiler, libfdt-dev and dtc (the CI build installs these).
No target hardware, firmware downloads or NAND mutations are used.
"""
import ctypes
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def function(path, name):
    source = (ROOT / path).read_text()
    start = source.index(name + '(')
    start = source.rfind('\n', 0, start) + 1
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


with tempfile.TemporaryDirectory(prefix='hg5585f-os-') as tmp:
    tmp = Path(tmp)
    source = r"""
#include <libfdt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <stdint.h>
typedef uint32_t u32;
static const char *test_board = "an7581-fiberhome-hg5585f";
#define CONFIG_DEFAULT_DEVICE_TREE test_board
#define CONFIG_TARGET_AN7581 1
#define FIT_CONFS_PATH "/configurations"
#define FIT_DEFAULT_PROP "default"
#define debug(...) do {} while (0)
#define fit_get_debug(...) do {} while (0)
#define tools_build() 0
#define IS_ENABLED(x) 0
static int fit_find_config_node(const void *fit) { return -1; }
"""
    source += function('boot/image-fit.c', 'fit_conf_get_node') + '\n'
    source += function('arch/arm/mach-airoha/an7581/init.c',
                       'hg5585f_fixup_partitions') + '\n'
    source += function('drivers/mtd/airoha-pnand/airoha_pnand.c',
                       'airoha_pnand_geometry_spare').replace('static ', '', 1) + '\n'
    source += r"""
int fixup(void *blob) { return hg5585f_fixup_partitions(blob); }
void board(int hg) { test_board = hg ? "an7581-fiberhome-hg5585f" : "other"; }
"""
    (tmp / 'test.c').write_text(source)
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra',
                    '-Wno-unused-parameter', '-Wno-unused-function',
                    '-o', str(tmp / 'test.so'), str(tmp / 'test.c'), '-lfdt'],
                   check=True)
    lib = ctypes.CDLL(str(tmp / 'test.so'))
    lib.fit_conf_get_node.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.fixup.argtypes = [ctypes.c_void_p]
    lib.board.argtypes = [ctypes.c_int]
    lib.fdt_path_offset.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.fdt_getprop.argtypes = [ctypes.c_void_p, ctypes.c_int,
                               ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
    lib.fdt_getprop.restype = ctypes.c_void_p

    def dt(text):
        result = subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb'],
                                input=('/dts-v1/;\n' + text).encode(),
                                stdout=subprocess.PIPE, check=True)
        return ctypes.create_string_buffer(result.stdout)

    def prop(blob, path, name):
        node = lib.fdt_path_offset(blob, path.encode())
        assert node >= 0, path
        length = ctypes.c_int()
        ptr = lib.fdt_getprop(blob, node, name.encode(), ctypes.byref(length))
        assert ptr, (path, name)
        return ctypes.string_at(ptr, length.value)

    lib.airoha_pnand_geometry_spare.argtypes = [ctypes.c_uint32]
    lib.airoha_pnand_geometry_spare.restype = ctypes.c_uint32
    for physical, expected in [(15, 0), (16, 16), (25, 16), (26, 26),
                               (27, 27), (28, 28), (32, 28), (64, 28)]:
        assert lib.airoha_pnand_geometry_spare(physical) == expected

    for config in ['config-1', 'config@1']:
        blob = dt('/ { configurations { default = "' + config + '"; ' +
                  config + ' { kernel = "kernel-1"; }; }; };')
        assert lib.fit_conf_get_node(blob, b'config-1') >= 0
        assert lib.fit_conf_get_node(blob, None) >= 0
        assert lib.fit_conf_get_node(blob, b'nonexistent') < 0
        lib.board(0)
        assert (lib.fit_conf_get_node(blob, b'config-1') >= 0) == (config == 'config-1')
        lib.board(1)
    # An explicit existing selection always wins over the default.
    blob = dt('/ { configurations { default = "config@1"; '
              'config@1 {}; config-1 { marker = "selected"; }; }; };')
    node = lib.fit_conf_get_node(blob, b'config-1')
    assert node == lib.fdt_path_offset(blob, b'/configurations/config-1')
    # Do not hide a broken/missing default, or alias arbitrary configurations.
    blob = dt('/ { configurations { default = "other"; config@1 {}; }; };')
    assert lib.fit_conf_get_node(blob, b'config-1') < 0

    def partitions(offset=0x20000, size=0, compatible='fiberhome,hg5585f-ct', cells=1):
        return dt(f"""/ {{ compatible = "{compatible}";
          nand {{ partitions {{ #address-cells = <{cells}>; #size-cells = <1>;
            bl2 {{ label = "bl2"; reg = <0 {offset}>; }};
            ubi {{ label = "ubi"; reg = <{offset} {size}>;
              compatible = "linux,ubi";
              volumes {{ fit {{ volname = "fit"; phandle = <42>; }}; }};
            }};
          }}; }};
        }};""")

    for offset in [0x20000, 0x100000]:
        for size in [0, 0x10000000 - offset]:
            blob = partitions(offset, size)
            assert lib.fixup(blob) == 0
            assert prop(blob, '/nand/partitions/ubi', 'reg') == struct.pack(
                '>II', 0x100000, 0xff00000 if size else 0)
            assert prop(blob, '/nand/partitions/bl2', 'reg') == struct.pack('>II', 0, 0x100000)
            assert prop(blob, '/nand/partitions/ubi/volumes/fit', 'phandle') == struct.pack('>I', 42)
            assert lib.fixup(blob) == 0 # repeat fixup is harmless
    for args in [(0x40000, 0), (0x20000, 12345)]:
        blob = partitions(*args)
        before = blob.raw
        assert lib.fixup(blob) < 0
        assert blob.raw == before
    blob = partitions(compatible='other,board')
    before = blob.raw
    assert lib.fixup(blob) == 0 and blob.raw == before
    blob = partitions()
    before = blob.raw
    lib.board(0)
    assert lib.fixup(blob) == 0 and blob.raw == before
    lib.board(1)
    blob = dt('/ { compatible = "fiberhome,hg5585f-ct"; };')
    assert lib.fixup(blob) == 0
    print('HG5585F FIT selection and OS partition regression checks passed')
