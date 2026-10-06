"""Build SDK6's DA14585 BLE peripheral with vendor GNU startup, then our overlays.

The licensed SDK remains local; no SDK sources or binary library are vendored.
"""

import argparse
import secrets
import shutil
import struct
import subprocess
import time
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--sdk", type=Path, default=ROOT / ".local/sdk6/DA145xx_SDK/6.0.20.1338")
parser.add_argument("--work", type=Path, default=ROOT / ".local/tag-project")
parser.add_argument("--lto", action="store_true")
parser.add_argument("--suota", action="store_true")
parser.add_argument(
    "--probe-flash", action="store_true", help="Read-only RAM diagnostic; never writes flash"
)
parser.add_argument(
    "--install-flash",
    action="store_true",
    help="Explicit RAM factory programmer; requires --suota --probe-flash",
)
parser.add_argument(
    "--toolchain",
    type=Path,
    default=ROOT / ".local/arm-gnu-toolchain-13.3.rel1-darwin-arm64-arm-none-eabi/bin",
)
args = parser.parse_args()
sdk = args.sdk.resolve()
project = sdk / "projects/target_apps/ble_examples/ble_app_peripheral"
work = args.work.resolve()
work.mkdir(parents=True, exist_ok=True)
shutil.copytree(project / "src", work / "src", dirs_exist_ok=True)
overlay = ROOT / "firmware/tag/src"
if overlay.exists():
    shutil.copytree(overlay, work / "src", dirs_exist_ok=True)
tree = ET.parse(project / "Keil_5/ble_app_peripheral.uvprojx")
target = tree.getroot().find("./Targets/Target")


def resolve(value):
    path = (project / "Keil_5" / value.replace("\\", "/")).resolve()
    if path.is_relative_to(project / "src"):
        return work / "src" / path.relative_to(project / "src")
    return path


sources = []
for node in target.findall(".//File"):
    if node.findtext(".//IncludeInBuild") == "0":
        continue
    path = resolve(node.findtext("FilePath"))
    if path.suffix == ".c" and path.name != "user_custs1_impl.c":
        sources.append(path)
includes = [
    resolve(p) for p in target.findtext(".//Cads/VariousControls/IncludePath").split(";") if p
]
includes += [
    sdk / "sdk/common_project_files",
    sdk / "sdk/common_project_files/ldscripts",
    work / "src/config",
    ROOT / "firmware/common",
]
# No board GPIO assumptions for RAM BLE bring-up. Preserve SWD, touch no panel pins.
periph = work / "src/platform/user_periph_setup.c"
periph.write_text("""#include "user_periph_setup.h"
#include "gpio.h"
#include "arch.h"
#include "syscntl.h"
void GPIO_reservations(void) {
 RESERVE_GPIO(PANEL_POWER,GPIO_PORT_2,GPIO_PIN_3,PID_GPIO);
 RESERVE_GPIO(PANEL_CS,GPIO_PORT_2,GPIO_PIN_1,PID_GPIO);
 RESERVE_GPIO(PANEL_IO20,GPIO_PORT_2,GPIO_PIN_0,PID_GPIO);
 RESERVE_GPIO(PANEL_IO00,GPIO_PORT_0,GPIO_PIN_0,PID_GPIO);
 RESERVE_GPIO(PANEL_IO01,GPIO_PORT_0,GPIO_PIN_1,PID_GPIO);
 RESERVE_GPIO(PANEL_IO05,GPIO_PORT_0,GPIO_PIN_5,PID_GPIO);
 RESERVE_GPIO(PANEL_IO06,GPIO_PORT_0,GPIO_PIN_6,PID_GPIO);
 RESERVE_GPIO(PANEL_IO07,GPIO_PORT_0,GPIO_PIN_7,PID_GPIO);
 RESERVE_GPIO(PANEL_IO10,GPIO_PORT_1,GPIO_PIN_0,PID_GPIO);
 RESERVE_GPIO(PANEL_IO11,GPIO_PORT_1,GPIO_PIN_1,PID_GPIO);
}
void set_pad_functions(void) {}
void periph_init(void) { SetBits16(PMU_CTRL_REG, PERIPH_SLEEP, 0); while (!(GetWord16(SYS_STAT_REG)&PER_IS_UP)) {} SetBits16(CLK_16M_REG,XTAL16_BIAS_SH_ENABLE,1); patch_func(); set_pad_functions(); SetBits16(SYS_CTRL_REG,PAD_LATCH_EN,1); }
""")
cfg = work / "src/config/user_config.h"
text = cfg.read_text().replace("ARCH_EXT_SLEEP_OTP_COPY_ON", "ARCH_EXT_SLEEP_ON")
text = text.replace("DLG-PRPH", "epapers-tag")
text = text.replace(".max_mtu = 23", ".max_mtu = 247")
text = text.replace("MS_TO_TIMERUNITS(1250)", "MS_TO_TIMERUNITS(2000)")
text = text.replace("APP_CFG_ADDR_PUB", "APP_CFG_ADDR_STATIC")
# Leave the SDK's all-zero address unset. The callback below derives a stable
# per-chip address from manufacturing OTP data instead of embedding one board's
# address in a firmware image shared by every screen.
if ".addr = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00}" not in text:
    raise ValueError("SDK peripheral configuration must have an unset device address")
