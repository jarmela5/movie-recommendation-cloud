
import os
from pathlib import Path
import secrets

path = Path(__file__).resolve().parents[1] / ".env"
existing = path.read_text() if path.exists() else ""
if any(line.strip().startswith("INTERNAL_API_TOKEN=") for line in existing.splitlines()):
    print("INTERNAL_API_TOKEN already configured in .env; unchanged.")
else:
    with path.open("a") as stream:
        stream.write("\nINTERNAL_API_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    os.chmod(path, 0o600)
    print("Created INTERNAL_API_TOKEN in ignored .env. Do not commit this file.")
