"""Install a factory image on a verified blank FM25Q04 using the RAM programmer.

Never accesses OTP programming. Preserve a verified full-chip backup first.
The programmer must have been built with --suota --probe-flash --install-flash.
"""

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install(work, image, backup, toolchain):
    factory, original = image.read_bytes(), backup.read_bytes()
    if len(factory) != 0x39000 or len(original) != 524288 or set(original) != {255}:
        raise ValueError("Factory install requires a verified blank 512 KB backup")
    if (
        factory[:2] != b"pP"
        or factory[0x4000:0x4003] != b"pQ\xaa"
        or factory[0x38000:0x38002] != b"pR"
    ):
        raise ValueError("Invalid factory image")
    symbols = {}
    for line in subprocess.check_output(
        [str(toolchain / "arm-none-eabi-nm"), str(work / "build/tag.elf")], text=True
    ).splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[-1]] = int(fields[0], 16)
    box, buffer, diag = (symbols[k] for k in ("ep_flash_probe", "core", "ep_suota_diag"))
    directory = work / "install-evidence"
    directory.mkdir(exist_ok=True)
    request = 0

    def run(lines, name):
        script = directory / (name + ".jlink")
        script.write_text(
            "\n".join(
                ["device DA14585", "si SWD", "speed 1000", "connect", "h", *lines, "g", "exit"]
            )
            + "\n"
        )
        log = directory / (name + ".log")
        with log.open("w") as output:
            result = subprocess.run(
                ["/usr/local/bin/JLinkExe", "-ExitOnError", "1", "-CommanderScript", str(script)],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
        text = log.read_text()
        if result.returncode or "Error occurred" in text or "Cannot" in text:
            raise RuntimeError(f"J-Link failed: {log}")
        return text

    identity = run([f"mem32 0x{diag:x},1"], "identity")
    if not re.search(rf"{diag:08X} = 00A14013", identity):
        raise ValueError("Actual flash is not the qualified FM25Q04")

    def rpc(address, length, operation, name, data=None):
        nonlocal request
        request += 1
        lines = []
        if data is not None:
            source = directory / (name + ".bin")
            source.write_bytes(data)
            lines.append(f"loadbin {source},0x{buffer:x} noreset")
        lines += [
            f"w4 0x{box:x},0x{address:x}",
            f"w4 0x{box + 4:x},0x{length:x}",
            f"w4 0x{box + 20:x},{operation}",
            f"w4 0x{box + 8:x},0x{request:x}",
            "g",
            "sleep 700",
            "h",
            f"mem32 0x{box:x},6",
        ]
        readback = directory / (name + "-read.bin")
        if operation == 0:
            lines.append(f"savebin {readback},0x{buffer:x},0x{length:x}")
        text = run(lines, name)
        match = re.search(rf"{box:08X} = ([A-F0-9 ]+)\n{box + 16:08X} = ([A-F0-9 ]+)", text)
        if not match or [int(v, 16) for v in (match[1] + match[2]).split()] != [
            address,
            length,
            request,
            request,
            0,
            operation,
        ]:
            raise RuntimeError(f"Flash command failed: {name}")
        return readback.read_bytes() if operation == 0 else None

    # Product header is committed last; bank two and all other data are untouched.
    sectors = [a for a in range(0, len(factory), 4096) if set(factory[a : a + 4096]) != {255}]
    for address in sectors:
        desired = factory[address : address + 4096]
        before = rpc(address, 4096, 0, f"{address:05x}-before")
        if before != original[address : address + 4096]:
            raise ValueError("Flash changed since the backup; stopping before erase")
        rpc(address, 4096, 1, f"{address:05x}-erase")
        rpc(address, 4096, 2, f"{address:05x}-write", desired)
        if rpc(address, 4096, 0, f"{address:05x}-verify") != desired:
            raise RuntimeError(f"Readback mismatch at {address:05x}")
        print(f"Verified sector {address:05x}", flush=True)
    print("Factory image verified; reset to boot from flash.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument(
        "--toolchain",
        type=Path,
        default=ROOT / ".local/arm-gnu-toolchain-13.3.rel1-darwin-arm64-arm-none-eabi/bin",
    )
    options = parser.parse_args()
    install(
        options.work.resolve(),
        options.image.resolve(),
        options.backup.resolve(),
        options.toolchain.resolve(),
    )
