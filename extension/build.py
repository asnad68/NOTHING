from __future__ import annotations
import argparse, json, shutil, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def build(browser: str) -> Path:
    if browser not in {"chrome", "firefox"}:
        raise ValueError(browser)
    manifest_name = f"manifest.{browser}.json"
    dist = ROOT / "dist" / browser
    if dist.exists(): shutil.rmtree(dist)
    dist.mkdir(parents=True)
    for item in ROOT.iterdir():
        if item.name in {"dist", "manifest.chrome.json", "manifest.firefox.json", "build.py", "README.md"}:
            continue
        target = dist / item.name
        if item.is_dir(): shutil.copytree(item, target)
        else: shutil.copy2(item, target)
    shutil.copy2(ROOT / manifest_name, dist / "manifest.json")
    pkg = ROOT / "dist" / f"nothing-business-identity-{browser}-0.1.0.zip"
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(dist.rglob("*")):
            if path.is_file(): z.write(path, path.relative_to(dist))
    return pkg

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("browser", choices=["chrome", "firefox", "all"])
    args = ap.parse_args()
    targets = ["chrome", "firefox"] if args.browser == "all" else [args.browser]
    out = [str(build(b)) for b in targets]
    print(json.dumps({"packages": out}, indent=2))

if __name__ == "__main__": main()
