# HG5585F parallel NAND boot recovery

## Scope and evidence

This change targets the HG5585F with S34ML02G300TFI00 parallel NAND. It is a
recovery candidate, not a hardware-validated release. The reported log reaches
BL23, then fails to read both UBI volume tables and the FIP. U-Boot has not run
at that point. DRAM training completes, but that alone is not a full RAM test.

The NAND table already matches the reference project: ID `01:da:469500`,
256 MiB capacity, 2048-byte pages, 128-byte physical OOB, 128 KiB eraseblocks,
and controller ECC4 per 512 bytes. Changing ECC strength would change the
on-flash encoding and is not justified by this log.

## Changes

* Reserve the first `0x100000` bytes for BL2 and start UBI at `0x100000`, matching
  `OVERRIDE_UBI_START_ADDR` in the ATF build script. Previously U-Boot started
  UBI at `0x20000`. This mismatch is real, but does not by itself explain every
  NAND read error in the log.
* Keep the boot-stage NAND timing on HG5585F, matching the EN7581 ATF policy.
  Other boards retain their existing timing policy.
* Apply `tools/atf-patches/hg5585f-pnand-ubi.patch` when building this board from
  ATF source. Parallel NAND UBI metadata and bad-block queries use the existing
  full-page path instead of the single-sector shortcut. SPI NAND retains its
  shortcut. This is a conservative workaround for investigation; scanning will
  be slower and hardware testing must establish whether it resolves the failure.
* Initialize the ATF read-length diagnostic and use the declared `uint32_t`
  type. The underlying vendor read API does not populate this output, so the
  previous multi-gigabyte counts were garbage. Stop printing that unsupported
  count and include NAND status in errors.

The ATF patch is checked before application and is idempotent. An incompatible
ATF source fails the build rather than silently omitting the workaround. It
modifies the sibling ATF working tree; commit identity alone does not describe
the resulting binary. The patch is included in this repository for review.

## Build

On a Linux host with the dependencies from README.md:

```sh
git clone --branch atf-v2.10 https://github.com/Yuzhii0718/atf-airoha.git ../atf-airoha
SOC=an7581 BOARD=fiberhome_hg5585f BLOBS=source ./airoha.sh
```

Use a full source build, not `BLOBS=prebuilt` or `STAGE=uboot`: existing blobs
do not receive the BL23 patch. Build both the preloader and `bl31-uboot.fip`
from the same run. The dedicated HG5585F verification workflow builds these
images against ATF commit `5beb2e5371` and uploads artifacts without publishing
a release. A successful compile does not establish NAND compatibility.

## Recovery and validation

1. Keep the original NAND/board-data backups. Do not erase NAND or rebuild UBI
   just to test whether the new FIP can execute.
2. At `Press 'x' to load BL31 + U-Boot FIP via XMODEM`, press `x` and use
   Tera Term's **File > Transfer > XMODEM > Send** to send the matching
   `*-bl31-uboot.fip`. The `C` characters are the receiver's normal CRC request.
   Send the FIP, not `preloader.bin` or raw `u-boot.bin`.
3. This first RAM load still uses the preloader currently installed on the
   device. It tests the FIP/U-Boot and provides access for inspection; it does
   not test the newly patched BL23 NAND reads.
4. Once in U-Boot, capture its full NAND identification, geometry, ECC and UBI
   messages. Verify the detected chip and the `0x100000` UBI boundary before
   planning any persistent update. Capture all errors from read-back verification.
5. Changing the UBI partition boundary on an existing installation requires a
   backup/migration plan. Do not blindly rebuild UBI: it deletes volumes,
   including any board data stored there. An existing UBI formatted from
   `0x20000` cannot be assumed safe to reuse from `0x100000`, especially with
   fastmap or volume metadata in the excluded eraseblocks.
6. A persistent test needs the matching patched preloader and FIP. After the
   update, capture a full cold-boot log. Success means BL23 reads the `fip`
   volume, reaches the U-Boot banner, and repeated cold boots/read-back checks
   succeed. Further ECC/read errors require hardware-specific investigation;
   repeated erases are not a substitute for that evidence.

