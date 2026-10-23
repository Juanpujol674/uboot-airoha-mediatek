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
