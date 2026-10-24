// SPDX-License-Identifier: GPL-2.0

#include <fdtdec.h>
#include <init.h>
#include <stdio.h>
#include <linux/string.h>
#include <log.h>
#ifdef CONFIG_OF_SYSTEM_SETUP
#include <fdt_support.h>
#endif
#include <linux/bitops.h>
#include <linux/io.h>
#include <linux/kconfig.h>
#include <linux/sizes.h>
#include <sysreset.h>
#include <asm/armv8/mmu.h>
#include <asm/global_data.h>
#include <asm/system.h>
#include <soc/airoha/pkgids.h>

DECLARE_GLOBAL_DATA_PTR;

/*
 * DRAM always starts at 0x80000000. The 32-bit window covers
 * [0x80000000, 0x100000000) and the 64-bit window continues from
 * 0x100000000 up to 0x280000000, see an7581_mem_map below.
 */
#define AN7581_DRAM_BASE			0x80000000UL
#define AN7581_DRAM_MAX_SIZE			(SZ_4G + SZ_4G)

/*
 * The first-stage bootloader initializes and trains DRAM, then detects its
 * total size using address aliasing. The detected size is stored in the
 * NP-SCU global parameter register (SYS_GLOBAL_PARM at 0x1fb00284) in units
 * of 16 MiB, using the same layout as EN7523/AN7563.
 *
 * Reading this saved value is preferable to trying to derive the capacity
 * from the DRAMC RKCFG register, whose RKSIZE and RKMODE fields describe the
 * controller rank configuration rather than the detected memory size.
 */
#define AN7581_SYS_GLOBAL_PARM			0x1fb00284UL
#define AN7581_SYS_GLOBAL_DRAM_SIZE_MASK	GENMASK(29, 20)
#define AN7581_SYS_GLOBAL_DRAM_SIZE_SHIFT	20
#define AN7581_SYS_GLOBAL_DRAM_SIZE_UNIT	SZ_16M

int print_cpuinfo(void)
{
	u32 hir = get_pkg();
	u32 pdidr = get_pdidr();
	u32 pkgid = get_pkgid();
	const char *soc_name = airoha_soc_name_from_regs(hir, pkgid, pdidr);

	if (pkgid != END_PACKAGE_ID) {
		printf("SoC:   Airoha %s\n", soc_name);
	} else {
		printf("SoC:   Airoha AN7581\n");
	}
	printf("DRAM:  %s\n", airoha_dram_type_name());

	return 0;
}

/*
 * Read the DRAM size detected by the first-stage bootloader.
 *
 * Return: the detected size, 0 when the register is not populated or holds
 *	   an out of range value.
 */
static phys_size_t an7581_dram_size_from_scu(void)
{
	void __iomem *sys_global = (void __iomem *)AN7581_SYS_GLOBAL_PARM;
	phys_size_t size;
	u32 units;
	u32 value;

	value = readl(sys_global);
	units = (value & AN7581_SYS_GLOBAL_DRAM_SIZE_MASK) >>
		AN7581_SYS_GLOBAL_DRAM_SIZE_SHIFT;
	size = (phys_size_t)units * AN7581_SYS_GLOBAL_DRAM_SIZE_UNIT;

	log_debug("AN7581 DRAM: SCU global param %08x, %u units, %llu MiB\n",
		  value, units, (unsigned long long)(size >> 20));

	if (!units || size > AN7581_DRAM_MAX_SIZE)
		return 0;

	return size;
}

int dram_init(void)
{
	phys_size_t size;

	gd->ram_base = AN7581_DRAM_BASE;

	size = an7581_dram_size_from_scu();

	if (size) {
		gd->ram_size = size;

		log_debug("AN7581 DRAM: base=%llx size=%llu MiB\n",
			  (unsigned long long)gd->ram_base,
			  (unsigned long long)(gd->ram_size >> 20));

		return 0;
	}

	/* Last resort, trust whatever the device tree describes. */
	log_warning("AN7581 DRAM: hardware detection failed, falling back to the device tree\n");

	return fdtdec_setup_mem_size_base();
}

int dram_init_banksize(void)
{
	phys_size_t size = get_effective_memsize();
	int bank;

	gd->dram[0].start = gd->ram_base;
	gd->dram[0].size = size > SZ_2G ? SZ_2G : size;

	for (bank = 1; bank < CONFIG_NR_DRAM_BANKS; bank++) {
		gd->dram[bank].start = 0;
		gd->dram[bank].size = 0;
	}

	/*
	 * Everything above the 32-bit window lives at 0x100000000 and is
	 * published as a second bank so that the whole range is reported.
	 */
	if (CONFIG_NR_DRAM_BANKS > 1 && gd->ram_size > SZ_2G) {
		gd->dram[1].start = gd->ram_base + SZ_2G;
		gd->dram[1].size = gd->ram_size - SZ_2G;
	}

	return 0;
}

