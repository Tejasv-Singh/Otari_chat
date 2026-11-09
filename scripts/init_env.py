"""Create .env from .env.example with fresh random secrets.

Never prints a secret. Leaves an existing value alone, so it is safe to rerun.
Key format per otari docs/configuration.md "The secret": "tk-" + >=47 url-safe chars.
"""

from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATED = {
    "OTARI_MASTER_KEY": lambda: secrets.token_hex(32),
    "OTARI_PROVIDER_ACCOUNT_PEPPER": lambda: secrets.token_urlsafe(32),
    "R2R_KEY_VILLAGER": lambda: "tk-" + secrets.token_urlsafe(48),
    "R2R_KEY_OFFICIAL": lambda: "tk-" + secrets.token_urlsafe(48),
    "R2R_KEY_AMPLIFIER": lambda: "tk-" + secrets.token_urlsafe(48),
    "R2R_KEY_DDO": lambda: "tk-" + secrets.token_urlsafe(48),
    "OLLAMA_MODELS_DIR": lambda: (Path.home() / ".ollama" / "models").as_posix(),
}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def main() -> None:
    env_path = ROOT / ".env"
    current = read_env(env_path)
    out: list[str] = []
    filled: list[str] = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            value = current.get(key, "")
            if not value and key in GENERATED:
                value = GENERATED[key]()
                filled.append(key)
            out.append(f"{key}={value}")
        else:
            out.append(line)
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {env_path} (generated: {', '.join(filled) or 'nothing new'})")
    if not current.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is empty: add it to .env to enable cloud failover.")


if __name__ == "__main__":
    main()
