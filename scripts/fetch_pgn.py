"""Download finished rounds of the 46th Olympiad (Open section) from Lichess broadcasts.

Lichess splits the event into several broadcasts by match range; this finds them all
and saves one PGN per broadcast per round into data/pgn/.
Run:  uv run python scripts/fetch_pgn.py
"""

import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

OUT = pathlib.Path(__file__).parent.parent / "data" / "pgn"
QUERY = "olympiad 2026"
ROUNDS = {"Round 1", "Round 2", "Round 3"}


def get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "olympiad-commentator-hackathon"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tours: dict[str, str] = {}
    for page in range(1, 6):
        found = json.loads(get(f"https://lichess.org/api/broadcast/search?page={page}&q={urllib.parse.quote(QUERY)}"))
        for r in found.get("currentPageResults", []):
            name = r["tour"]["name"]
            if "Olympiad" in name and "2026" in name and "| Open |" in name:
                tours[r["tour"]["id"]] = name
        if not found.get("nextPage"):
            break
    print(f"{len(tours)} Open broadcasts", file=sys.stderr)
    for tour_id, name in sorted(tours.items(), key=lambda kv: kv[1]):
        span = re.sub(r"[^0-9a-z]+", "-", name.split("|")[-1].strip().lower()).strip("-")
        for rnd in json.loads(get(f"https://lichess.org/api/broadcast/{tour_id}"))["rounds"]:
            if rnd["name"] in ROUNDS and rnd.get("finished"):
                path = OUT / f"open-{rnd['name'].lower().replace(' ', '')}-{span}.pgn"
                path.write_bytes(get(f"https://lichess.org/api/broadcast/round/{rnd['id']}.pgn", "application/x-chess-pgn"))
                print(f"{path.name}: {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