text = text.replace(
    "\\x59\\x5A\\x08\\xE4\\x86\\x2A\\x9E\\x8F\\xE9\\x11\\xBC\\x7C\\x98\\x43\\x42\\x18",
    "\\x01\\xe0\\xbe\\x1b\\xa4\\x94\\xb4\\x9c\\x48\\x4c\\x31\\x6e\\x01\\x00\\xa0\\xe3",
)
cfg.write_text(text)
advanced = work / "src/config/da14585_config_advanced.h"
advanced.write_text(
    advanced.read_text()
    .replace("#define CFG_RET_DATA_SIZE    (2048)", "#define CFG_RET_DATA_SIZE    (34560)")
    .replace("// #define MSG_HEAP_SZ             6880", "#define MSG_HEAP_SZ             4096")
)
callback = work / "src/config/user_callback_config.h"
callback.write_text(
    callback.read_text().replace(
        ".app_on_db_init_complete            = default_app_on_db_init_complete",
        ".app_on_db_init_complete            = user_app_db_init_complete",
    ).replace(
        ".app_on_generate_static_random_addr = default_app_generate_static_random_addr",
        ".app_on_generate_static_random_addr = default_app_generate_unique_static_random_addr",
    )
)
if "= default_app_generate_unique_static_random_addr," not in callback.read_text():
    raise ValueError("SDK peripheral must use the per-chip stable address callback")
