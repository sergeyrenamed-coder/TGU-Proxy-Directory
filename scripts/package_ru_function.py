"""Package a Yandex Cloud Function without credentials, from repository root."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tgu-ru-checker-function.zip"

if __name__ == "__main__":
    with ZipFile(OUT, "w", ZIP_DEFLATED) as archive:
        for source, destination in [
            ("ru_checker/main.py", "main.py"),
            ("scripts/collect.py", "scripts/collect.py"),
            ("scripts/__init__.py", "scripts/__init__.py"),
            ("requirements.txt", "requirements.txt"),
        ]:
            archive.write(ROOT / source, destination)
    print("Created", OUT)
