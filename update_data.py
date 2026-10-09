
import json
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from io import StringIO

import pandas as pd
import requests

SEASON = date.today().year
HISTORY_START_SEASON = SEASON - 6
STATS_URL = ("https://evollve.net/stats/ncaa/women/indoor/volleyball/dig_in/player_single_seasons/"
             "?min_season={min_season}&max_season={max_season}&criteria_type=values"
             "&stats_to_show={stats_to_show}&submit=true")
ROSTER_BASE = "https://raw.githubusercontent.com/Sports-Roster-Data/womens-volleyball/main/data/"
HEADERS = {"User-Agent": "Toledo Volleyball data job (contact: you@example.com)"}
OUT_DIR = "data"
MIN_EXPECTED_ROWS = 1000   # if the stats page comes back broken, keep yesterday's file

ABBR = {"st": "state", "wash": "washington", "ariz": "arizona", "colo": "colorado", "fla": "florida",
        "ga": "georgia", "ky": "kentucky", "ill": "illinois", "mich": "michigan", "miss": "mississippi",
        "tenn": "tennessee", "tex": "texas", "caro": "carolina", "ala": "alabama", "ark": "arkansas",
        "conn": "connecticut", "ind": "indiana", "la": "louisiana", "mo": "missouri", "neb": "nebraska",
        "okla": "oklahoma", "ore": "oregon", "pa": "pennsylvania", "va": "virginia", "wis": "wisconsin",
        "so": "southern", "ut": "texas", "unc": "north carolina", "technology": "tech"}
STOP = {"university", "of", "the", "college", "at", "and", "institute", "univ", "u"}


def ascii_lower(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def norm_name(s):
    return " ".join(re.sub(r"[^a-z ]", "", ascii_lower(s).replace("-", " ")).split())


def team_match(a, b):
    def toks(s):
        out = []
        for w in re.sub(r"[^a-z ]", " ", ascii_lower(s).replace("-", " ")).split():
            out += [x for x in ABBR.get(w, w).split() if x not in STOP]
        return set(out)
    A, B = toks(a), toks(b)
    return len(A & B) / min(len(A), len(B)) if A and B else 0.0


def get_stats(stats_to_show="totals", min_season=SEASON, max_season=SEASON):
    url = STATS_URL.format(stats_to_show=stats_to_show,
                           min_season=min_season, max_season=max_season)
    r = requests.get(url, headers=HEADERS, timeout=180)
    r.raise_for_status()
    return pd.read_html(StringIO(r.text))[0]


def add_sets(stats):
    per_set = get_stats("per_set")
    keys = ["Season", "Player", "Team"]
    out = stats.merge(per_set[keys + ["Sets"]], on=keys, how="left", validate="one_to_one")
    if out["Sets"].isna().any():
        raise ValueError(f"Sets were unavailable for {out['Sets'].isna().sum()} players")
    sets = out.pop("Sets")
    out.insert(out.columns.get_loc("Matches") + 1, "Sets", sets)
    return out


def get_player_history(current_stats, rosters):
    current = current_stats[["Season", "Player", "Team"]]
    roster_history = rosters[["_season", "name", "team"]].rename(
        columns={"_season": "Season", "name": "Player", "team": "Team"})
    history = pd.concat([current, roster_history], ignore_index=True).drop_duplicates()
    history["Season"] = pd.to_numeric(history["Season"], errors="raise").astype(int)
    return history


def get_rosters():
    frames = []
    for s in range(SEASON, HISTORY_START_SEASON - 1, -1):
        try:
            frame = pd.read_csv(f"{ROSTER_BASE}rosters_{s}-{str(s + 1)[-2:]}.csv")
            frame["_season"] = s
            frames.append(frame)
        except Exception as e:
            print(f"Roster file for {s} unavailable: {e}")
    r = pd.concat(frames, ignore_index=True).drop_duplicates(["_season", "name", "team"])
    r["_key"] = r["name"].map(norm_name)
    return r


def add_hometowns(stats, rosters):
    by_name = {k: g for k, g in rosters.groupby("_key")}
    hometown, high_school, roster_team = [], [], []
    for player, team in zip(stats["Player"], stats["Team"]):
        cand = by_name.get(norm_name(player))
        pick = None
        if cand is not None:
            best = max(cand.index, key=lambda i: team_match(cand.at[i, "team"], team))
            if team_match(cand.at[best, "team"], team) >= 0.5 or cand["hometown"].nunique() <= 1:
                pick = cand.loc[best]
        hometown.append(None if pick is None else pick["hometown"])
        high_school.append(None if pick is None else pick["high_school"])
        roster_team.append(None if pick is None else pick["team"])
    out = stats.copy()
    out["Hometown"], out["High School"] = hometown, high_school
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    stats = add_sets(get_stats())
    if len(stats) < MIN_EXPECTED_ROWS:
        raise SystemExit(f"Only {len(stats)} rows returned; keeping the previous file.")
    rosters = get_rosters()
    out = add_hometowns(stats, rosters)
    out.to_csv(os.path.join(OUT_DIR, "all_players.csv"), index=False)
    history = get_player_history(stats, rosters)
    history.to_csv(os.path.join(OUT_DIR, "player_history.csv"), index=False)
    meta = {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "season": SEASON, "players": len(out),
            "with_hometown": int(out["Hometown"].notna().sum()),
            "history_start_season": int(history["Season"].min())}
    with open(os.path.join(OUT_DIR, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(meta)


if __name__ == "__main__":
    main()