if args.suota:
    args.lto = True
    periph.write_text(
        periph.read_text().replace(
            " RESERVE_GPIO(PANEL_POWER",
            " RESERVE_GPIO(FLASH_CS,GPIO_PORT_0,GPIO_PIN_3,PID_GPIO);\n RESERVE_GPIO(PANEL_POWER",
            1,
        )
    )
    for file, before, after in [
        ("user_profiles_config.h", "#define CFG_PRF_DISS", "#define CFG_PRF_SUOTAR"),
        (
            "user_modules_config.h",
            "EXCLUDE_DLG_SUOTAR          (1)",
            "EXCLUDE_DLG_SUOTAR          (0)",
        ),
        ("da14585_config_basic.h", "#undef CFG_SPI_FLASH_ENABLE", "#define CFG_SPI_FLASH_ENABLE"),
        # The retained payload is 35,064 bytes. Reserve 35,328, keeping the
        # complete payload/heaps/1,536-byte stack and room for address setup.
        ("da14585_config_advanced.h", "(34560)", "(35328)"),
        (
            "da14585_config_advanced.h",
            "CFG_MAX_TX_PACKET_LENGTH        (251)",
            "CFG_MAX_TX_PACKET_LENGTH        (27)",
        ),
        (
            "da14585_config_advanced.h",
            "CFG_MAX_RX_PACKET_LENGTH        (251)",
            "CFG_MAX_RX_PACKET_LENGTH        (27)",
        ),
    ]:
        path = work / "src/config" / file
        path.write_text(path.read_text().replace(before, after))
    callback.write_text(
        callback.read_text().replace(
            '#include "user_peripheral.h"',
            '#include "user_peripheral.h"\n#include "ep_suota.h"\n#include "app_suotar.h"\nstatic const struct app_suotar_cb user_app_suotar_cb = { .on_suotar_status_change = ep_suota_status };',
        )
    )
    sources += [
        sdk / "sdk/ble_stack/profiles/suota/suotar/src/suotar.c",
        sdk / "sdk/ble_stack/profiles/suota/suotar/src/suotar_task.c",
        work / "src/ep_suota.c",
    ]
    includes += [sdk / "sdk/ble_stack/profiles/suota/suotar/api", work / "src"]
    suota_app = sdk / "sdk/app_modules/src/app_suotar/app_suotar.c"
    generated = work / "src/app_suotar.c"
    source = suota_app.read_text().replace(
        '#include "app_suotar.h"', '#include "app_suotar.h"\n#include "ep_suota.h"'
    )
    source = source.replace(
        "if(  suota_state.crc_calc != 0 )",
        "if(suota_state.crc_calc != 0 || !ep_suota_verify_image())",
    )
    source = source.replace(
        "uint8_t mem_dev_cmd = (uint8_t) ((mem_dev & SUOTAR_READ_MEM_DEV_TYPE) >> 24);",
        "uint8_t mem_dev_cmd = (uint8_t) ((mem_dev & SUOTAR_READ_MEM_DEV_TYPE) >> 24);\n    if (mem_dev_cmd == SUOTAR_IMG_SPI_FLASH && (mem_dev & 0xFFFFFF)) { suotar_send_status_update_req(SUOTAR_INVAL_IMG_BANK); return; }",
    )
    source = source.replace(
        "spi_conf->clk.port      =",
        "suota_state.gpio_map = 0x05060300; /* fixed board pins, never accept arbitrary GPIOs */\n    spi_conf->clk.port      =",
    )
    source = source.replace(
        "if( data_len < sizeof(image_header_t) )",
        "if(!ep_suota_valid_header(data, data_len)) return SUOTAR_INVAL_IMG_HDR;\n    if( data_len < sizeof(image_header_t) )",
    )
    source = source.replace(
        "if( SUOTAR_IS_FOR_IMAGE( suota_state.mem_dev ) )",
        "if(suota_state.mem_dev == SUOTAR_IMG_SPI_FLASH && ep_suota_diag.active)",
        1,
    )
    source = source.replace(
        "memset(mem_data_buff, 0xFF, sizeof(mem_data_buff));",
        "new_bank = ep_suota_diag.bank == 1 ? SECOND_IMAGE_BANK : FISRT_IMAGE_BANK;\n    memset(mem_data_buff, 0xFF, sizeof(mem_data_buff));",
        1,
    )
    source = source.replace(
        "#if DEVELOPMENT_DEBUG", "#if DEVELOPMENT_DEBUG && !defined(EP_TAG_SUOTA)"
    )
    generated.write_text(source)
    sources = [generated if p == suota_app else p for p in sources]
    (work / "src/ep_build.h").write_text(
        f'#define EP_BUILD_VERSION "0.3.3-{secrets.token_hex(4)}"\n'
    )
boot = sdk / "sdk/platform/arch/boot/GCC"
sources += [
    boot / "startup_DA14585_586.S",
    boot / "ivtable_DA14585_586.S",
    ROOT / "firmware/common/ep_core.c",
    work / "src/panel.c",
]
cmake = f'''cmake_minimum_required(VERSION 3.20)
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_C_COMPILER "{args.toolchain}/arm-none-eabi-gcc")
set(CMAKE_ASM_COMPILER "{args.toolchain}/arm-none-eabi-gcc")
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
project(epapers_tag C ASM)
add_executable(tag.elf {" ".join('"' + str(p) + '"' for p in sources)})
target_include_directories(tag.elf PRIVATE {" ".join('"' + str(p) + '"' for p in includes)})
target_compile_options(tag.elf PRIVATE -mcpu=cortex-m0 -mthumb -Os -g3 -fshort-enums -fshort-wchar -ffunction-sections -fdata-sections -fno-strict-aliasing -Wno-address-of-packed-member -Wno-incompatible-pointer-types "$<$<COMPILE_LANGUAGE:C>:-includeda1458x_config_basic.h>" "$<$<COMPILE_LANGUAGE:C>:-includeda1458x_config_advanced.h>" "$<$<COMPILE_LANGUAGE:C>:-includeuser_config.h>")
target_compile_definitions(tag.elf PRIVATE __DA14585__ __START=main)
add_custom_command(OUTPUT "${{CMAKE_BINARY_DIR}}/tag.ld" COMMAND "{args.toolchain}/arm-none-eabi-gcc" -E -P -x c {" ".join('-I"' + str(p) + '"' for p in includes)} "{sdk}/sdk/common_project_files/ldscripts/ldscript_DA14585_586.lds.S" -o "${{CMAKE_BINARY_DIR}}/tag.ld" COMMAND python3 "{ROOT}/scripts/align_tag_linker.py" "${{CMAKE_BINARY_DIR}}/tag.ld" DEPENDS "{cfg}" "{ROOT}/scripts/align_tag_linker.py")
add_custom_target(linker DEPENDS "${{CMAKE_BINARY_DIR}}/tag.ld")
add_dependencies(tag.elf linker)
target_link_options(tag.elf PRIVATE -mcpu=cortex-m0 -mthumb -nostartfiles --specs=nano.specs --specs=nosys.specs -Wl,--gc-sections -Wl,-Map=${{CMAKE_BINARY_DIR}}/tag.map -T${{CMAKE_BINARY_DIR}}/tag.ld -Wl,-L,{sdk}/sdk/common_project_files/misc)
target_link_libraries(tag.elf PRIVATE "{sdk}/sdk/platform/system_library/output/Keil_5/da14585_586.lib" c gcc)
add_custom_command(TARGET tag.elf POST_BUILD COMMAND "{args.toolchain}/arm-none-eabi-size" tag.elf COMMAND "{args.toolchain}/arm-none-eabi-objcopy" -O binary tag.elf tag.bin)
'''
if args.lto:
    cmake += "\ntarget_compile_options(tag.elf PRIVATE -flto)\ntarget_link_options(tag.elf PRIVATE -flto)\n"
