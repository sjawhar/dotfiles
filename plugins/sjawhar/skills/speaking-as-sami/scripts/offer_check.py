#!/usr/bin/env python3
"""Mechanical checks for a customer offer, spec sheet or catalog.

Usage: offer_check.py <file.md>...
Exit 1 when any file has a finding. Standard library only.

Checks the rules in speaking-as-sami/references/offer-documents.md that a program can decide:
banned verbs, long sentences, semicolons, dashes, phrasal verbs, hedges, internal-status words.
Code blocks, inline code, link targets and the Dispatch ask block are skipped.
"""

import argparse
import re
import sys
from pathlib import Path

MAX_SENTENCE_WORDS = 25

BANNED_VERBS = {
    "carry": "what the document states, includes or costs",
    "carries": "what the document states, includes or costs",
    "carried": "what the document states, includes or costs",
    "carrying": "what the document states, includes or costs",
    "name": "states, lists, is called",
    "names": "states, lists, is called",
    "named": "states, lists, is called",
    "naming": "states, lists, is called",
    "pay": "the price, or what the buyer receives",
    "pays": "the price, or what the buyer receives",
    "paid": "the price, or what the buyer receives",
    "paying": "the price, or what the buyer receives",
    "show": "proves, measures, reports, or the number itself",
    "shows": "proves, measures, reports, or the number itself",
    "showed": "proves, measures, reports, or the number itself",
    "showing": "proves, measures, reports, or the number itself",
    "land": "is delivered, is merged, is released",
    "lands": "is delivered, is merged, is released",
    "landed": "is delivered, is merged, is released",
    "landing": "is delivered, is merged, is released",
    "arrive": "is delivered, the recipient receives it",
    "arrives": "is delivered, the recipient receives it",
    "arrived": "is delivered, the recipient receives it",
    "arriving": "is delivered, the recipient receives it",
    "ride": "the dependency itself",
    "rides": "the dependency itself",
    "riding": "the dependency itself",
}

# "the name of the file" and "under new names" are nouns, not the banned verb.
NOUN_BEFORE = re.compile(
    r"\b(the|its|their|our|your|a|an|his|her|that|this|each|no|both|these|those"
    r"|new|same|other|different|exact|full|short|long|correct)\s+$",
    re.I,
)
# A preposition one or two words back makes a noun phrase. "to" is left out: it also marks an infinitive.
PREP_NEAR = re.compile(r"\b(by|under|with|for|in|on|of|from|as|at|into|over|without|through)\s+(?:[a-z]+\s+)?$", re.I)
NOUN_AFTER = re.compile(r"^\s+(of|for|in|on)\b", re.I)
NOUN_ONLY = {"name", "names", "pay", "show", "shows", "ride", "rides"}

PHRASAL_VERBS = [
    "set up", "sets up", "setting up", "ramp up", "ramping up", "roll out", "rolling out",
    "rolls out", "spin up", "spinning up", "carve out", "carves out", "dial in", "bake in",
    "baked in", "lock in", "locked in", "pull together", "reach out", "reaching out",
    "kick off", "kicks off", "drill down", "double down", "circle back", "build out",
    "builds out", "stand up", "stands up", "tease out", "wrap up", "sign off", "ship out",
]

HEDGES = [
    "aim to", "aims to", "hope to", "hopes to", "we believe", "we think", "strive",
    "seek to", "seeks to", "working towards", "working toward", "intend to", "intends to",
    "look to", "plan to deliver", "should be able to", "we expect to", "fairly", "quite",
    "somewhat", "relatively", "arguably", "largely",
]

STATUS_WORDS = [
    "paused", "stopped", "deprioritised", "deprioritized", "on hold", "backlog",
    "internally", "our pipeline", "work in flight", "for now", "at the moment",
    "so far", "currently", "to date", "as of today", "under way", "we are rebuilding",
    "lane ", "sprint", "roadmap item", "wip",
]

FENCE = re.compile(r"^\s*(```|~~~)")
ASK_OPEN = re.compile(r"^\s*:::ask")
ASK_CLOSE = re.compile(r"^\s*:::\s*$")
INLINE_CODE = re.compile(r"`[^`]*`")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
URL = re.compile(r"\b(?:https?://|dispatch://|skill://)\S+")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def strip_noise(line: str) -> str:
    line = INLINE_CODE.sub(" ", line)
    line = LINK_TARGET.sub("] ", line)
    return URL.sub(" ", line)


def prose_lines(text: str):
    """Yield (line_number, prose) for lines a reader reads as sentences."""
    in_fence = False
    in_ask = False
    for number, raw in enumerate(text.splitlines(), start=1):
        if FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if ASK_OPEN.match(raw):
            in_ask = True
            continue
        if in_ask:
            if ASK_CLOSE.match(raw):
                in_ask = False
            continue
        if raw.startswith("    ") or raw.startswith("\t"):
            continue
        yield number, strip_noise(raw)


def banned_verb_hits(line: str):
    for match in re.finditer(r"\b[A-Za-z]+\b", line):
        word = match.group(0).lower()
        if word not in BANNED_VERBS:
            continue
        if word in NOUN_ONLY:
            before = line[: match.start()]
            after = line[match.end() :]
            if NOUN_BEFORE.search(before) or PREP_NEAR.search(before) or NOUN_AFTER.match(after):
                continue
        yield word, BANNED_VERBS[word]


def check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    findings: list[str] = []

    def report(number: int, kind: str, detail: str) -> None:
        findings.append(f"{path}:{number}: {kind}: {detail}")

    for number, line in prose_lines(text):
        lower = line.lower()
        for word, replacement in banned_verb_hits(line):
            report(number, "banned verb", f'"{word}" -> write {replacement}')
        if ";" in line:
            report(number, "semicolon", "split the sentence")
        for dash in ("\u2014", "\u2013"):
            if dash in line:
                report(number, "dash", "restructure the sentence")
        for phrase in PHRASAL_VERBS:
            if re.search(rf"\b{re.escape(phrase)}\b", lower):
                report(number, "phrasal verb", f'"{phrase}" -> use one plain verb')
        for phrase in HEDGES:
            if re.search(rf"\b{re.escape(phrase)}\b", lower):
                report(number, "hedge", f'"{phrase}" -> state the fact or delete')
        for phrase in STATUS_WORDS:
            if re.search(rf"{re.escape(phrase)}", lower):
                report(number, "internal status", f'"{phrase.strip()}" -> the buyer does not act on this')
        body = re.sub(r"^[#>\-*\d.\s|]+", "", line).strip()
        if not body:
            continue
        for sentence in SENTENCE_SPLIT.split(body):
            words = sentence.split()
            if len(words) > MAX_SENTENCE_WORDS:
                report(number, "long sentence", f"{len(words)} words, over {MAX_SENTENCE_WORDS}")

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    total = 0
    for path in args.paths:
        findings = check_file(path)
        total += len(findings)
        for finding in findings:
            print(finding)
        if not findings:
            print(f"{path}: clean")
    if total:
        print(f"\n{total} finding(s)", file=sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