## Sources

* [Reference project](https://github.com/pbs05/uboot-an758x)
* [Reference NAND identification table](https://github.com/pbs05/uboot-an758x/blob/main/tf-a/plat/ecnt/common/drivers/flash/parallel_nand_flash_table.c)
* [ATF build flags](https://github.com/Yuzhii0718/atf-airoha/blob/5beb2e5371/build.sh)
* [ATF UBI glue](https://github.com/Yuzhii0718/atf-airoha/blob/5beb2e5371/plat/ecnt/en7523/bl2_boot_nand_ubi.c)

## OS boot follow-up (2026-10-24)

The subsequent serial log reaches U-Boot a75ce4bf from NAND and verifies all
three FIT image hashes. It contains two independent OS boot failures:

* `Could not find configuration node 'config-1'`: the tested QWRT 5.4.302
  and ponwrt 6.18.52 images default to `config@1`. The ponwrt 6.18.55 image
  defaults to `config-1`. The HG5585F-only lookup alias accepts the old
  environment's `config-1` only when that node is absent and the FIT explicitly
  defaults to `config@1`. Existing and other explicit selections stay strict.
* A Linux 6.18.52 session reaches `Starting kernel`, then reports ECC -74 while
  reading both UBI volume tables, bad CRC in the factory record and a VFS root
  mount panic. This remains an unresolved kernel/NAND interoperability issue.
  A successful build or FIT hash check does not prove Linux can read the NAND.

All three locally inspected firmware DTBs use BL2 size / UBI start `0x20000`.
The installed BL23 and this recovery branch use `0x100000`. The OS DT fixup now
updates just those two `reg` properties before Linux starts, preserving the
volumes subtree, rootdisk references and phandles. It supports only the known
HG5585F layouts on this HG5585F build, and refuses unfamiliar layouts. It does
not migrate data or alter NAND ECC. The next serial log should report
`HG5585F: OS NAND partitions use BL23 UBI offset 0x100000` and Linux's ubi
partition should start at `0x000000100000`.

The supplied Actions run 39948175300 built pbs05/ponwrt commit `8ef1bd9`.
Its downstream EN7581 patch already protects 8 FDM bytes and disables the
bad-marker swap; it must not be confused with unmodified upstream mtk_nand.
However, it selects the largest supported spare size within the chip geometry:
for 2048-byte pages / 128-byte OOB this is **28 bytes per 512-byte sector**.
The current U-Boot vendor backend selects **16 bytes per sector for BCH4**.
The pbs05 U-Boot reference driver also selects 28 bytes for this geometry.
This is a concrete on-flash page-format mismatch despite the same BCH4 strength.
See the [tested kernel patch](https://github.com/pbs05/ponwrt/blob/8ef1bd9/target/linux/airoha/patches-6.18/141-mtd-rawnand-mtk-add-Airoha-EN7581-support.patch)
and [reference U-Boot driver](https://github.com/pbs05/uboot-an758x/blob/main/drivers/mtd/nand/raw/airoha_en7581_nand.c).

Do not change U-Boot's encoding on the installed NAND as an isolated adjustment:
BL23, FIP, environment and board-data volumes must remain readable by every boot
stage. A complete remedy requires either firmware using the existing 16-byte
layout, or a coordinated boot-stage migration with verified 16/28-byte reading
and backups. This follow-up deliberately does not implement or claim that
migration. It fixes the independent FIT-name and OS partition-view defects.

The next necessary evidence is the firmware source/release link and a new
serial log after the configuration/partition fixes. If ECC/volume-table
errors remain, firmware built with a compatible AN7581 parallel-NAND driver
is required; the two U-Boot fixes alone do not establish a working system.
There is no justification for erasing/rebuilding UBI again at this stage.

Host regression checks exercise the production FIT lookup and partition fixup
against real libfdt, including both configuration names, explicit selection,
other boards, repeat execution, phandle preservation and rejected layouts:

```
python3 test/airoha/hg5585f-os-compat.py
```

## Separate pbs05 page-layout candidate

Build the explicit `BOARD=fiberhome_hg5585f_pbs` profile with `BLOBS=source`.
It uses the same board DTS and environment, but enables
`CONFIG_AIROHA_PARALLEL_NAND_GEOMETRY_SPARE`. U-Boot and the separately patched
BL23 choose the largest supported spare stride within physical OOB geometry:
16, 26, 27 or 28 bytes per sector. On S34ML02G300TFI00 this means BCH4/512,
8-byte FDM protected by BCH, and **28-byte spare stride** in both stages,
matching pbs05 U-Boot and the tested ponwrt source. BCH strength is unchanged.
The boot log now states the exact page format.

```
SOC=an7581 BOARD=fiberhome_hg5585f_pbs BLOBS=source ./airoha.sh
```

The original `fiberhome_hg5585f` profile keeps its 16-byte BCH4 spare layout.
Switching the shared ATF source back to it removes the PBS stride patch; smoke
tests cover applying, repeated application and removing that patch. Prebuilt
ATF and U-Boot-only builds are rejected for the PBS profile. CI builds both
profiles in separate jobs and saves distinctly named candidate artifacts.

**The PBS profile is not a drop-in FIP replacement for the current 16-byte UBI.**
Do not flash the new preloader/FIP or reformat NAND based only on build success.
The observed original-firmware backup is a set of MTD partition images plus a
separate factory image (11 files, 248512512 bytes including that extra factory
copy); it is not a 256 MiB NAND image with OOB. Before migration, inventory and
back up the currently readable UBI volumes, their names/IDs/sizes and board
calibration/identity data. Keep these private; they must not enter the Git repo.
Prepare an off-device recovery path and a consistent new preloader/FIP/UBI plan
before any erase. Neither this code nor the CI performs device migration.

The PBS candidate corrects the confirmed stride mismatch in code. Its ability
to initialize this board, preserve board data through migration, and boot all
three tested firmwares still requires hardware validation. The two firmware
images without available source may contain additional driver differences.

## RAM-only diagnostic profile (not an installer)

`BOARD=fiberhome_hg5585f_pbs_ramdiag` uses the PBS page geometry, but keeps its
environment only in RAM, disables automatic boot and UBI/Web recovery, and
rejects page programming and block erase inside the parallel NAND driver.
MTD also advertises the device as read-only. OOB writes and bad-block marking
share the guarded primitives. Tests execute those actual functions against
stubbed hardware and assert that no write/erase call reaches the hardware.
CI checks the resolved configuration, not just the input defconfig.

Load ONLY its `*-bl31-uboot*.fip` through the EXISTING preloader's XMODEM
recovery prompt. Do not flash it, and do not install a diagnostic preloader.
CI deliberately excludes the diagnostic preloader from the artifact.
The existing preloader remains responsible for DRAM and the serial transfer.
At the console run only `version`, `mtd list`, and optionally `ping` to a PC
on the same Ethernet subnet. Capture the complete serial log. Power cycling
returns to the installed boot chain. Do not boot Linux or invoke raw register
writes: the U-Boot driver guards cannot constrain another operating system
or arbitrary code. This is diagnostic protection, not a security boundary.

This first test checks entry into U-Boot, NAND identification/selected format,
and Ethernet. It cannot prove that the new BL23 can cold-boot the NAND, or
that Linux can mount a newly written 28-byte UBI. Existing 16-byte content is
not expected to decode using the new 28-byte layout. No migration commands
are provided until those separate write/recovery assumptions are validated.

The supplied `factory-1m.bin` is a converted board-data image, not a native
1 MiB stock partition. pbs05/fiberhome-factory defines this 1 MiB format;
ponwrt documents restoring it to the `factory` UBI volume. A 1 MiB dynamic
volume rounds up to nine 126976-byte LEBs (1142784 bytes); that capacity does
not by itself imply missing board data or require altering the 1 MiB file.
Source: https://github.com/pbs05/fiberhome-factory and
https://github.com/pbs05/ponwrt/blob/master/README_zh.md