#ifdef CONFIG_OF_SYSTEM_SETUP
/* Keep the OS view consistent with the installed HG5585F BL23/U-Boot.
 * Update properties in place: retain the volumes subtree and its phandles.
 * This does not move, erase or reformat any NAND contents.
 */
static int hg5585f_fixup_partitions(void *blob)
{
	int node = -1, depth = 0, ubi = -1, bl2 = -1, parent = -1, len;
	const char *label;
	const fdt32_t *reg;
	fdt32_t newreg[2];
	int ret;

	if (strcmp(CONFIG_DEFAULT_DEVICE_TREE, "an7581-fiberhome-hg5585f") ||
	    (fdt_node_check_compatible(blob, 0, "fiberhome,hg5585f-ct") &&
	     fdt_node_check_compatible(blob, 0, "fiberhome,hg5585f-cu") &&
	     fdt_node_check_compatible(blob, 0, "fiberhome,hg5585f")))
		return 0;

	while ((node = fdt_next_node(blob, node, &depth)) >= 0) {
		label = fdt_getprop(blob, node, "label", &len);
		if (label && len == sizeof("ubi") &&
		    !memcmp(label, "ubi", sizeof("ubi"))) {
			ubi = node;
			parent = fdt_parent_offset(blob, node);
			break;
		}
	}
	if (ubi < 0)
		return 0; /* e.g. a RAM recovery image without NAND partitions */
	if (fdt_address_cells(blob, parent) != 1 ||
	    fdt_size_cells(blob, parent) != 1)
		return -FDT_ERR_BADNCELLS;
	fdt_for_each_subnode(node, blob, parent) {
		label = fdt_getprop(blob, node, "label", &len);
		if (label && len == sizeof("bl2") &&
		    !memcmp(label, "bl2", sizeof("bl2")))
			bl2 = node;
	}
	reg = fdt_getprop(blob, ubi, "reg", &len);
	if (!reg || len != sizeof(newreg) || bl2 < 0 ||
	    (fdt32_to_cpu(reg[0]) != 0x20000 &&
	     fdt32_to_cpu(reg[0]) != 0x100000))
		return -FDT_ERR_BADVALUE;
	/* A zero size means the remainder of the NAND in these OpenWrt DTs. */
	if (fdt32_to_cpu(reg[1]) != 0 &&
	    fdt32_to_cpu(reg[1]) != 0x10000000 - fdt32_to_cpu(reg[0]))
		return -FDT_ERR_BADVALUE;
	newreg[0] = cpu_to_fdt32(0x100000);
	newreg[1] = reg[1] ? cpu_to_fdt32(0xff00000) : 0;
	reg = fdt_getprop(blob, bl2, "reg", &len);
	if (!reg || len != sizeof(newreg) || fdt32_to_cpu(reg[0]) ||
	    (fdt32_to_cpu(reg[1]) != 0x20000 &&
	     fdt32_to_cpu(reg[1]) != 0x100000))
		return -FDT_ERR_BADVALUE;
	ret = fdt_setprop_inplace(blob, ubi, "reg", newreg, sizeof(newreg));
	if (ret)
		return ret;
	newreg[0] = 0;
	newreg[1] = cpu_to_fdt32(0x100000);
	ret = fdt_setprop_inplace(blob, bl2, "reg", newreg, sizeof(newreg));
	if (!ret)
		printf("HG5585F: OS NAND partitions use BL23 UBI offset 0x100000\n");
	return ret;
}

int ft_system_setup(void *blob, struct bd_info *bd)
{
	u64 start[1] = { gd->ram_base };
	u64 size[1] = { gd->ram_size };
	int ret;

	(void)bd;

	ret = hg5585f_fixup_partitions(blob);
	if (ret)
		return ret;

	/* Publish the detected size, the DT one may be wrong or missing. */
	return fdt_fixup_memory_banks(blob, start, size, 1);
}
#endif

void reset_cpu(void)
{
	psci_system_reset();
}

static struct mm_region an7581_mem_map[] = {
	{
		/* DDR, 32-bit area */
		.virt = 0x80000000UL,
		.phys = 0x80000000UL,
		.size = SZ_2G,
		.attrs = PTE_BLOCK_MEMTYPE(MT_NORMAL) | PTE_BLOCK_OUTER_SHARE,
	}, {
		/* DDR, 64-bit area */
		.virt = 0x100000000UL,
		.phys = 0x100000000UL,
		.size = SZ_4G + SZ_2G,
		.attrs = PTE_BLOCK_MEMTYPE(MT_NORMAL) | PTE_BLOCK_OUTER_SHARE,
	}, {
		.virt = 0x00000000UL,
		.phys = 0x00000000UL,
		.size = 0x40000000UL,
		.attrs = PTE_BLOCK_MEMTYPE(MT_DEVICE_NGNRNE) |
			 PTE_BLOCK_NON_SHARE |
			 PTE_BLOCK_PXN | PTE_BLOCK_UXN
	}, {
		/* List terminator */
	}
};
struct mm_region *mem_map = an7581_mem_map;
