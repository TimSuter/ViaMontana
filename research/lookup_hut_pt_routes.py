import csv
import argparse
import html
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "hut_inclusion.csv"
OUTPUT = ROOT / "research" / "hut_public_transport_route_lookup.csv"

USER_AGENT = "Mozilla/5.0 ViaMontana research"
SEARCH_URL = "https://lite.duckduckgo.com/lite/?q="

ACCESS_TERMS = (
    "public transport",
    "öffentliche verkehr",
    "oeffentliche verkehr",
    "öV",
    "anreise",
    "arrival",
    "access",
    "approach",
    "zustieg",
    "ausgangspunkt",
    "departure point",
    "postauto",
    "bus",
    "train",
    "bahn",
)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", "ignore")


def strip_tags(text):
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def ddg_results(query, limit=8):
    page = fetch(SEARCH_URL + urllib.parse.quote(query))
    rows = re.findall(
        r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>',
        page,
        flags=re.I | re.S,
    )
    results = []
    for href, title in rows:
        title = strip_tags(title)
        href = html.unescape(href)
        if "uddg=" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if href.startswith("//duckduckgo.com/l/?"):
            href = urllib.parse.parse_qs(urllib.parse.urlparse("https:" + href).query).get("uddg", [href])[0]
        if href.startswith("http") and title:
            results.append({"title": title, "url": href})
        if len(results) >= limit:
            break
    return results


def score_result(result):
    url = result["url"].lower()
    title = result["title"].lower()
    score = 0
    if "sac-cas.ch" in url and "route-portal" in url:
        score += 20
    if "/mountain-hiking/" in url or "/berg-und-alpinwandern/" in url:
        score += 10
    if any(word in title for word in ("from ", "vom ", "von ", "de ", "da ")):
        score += 3
    if any(term.lower() in title for term in ACCESS_TERMS):
        score += 3
    if any(domain in url for domain in ("schweizmobil.ch", "myswitzerland.com", "ticino.ch")):
        score += 2
    return score


def context_snippets(text):
    lowered = text.lower()
    snippets = []
    for term in ACCESS_TERMS:
        idx = lowered.find(term.lower())
        if idx >= 0:
            start = max(0, idx - 220)
            end = min(len(text), idx + 520)
            snippets.append(text[start:end])
    deduped = []
    for snippet in snippets:
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if snippet and snippet not in deduped:
            deduped.append(snippet)
    return deduped[:4]