if args.suota:
    cmake += "\ntarget_compile_definitions(tag.elf PRIVATE EP_TAG_SUOTA)\n"
if args.probe_flash:
    if not args.suota:
        parser.error("--probe-flash requires --suota")
    cmake += "\ntarget_compile_definitions(tag.elf PRIVATE EP_TAG_FLASH_PROBE)\n"
if args.install_flash:
    if not args.probe_flash or not args.suota:
        parser.error("--install-flash requires --suota --probe-flash")
    cmake += "\ntarget_compile_definitions(tag.elf PRIVATE EP_TAG_FLASH_INSTALL)\n"
(work / "CMakeLists.txt").write_text(cmake)
subprocess.run(["cmake", "-S", str(work), "-B", str(work / "build"), "-G", "Ninja"], check=True)
subprocess.run(["cmake", "--build", str(work / "build"), "-j", "6"], check=True)
rows = subprocess.check_output(
    [str(args.toolchain / "arm-none-eabi-nm"), str(work / "build/tag.elf")], text=True
).splitlines()
symbols = {v.split()[-1]: int(v.split()[0], 16) for v in rows if len(v.split()) == 3}
ram_script = (
    (ROOT / ".local/hardware/tag-ram.jlink")
    if work == ROOT / ".local/tag-project"
    else work / "tag-ram.jlink"
)
ram_script.write_text(f"""device DA14585
si SWD
speed 1000
connect
r
h
w2 0x50003300,0x0008
loadfile {work}/build/tag.elf
w2 0x50000012,0x00a2
wreg MSP 0x{symbols["__StackTop"] & ~7:08x}
SetPC 0x{symbols["Reset_Handler"] & ~1:08x}
g
sleep 1000
h
regs
g
exit
""")
if args.suota and not args.probe_flash:
    code = (work / "build/tag.bin").read_bytes()
    version = (work / "src/ep_build.h").read_text().split('"')[1].encode("ascii")
    if len(code) > 0x18000 - 64:
        raise ValueError("Application is too large for a SUOTA bank")
    header = struct.pack(
        "<2sBBII16sIB31s",
        b"pQ",
        0xAA,
        0,
        len(code),
        zlib.crc32(code),
        version,
        int(time.time()),
        0,
        b"\xff" * 31,
    )
    image = header + code
    (work / "build/tag.img").write_bytes(image)
    # Official SDK secondary bootloader stays local. Never program OTP.
    boot = (
        sdk / "config/toolbox_resources/DA14585-586/common/secondary_bootloader_585.bin"
    ).read_bytes()
    if len(boot) + 8 > 0x4000:
        raise ValueError("Secondary bootloader overlaps image bank")
    factory = bytearray(b"\xff" * 0x39000)
    factory[:8] = b"pP" + b"\x00" * 3 + len(boot).to_bytes(3, "big")
    factory[8 : 8 + len(boot)] = boot
    factory[0x4000 : 0x4000 + len(image)] = image
    factory[0x38000:0x3800C] = struct.pack("<2s2sII", b"pR", b"\x00\x00", 0x4000, 0x1F000)
    (work / "build/tag-factory.bin").write_bytes(factory)
    print(f"SUOTA {version.decode()} CRC {zlib.crc32(code):08x}, {len(image)} bytes")
