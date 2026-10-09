import csv
import json
import mimetypes
import os
import re
import unicodedata
from collections import defaultdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "all_players.csv"
HISTORY_FILE = ROOT / "data" / "player_history.csv"
PORTAL_FILE = "index.html"
HOST = os.environ.get("VOLLEYBALL_HOST", "127.0.0.1")
PORT = int(os.environ.get("VOLLEYBALL_PORT", "8765"))
MAX_REQUEST_BYTES = 1_000_000


def normalize_name(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z ]", " ", value.lower().replace("-", " ")).split())


class PlayerIndex:
    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.columns = []
        self.by_name = defaultdict(list)
        self.name_lengths = []

    def load_if_needed(self):
        mtime = self.path.stat().st_mtime_ns
        if mtime == self.mtime:
            return

        by_name = defaultdict(list)
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            for row in reader:
                by_name[normalize_name(row.get("Player", ""))].append(row)

        self.columns = columns
        self.by_name = by_name
        self.name_lengths = sorted({len(name.split()) for name in by_name if len(name.split()) >= 2}, reverse=True)
        self.mtime = mtime

    def search(self, name):
        self.load_if_needed()
        key = normalize_name(name)
        matches = self.by_name.get(key, [])

        if not matches:
            parts = key.split()
            if len(parts) >= 2:
                first, last = parts[0], parts[-1]
                candidates = [
                    row
                    for candidate_name, rows in self.by_name.items()
                    if first in candidate_name.split() and last in candidate_name.split()
                    for row in rows
                ]
                if len(candidates) == 1:
                    matches = candidates

        if not matches:
            return None

        return max(matches, key=lambda row: (number(row.get("Sets")), number(row.get("Matches"))))

    def extract_names(self, text):
        self.load_if_needed()
        found = []
        seen = set()
        for source_line in text.splitlines():
            words = normalize_name(source_line).split()
            index = 0
            while index < len(words):
                match = None
                for length in self.name_lengths:
                    if index + length > len(words):
                        continue
                    candidate = " ".join(words[index:index + length])
                    if candidate in self.by_name:
                        match = candidate
                        break
                if match:
                    if match not in seen:
                        player = self.search(match)
                        found.append({"query": player.get("Player", match),
                                      "source_line": source_line, "player": player})
                        seen.add(match)
                    index += len(match.split())
                else:
                    index += 1
        return found


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1


PLAYERS = PlayerIndex(DATA_FILE)


class HistoryIndex:
    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.by_name = defaultdict(list)

    def load_if_needed(self):
        mtime = self.path.stat().st_mtime_ns
        if mtime == self.mtime:
            return
        by_name = defaultdict(list)
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                by_name[normalize_name(row.get("Player", ""))].append(row)
        self.by_name = by_name
        self.mtime = mtime

    def years_played(self, player_name, current_team):
        try:
            self.load_if_needed()
        except FileNotFoundError:
            return None

        records = self.by_name.get(normalize_name(player_name), [])
        if not records:
            return None

        teams_by_season = defaultdict(set)
        for row in records:
            teams_by_season[row.get("Season", "")].add(normalize_name(row.get("Team", "")))

        if any(len(teams) > 1 for teams in teams_by_season.values()):
            team_key = normalize_name(current_team)
            records = [row for row in records if normalize_name(row.get("Team", "")) == team_key]

        seasons = {row.get("Season") for row in records if row.get("Season")}
        return len(seasons) or None


HISTORY = HistoryIndex(HISTORY_FILE)


class VolleyballHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        relative = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        if not relative:
            relative = PORTAL_FILE
        resolved = (ROOT / relative).resolve()
        if ROOT not in resolved.parents and resolved != ROOT:
            return str(ROOT / "__not_found__")
        return str(resolved)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/search":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            PLAYERS.load_if_needed()
            results = []
            text = payload.get("text")
            if isinstance(text, str):
                if len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
                    raise ValueError("Pasted text is too large")
                extracted = PLAYERS.extract_names(text)
                for item in extracted:
                    player = item["player"]
                    years = HISTORY.years_played(player["Player"], player["Team"])
                    results.append({"query": item["query"], "source_line": item["source_line"],
                                    "found": True, "years_played": years, "player": player})
            else:
                names = payload.get("names")
                if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                    raise ValueError("Provide pasted 'text' or a 'names' array")
                if len(names) > 1000:
                    raise ValueError("A maximum of 1,000 names can be searched at once")
                for name in names:
                    cleaned = " ".join(name.split())
                    player = PLAYERS.search(cleaned) if cleaned else None
                    years = HISTORY.years_played(player["Player"], player["Team"]) if player else None
                    results.append({"query": cleaned, "source_line": name,
                                    "found": player is not None, "years_played": years,
                                    "player": player})

            self.send_json({"columns": PLAYERS.columns, "results": results})
        except FileNotFoundError:
            self.send_json({"error": "Player data is unavailable. Run update_data.py first."}, 503)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)
        except Exception as error:
            self.send_json({"error": f"Search failed: {error}"}, 500)

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def main():
    mimetypes.add_type("text/csv", ".csv")
    server = ThreadingHTTPServer((HOST, PORT), VolleyballHandler)
    print(f"Volleyball portal running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