def first_match(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def analyze_page(url):
    try:
        raw = fetch(url)
    except Exception as exc:
        return {"error": str(exc)}
    text = strip_tags(raw)
    title = first_match([r"<title[^>]*>(.*?)</title>"], raw)
    route_heading = first_match(
        [
            r"##?\s*(From .*?)(?: Difficulty| Ascent| Route description)",
            r"##?\s*(Vom .*?)(?: Schwierigkeit| Aufstieg| Routenbeschreibung)",
            r"##?\s*(Von .*?)(?: Schwierigkeit| Aufstieg| Routenbeschreibung)",
        ],
        text,
    )
    departure = first_match(
        [
            r"Departure point\s+([^*]{3,120}?)(?: Show on map| Get there| Waypoints| Remarks)",
            r"Ausgangspunkt\s+([^*]{3,120}?)(?: Auf Karte| Anreise| Wegpunkte| Bemerkungen)",
        ],
        text,
    )
    difficulty = first_match([r"Difficulty\s+([A-Z]?\d[+-]?)", r"Schwierigkeit\s+([A-Z]?\d[+-]?)"], text)
    ascent = first_match([r"Ascent\s+([^#]{1,80}?m)", r"Aufstieg\s+([^#]{1,80}?Hm)"], text)
    route_description = first_match(
        [
            r"Route description\s+(.{20,500}?)(?: Variant| Additional information| Feedback)",
            r"Routenbeschreibung\s+(.{20,500}?)(?: Variante| Zusatzinformationen| Rückmeldung)",
        ],
        text,
    )
    snippets = " | ".join(context_snippets(text))
    return {
        "title": title,
        "route_heading": route_heading,
        "departure": departure,
        "difficulty": difficulty,
        "ascent": ascent,
        "route_description": route_description,
        "access_snippets": snippets,
        "error": "",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0, help="Zero-based row offset in included hut list.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum huts to process.")
    parser.add_argument("--append", action="store_true", help="Append to an existing output CSV.")
    args = parser.parse_args()

    with INPUT.open(newline="", encoding="utf-8-sig") as f:
        huts = [row for row in csv.DictReader(f) if row.get("include_in_evaluation") == "1"]
    selected_huts = huts[args.start :]
    if args.limit is not None:
        selected_huts = selected_huts[: args.limit]

    mode = "a" if args.append and OUTPUT.exists() else "w"
    with OUTPUT.open(mode, newline="", encoding="utf-8") as f:
        fields = [
            "hut_index",
            "hut_name",
            "latitude",
            "longitude",
            "candidate_status",
            "pt_start_or_departure",
            "route_name",
            "duration_ascent_text",
            "difficulty",
            "route_summary",
            "source_title",
            "source_url",
            "notes",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        if mode == "w":
            writer.writeheader()

        for i, hut in enumerate(selected_huts, args.start + 1):
            name = hut["name"]
            queries = [
                f'"{name}" "Swiss Alpine Club" "Mountain hiking" "Departure point"',
                f'"{name}" Zustieg Anreise ÖV',
                f'"{name}" access public transport hut',
            ]
            found = []
            for query in queries:
                try:
                    found.extend(ddg_results(query))
                except Exception:
                    pass
                time.sleep(0.25)

            unique = {}
            for result in found:
                unique.setdefault(result["url"], result)
            candidates = sorted(unique.values(), key=score_result, reverse=True)

            chosen = None
            details = None
            for candidate in candidates[:5]:
                details = analyze_page(candidate["url"])
                searchable = " ".join(str(details.get(k, "")) for k in details)
                if details.get("departure") or any(term.lower() in searchable.lower() for term in ACCESS_TERMS):
                    chosen = candidate
                    break
                time.sleep(0.2)

            if not chosen and hut.get("website"):
                chosen = {"title": "hut website from input CSV", "url": hut["website"]}
                details = analyze_page(hut["website"])

            if chosen and details:
                status = "candidate_found" if details.get("departure") or details.get("route_heading") else "source_found_needs_manual_review"
                writer.writerow(
                    {
                        "hut_index": hut["hut_index"],
                        "hut_name": name,
                        "latitude": hut["latitude"],
                        "longitude": hut["longitude"],
                        "candidate_status": status,
                        "pt_start_or_departure": details.get("departure", ""),
                        "route_name": details.get("route_heading", ""),
                        "duration_ascent_text": details.get("ascent", ""),
                        "difficulty": details.get("difficulty", ""),
                        "route_summary": details.get("route_description", "")[:900],
                        "source_title": details.get("title") or chosen["title"],
                        "source_url": chosen["url"],
                        "notes": details.get("access_snippets", "")[:900] or details.get("error", ""),
                    }
                )
            else:
                writer.writerow(
                    {
                        "hut_index": hut["hut_index"],
                        "hut_name": name,
                        "latitude": hut["latitude"],
                        "longitude": hut["longitude"],
                        "candidate_status": "not_found",
                        "pt_start_or_departure": "",
                        "route_name": "",
                        "duration_ascent_text": "",
                        "difficulty": "",
                        "route_summary": "",
                        "source_title": "",
                        "source_url": "",
                        "notes": "No candidate source found in automated pass.",
                    }
                )
            f.flush()
            print(f"{i}/{len(huts)} {name}: {status if chosen else 'not_found'}", flush=True)


if __name__ == "__main__":
    main()
