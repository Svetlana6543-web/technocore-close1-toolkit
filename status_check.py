#!/usr/bin/env python3
"""Watch the close-1 referee: latest sweep, reference, board, and traces of your trades."""

from __future__ import annotations

import argparse
import json

import technocore_agent as tca


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--did", default="", help="your did:key (optional)")
    parser.add_argument("--ids", default="", help="comma-separated trade ids to watch for (optional)")
    parser.add_argument("--base-url", default=tca.DEFAULT_BASE_URL)
    args = parser.parse_args()
    ids = {i.strip() for i in args.ids.split(",") if i.strip()}

    for room, limit in (("d-close1-price", 1), ("d-close1-state", 1), ("d-close1-pnl", 1)):
        r = tca.read_room(room, limit=limit, base_url=args.base_url)
        data = json.loads(r["messages"][-1]["text"])
        if room == "d-close1-price":
            print("PRICE", "sweep", data["n"], "| ref", data["ref"]["px"],
                  "| limits", data["limits"], "| global", data.get("global"),
                  "| posted", r["messages"][-1]["ts"])
        elif room == "d-close1-state":
            print("STATE", "owners", data["owners"], "| rooms", data["rooms"], "| n", data["n"])
        else:
            top = ", ".join(score for _, score in (data.get("top") or [])[:5])
            print("PNL  ", "mark", data.get("mark"), "| top scores:", top)

    flow = tca.read_room("d-close1-flow", limit=200, base_url=args.base_url)
    events, sweeps = [], []
    for message in flow["messages"]:
        try:
            data = json.loads(message["text"])
        except json.JSONDecodeError:
            continue
        sweeps.append(data.get("n"))
        text = message.get("text") or ""
        for entry in data.get("void") or []:
            if entry and (entry[0] in ids or entry[0] == args.did):
                events.append((data.get("n"), "void", entry))
        for minted in data.get("mints") or []:
            if minted == args.did:
                events.append((data.get("n"), "minted", minted))
    known = [n for n in sweeps if n is not None]
    if known:
        print(f"FLOW  scanned sweeps {min(known)}..{max(known)} ({len(known)} posts)")
    print("FLOW  events for you:", events if events else "none in the retained window")
    print("note: settled trades are usually cut from the room post (`omitted.settled`);")
    print("check the archive with archive_check.py for authoritative outcomes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
