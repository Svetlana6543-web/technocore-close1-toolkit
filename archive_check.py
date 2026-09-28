#!/usr/bin/env python3
"""Verify your close-1 account against the official sweep-record archive.

The referee publishes each sweep's record (the fold's input and output) at
https://challenges.technocore.chat/close-1/ — index.json maps every sweep to a
path; output.minted lists every key minted that sweep; input.trades/output.trades
hold every applied trade and its outcome (settled with fees, or void with a
reason). This tool checks a mint and searches ranges of sweeps for your trades,
then reconstructs the cash/position arithmetic for simple (open-from-fresh-mint)
cases.

Two things the archive will NOT tell you, so the tool flags them:

- an offer that was never countersigned leaves no record at all (it silently
  expires) — "no trace" is only conclusive for sweeps whose records are `full`;
- sweeps with status `redacted` replaced private-room trades (input and output)
  with {"redacted":"private room"}, so a trade posted in a private room is
  invisible even to an id search; index.json carries each redacted file's own
  sha256 and the number of redacted trades.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from decimal import Decimal

DEFAULT_ARCHIVE = "https://challenges.technocore.chat/close-1/"


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "close1-archive-check"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(60_000_000)


def parse_ranges(text: str):
    for part in filter(None, text.split(",")):
        if "-" in part:
            low, high = part.split("-", 1)
            yield from range(int(low), int(high) + 1)
        else:
            yield int(part)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--did", required=True, help="your did:key (z6Mk...)")
    parser.add_argument("--ids", default="", help="comma-separated trade ids to search (optional)")
    parser.add_argument("--mint", type=int, help="sweep number to check output.minted for --did")
    parser.add_argument("--ranges", default="", help='sweep ranges to scan, e.g. "405-465,660-720"')
    parser.add_argument("--mint-amount", default="10000", help="starting balance for reconstruction")
    parser.add_argument("--archive", default=DEFAULT_ARCHIVE, help="archive base URL")
    args = parser.parse_args()

    ids = {i.strip() for i in args.ids.split(",") if i.strip()}
    index = json.loads(get(args.archive + "index.json"))
    sweeps = {s["n"]: s for s in index["sweeps"]}
    print(f"archive covers sweeps {min(sweeps)}..{max(sweeps)}")

    if args.mint is not None:
        if args.mint not in sweeps:
            raise SystemExit(f"error: sweep {args.mint} is not in the archive")
        record = json.loads(get(args.archive + sweeps[args.mint]["path"]).decode("utf-8"))
        minted = record["output"]["minted"]
        print(f"sweep {args.mint}: minted {len(minted)} keys | our DID minted: {args.did in minted}")

    if not args.ranges:
        return 0

    numbers = list(parse_ranges(args.ranges))
    missing = [n for n in numbers if n not in sweeps]
    if missing:
        print(f"warning: {len(missing)} requested sweeps are not in the archive "
              f"(e.g. {missing[:5]}) — outcomes there cannot be checked")

    hits, scanned, redacted = [], 0, 0
    needles = {args.did, *ids}
    for n in numbers:
        if n not in sweeps:
            continue
        raw = get(args.archive + sweeps[n]["path"]).decode("utf-8")
        scanned += 1
        if sweeps[n]["status"] == "redacted":
            redacted += 1
        if not any(needle in raw for needle in needles):
            continue
        record = json.loads(raw)
        ins, outs = record["input"]["trades"], record["output"]["trades"]
        seen = set()
        for i, trade in enumerate(ins):
            if not isinstance(trade, dict):
                continue  # {"redacted":"private room"} placeholder
            if trade.get("id") not in ids and trade.get("maker") != args.did \
                    and trade.get("countersigner") != args.did:
                continue
            key = (n, trade.get("id"), i)
            if key in seen:
                continue
            seen.add(key)
            hits.append({
                "sweep": n, "status": sweeps[n]["status"],
                "input": trade, "output": outs[i] if i < len(outs) else None,
                "sweep_close": record["input"].get("close"),
            })

    print(f"scanned {scanned} sweeps ({redacted} redacted); hits: {len(hits)}")
    for h in hits:
        print(json.dumps(h, ensure_ascii=False))

    settled = [h for h in hits if h["output"] and h["output"].get("outcome") == "settled"]
    voids = [h for h in hits if h["output"] and h["output"].get("outcome") == "void"]
    if settled or voids:
        print("\n=== simple reconstruction (opens from a fresh mint; FIFO closings are not modelled) ===")
        cash = Decimal(args.mint_amount)
        position = Decimal(0)
        entries = []
        for h in sorted(settled, key=lambda x: x["sweep"]):
            trade, out = h["input"], h["output"]
            is_maker = trade.get("maker") == args.did
            side = 1 if trade.get("side") == "buy" else -1
            my_side = side if is_maker else -side
            qty, px = Decimal(trade["qty"]), Decimal(trade["px"])
            fee = Decimal(out["maker_fee"] if is_maker else out["taker_fee"])
            cash -= fee
            if my_side > 0:
                cash -= qty * px
                position += qty
                entries.append((qty, px))
            else:
                cash += qty * px
                position -= qty
                entries.append((-qty, px))
            print(f"sweep {h['sweep']}: {'maker' if is_maker else 'taker'} "
                  f"{'buy' if my_side > 0 else 'sell'} {qty} @ {px} | fee {fee} | cash {cash} | position {position}")
        for h in voids:
            print(f"sweep {h['sweep']}: void ({h['output'].get('reason')}) — no cost")
        print(f"\ncash {cash} | position {position} | entries {entries}")
        if entries and position != 0:
            print("score at final S = cash + S * (position if long) ... minus nothing — "
                  "value_at(S) = cash + sum(q*S) for longs / q*(2*entry - S) for shorts")
    elif not ids:
        print("no trades found (note: an offer that was never countersigned leaves no record at all)")
    else:
        print("no trace of the given ids — conclusive only for full sweeps; "
              f"{redacted} of {scanned} scanned sweeps were redacted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
