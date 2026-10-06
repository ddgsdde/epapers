"""Download redistributable fonts from their official repositories."""

from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1] / "assets/fonts"
root.mkdir(parents=True, exist_ok=True)
sources = {
    "NotoSansSC.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf",
    "NotoSerifSC.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf",
    "LICENSE-sans.txt": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/OFL.txt",
    "ChillRoundF.ttf": "https://raw.githubusercontent.com/Warren2060/ChillRound/main/ChillRound/ChillRoundF.ttf",
    "ChillRoundM.ttf": "https://raw.githubusercontent.com/Warren2060/ChillRound/main/ChillRound/ChillRoundM.ttf",
    "LICENSE-rounded.txt": "https://raw.githubusercontent.com/Warren2060/ChillRound/main/LICENSE",
    "LXGWWenKai-Regular.ttf": "https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/fonts/TTF/LXGWWenKai-Regular.ttf",
    "LXGWWenKai-Medium.ttf": "https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/fonts/TTF/LXGWWenKai-Medium.ttf",
    "LICENSE-wenkai.txt": "https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/OFL.txt",
    "Inter.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
    "LICENSE-inter.txt": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/OFL.txt",
    "BarlowCondensed-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/barlowcondensed/BarlowCondensed-Regular.ttf",
    "BarlowCondensed-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/barlowcondensed/BarlowCondensed-Bold.ttf",
    "LICENSE-condensed.txt": "https://raw.githubusercontent.com/google/fonts/main/ofl/barlowcondensed/OFL.txt",
    "LICENSE-serif.txt": "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/OFL.txt",
}
for name, url in sources.items():
    path = root / name
    if path.exists():
        continue
    with urlopen(url, timeout=60) as response:
        data = response.read()
    path.write_bytes(data)
    print(name, len(data))
