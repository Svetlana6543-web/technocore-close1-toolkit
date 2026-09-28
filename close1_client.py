#!/usr/bin/env python3
"""Close-1 client: register, build and sign offers, accept terms, read the referee.

Dry-run by default: nothing is posted or signed against the network without
--post. Signing uses an encrypted Ed25519 identity.pem (PKCS8) through the
vendored technocore_agent module, so the passphrase never appears on the
command line.

Trade protocol (see close-call-game.md in flop-labs/technocore-close-call-challenge):

  terms     = {"id":…,"maker":did,"px":"…","qty":"…","side":"buy|sell","taker":"any"|did,"until":<sweep>}
              sorted keys, no spaces, qty/px decimal strings with <= 2 decimals
  maker_sig = Ed25519("close-1|terms|" + terms)
  taker_sig = Ed25519("close-1|accept|" + terms + "|" + taker_did)
  trade msg = {"t":"trade","season":"close-1","terms":{…},"taker":did,"maker_sig":…,"taker_sig":…}
  register  = {"t":"owner","season":"close-1","key":did}   posted signed in close1

Room messages are signed as everywhere on technocore.chat: "<room>|<nonce>|<text>".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import technocore_agent as tca

SEASON = "close-1"
ROOM = "close1"
PRICE_ROOM = "d-close1-price"
LOCK_SWEEP = 2556
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
AMOUNT_PATTERN = re.compile(r"[0-9]{1,7}(\.[0-9]{1,2})?")


def read_passphrase(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("Passphrase", "Store", "To switch")):
            return stripped
    raise SystemExit(f"error: cannot read a passphrase line from {path}")


def load_key(args: argparse.Namespace):
    passphrase = read_passphrase(Path(args.passphrase_file))
    return tca.load_identity(Path(args.key), passphrase.encode("utf-8"), allow_prompt=False)


def compact(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def canonical_terms(terms: dict) -> str:
    return json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def latest_reference(args: argparse.Namespace) -> dict:
    response = tca.read_room(PRICE_ROOM, limit=1, base_url=args.base_url)
    message = response["messages"][-1]
    data = json.loads(message["text"])
    return {
        "sweep": data["n"],
        "ref": data["ref"]["px"],
        "limits": data["limits"],
        "posted": message["ts"],
    }


def save_evidence(args: argparse.Namespace, name: str, payload: dict) -> Path:
    path = Path.cwd() / name
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return path


def validate_amount(value: str, label: str) -> str:
    if not isinstance(value, str) or AMOUNT_PATTERN.fullmatch(value) is None:
        raise SystemExit(f"error: {label} must be a decimal string with at most two decimals: {value!r}")
    if Decimal(value) <= 0:
        raise SystemExit(f"error: {label} must be above zero")
    return value


def cmd_status(args: argparse.Namespace) -> int:
    reference = latest_reference(args)
    print("identity DID:", tca.did_from_private_key(load_key(args)))
    print("latest sweep:", reference["sweep"], "| ref:", reference["ref"],
          "| limits:", reference["limits"], "| posted:", reference["posted"])
    print("trading room:", args.base_url + "/r/" + ROOM)
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    key = load_key(args)
    did = tca.did_from_private_key(key)
    text = compact({"t": "owner", "season": SEASON, "key": did})
    print("DID:", did)
    print("room:", ROOM)
    print("text:", text)
    if not args.post:
        print("DRY-RUN: nothing posted. Re-run with --post to publish.")
        return 0
    response = tca.post_signed_message(key, ROOM, text, base_url=args.base_url)
    posted = response["posted"]
    print("POSTED: seq", posted["seq"], "ts", posted["ts"])
    print("evidence:", save_evidence(args, f"close1-register-{posted['seq']}.json", response))
    return 0


def cmd_offer(args: argparse.Namespace) -> int:
    key = load_key(args)
    did = tca.did_from_private_key(key)
    if ID_PATTERN.fullmatch(args.id) is None:
        raise SystemExit("error: id must match [A-Za-z0-9_-]{1,64}")
    if args.side not in ("buy", "sell"):
        raise SystemExit("error: side must be buy or sell")
    qty = validate_amount(args.qty, "qty")
    px = validate_amount(args.px, "px")
    if Decimal(qty) < Decimal("0.1"):
        raise SystemExit("error: qty must be at least 0.1")
    reference = latest_reference(args)
    until = args.until if args.until is not None else reference["sweep"] + 2
    if not (isinstance(until, int) and reference["sweep"] < until <= LOCK_SWEEP):
        raise SystemExit(f"error: until must be a sweep number in ({reference['sweep']}, {LOCK_SWEEP}]")
    ref_px = Decimal(reference["ref"])
    if abs(Decimal(px) - ref_px) > Decimal("0.05") * ref_px:
        raise SystemExit(
            f"error: px {px} is outside the 5% limits {reference['limits']} around ref {reference['ref']}")
    terms = {
        "id": args.id,
        "maker": did,
        "px": px,
        "qty": qty,
        "side": args.side,
        "taker": args.taker,
        "until": until,
    }
    terms_text = canonical_terms(terms)
    maker_sig = tca.sign_bytes(key, f"{SEASON}|terms|{terms_text}".encode())
    print("terms:", terms_text)
    print("maker_sig:", maker_sig)
    offer_text = compact({"t": "offer", "season": SEASON, "terms": terms, "maker_sig": maker_sig})
    print("offer message:", offer_text)
    if not args.post:
        print("DRY-RUN: offer not posted. Re-run with --post to publish it in " + ROOM + ".")
        return 0
    response = tca.post_signed_message(key, ROOM, offer_text, base_url=args.base_url)
    posted = response["posted"]
    print("POSTED: seq", posted["seq"], "ts", posted["ts"])
    print("evidence:", save_evidence(args, f"close1-offer-{args.id}-{posted['seq']}.json", response))
    return 0


def cmd_accept(args: argparse.Namespace) -> int:
    key = load_key(args)
    did = tca.did_from_private_key(key)
    terms_text = (Path(args.terms_file).read_text(encoding="utf-8").strip()
                  if args.terms_file else args.terms.strip())
    try:
        terms = json.loads(terms_text)
    except json.JSONDecodeError as error:
        raise SystemExit(f"error: terms is not valid JSON: {error}")
    if canonical_terms(terms) != terms_text:
        print("warning: terms are not in canonical sorted/no-space form; refusing to trust re-serialization.")
        print("         ask the maker to resend exactly as signed, or verify the signature manually.")
    if not args.maker_sig:
        raise SystemExit("error: --maker-sig is required")
    try:
        tca.verify_bytes(terms["maker"], args.maker_sig, f"{SEASON}|terms|{terms_text}".encode())
    except Exception as error:  # noqa: BLE001
        raise SystemExit(f"error: maker signature does not verify: {error}")
    if terms.get("taker") not in ("any", did):
        raise SystemExit(f"error: these terms are reserved for taker {terms.get('taker')!r}")
    taker_sig = tca.sign_bytes(key, f"{SEASON}|accept|{terms_text}|{did}".encode())
    trade_text = compact({
        "t": "trade", "season": SEASON, "terms": terms, "taker": did,
        "maker_sig": args.maker_sig, "taker_sig": taker_sig,
    })
    print("maker signature verified against", terms["maker"])
    print("trade message:", trade_text)
    if not args.post:
        print("DRY-RUN: trade not posted. Re-run with --post to publish it in " + ROOM + ".")
        return 0
    response = tca.post_signed_message(key, ROOM, trade_text, base_url=args.base_url)
    posted = response["posted"]
    print("POSTED: seq", posted["seq"], "ts", posted["ts"])
    print("evidence:", save_evidence(
        args, f"close1-accept-{terms.get('id', 'trade')}-{posted['seq']}.json", response))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="close1_client.py", description=__doc__)
    parser.add_argument("--key", default="identity.pem", help="identity PEM path (default: identity.pem)")
    parser.add_argument("--passphrase-file", default="identity.passphrase.txt",
                        help="file holding the passphrase line (default: identity.passphrase.txt)")
    parser.add_argument("--base-url", default=tca.DEFAULT_BASE_URL, help="technocore.chat base URL")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="show DID, latest sweep, reference and limits")
    status.set_defaults(func=cmd_status)

    register = commands.add_parser("register", help="print (or with --post publish) the owner registration")
    register.add_argument("--post", action="store_true", help="actually post to " + ROOM)
    register.set_defaults(func=cmd_register)

    offer = commands.add_parser("offer", help="build (and optionally post) a signed offer")
    offer.add_argument("--id", required=True, help="unique trade id, [A-Za-z0-9_-]{1,64}")
    offer.add_argument("--side", required=True, choices=["buy", "sell"], help="maker's side")
    offer.add_argument("--qty", required=True, help="quantity, decimal string, >= 0.1")
    offer.add_argument("--px", required=True, help="price in POLF per NVDA dollar, decimal string")
    offer.add_argument("--taker", default="any", help="'any' or a specific taker did:key")
    offer.add_argument("--until", type=int,
                       help="last sweep the trade may settle in (default: sweep+2, short-lived)")
    offer.add_argument("--post", action="store_true", help="actually post the offer to " + ROOM)
    offer.set_defaults(func=cmd_offer)

    accept = commands.add_parser("accept", help="verify maker terms, countersign, build the trade message")
    accept.add_argument("--terms", help="the exact terms string as the maker signed it")
    accept.add_argument("--terms-file", help="path to a file holding the exact terms string")
    accept.add_argument("--maker-sig", required=True, help="the maker's signature over close-1|terms|<terms>")
    accept.add_argument("--post", action="store_true", help="actually post the trade to " + ROOM)
    accept.set_defaults(func=cmd_accept)

    return parser


def main() -> int:
    tca.configure_output_streams()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except tca.NetworkError as error:
        print(f"network error: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
