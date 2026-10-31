# Sizing a restatement programme

Read this before you promise a date for many units. One item restated once needs none of it.

## Restate whole items, not flagged lines

A flagged line sits inside a definition or a proof that must be written again as a whole. Measured over one
corpus: 87,308 flagged lines in 1,523 files sat inside items holding 622,837 lines, 63% of the text. So
item-level restatement is right, and it is not a cheap patch.

Per file, in that corpus: 233 files were 90% or more touched, 948 were 60% to 90%, 208 were 30% to 60%, and
134 were under 30%. The largest single item was a 3,232-line theorem.

## State a shared text once

Where several units hold the same text once a name is substituted, one writer states it once and each other
unit takes that text, with its own review, its own build and its own check. 61 of 277 files in one programme
were such members, in 11 families. Prove membership before you use it: byte-equal after the named substitution,
nothing less. Near-identical is not identical, and two files that differ by a few constants are not one file.

## Plan around the build, not the writing

Measured in one programme:

| Step | Cost |
| --- | --- |
| Extraction | 4 s per file; 11 to 164 s per unit |
| Split check | about 2 s per unit |
| Quarantine computation | 2 to 3 min per unit |
| Interface printing | 0.4 to 6.4 min per file, median 1.8 |
| Writer | 4 min 28 s to 47 min per file; the spread is per item, not per file |
| Reviewer | about 17 min per file |
| Baseline unit build | 1,396 to 8,541 compiled files, about 42 files a minute on a shared machine |

The build is the bottleneck, so stage build machines and a shared content-keyed cache before you stage writers,
and give one unit one machine while free machines remain.

## Expect rework, and budget it

In the pathfinder unit of one programme, 7 of the first 11 writer runs were thrown away, every one found from
a transcript or a writer's own disclosure and none from a failing build. All seven came from how the work was
set up, not from the writers. A programme that starts with the rules those seven paid for should see a much
lower rate; if it does not, the method is wrong rather than the set-up.

The first review sent back 18 of 42 items of a writer whose audit was clean, on style alone.

## Order the work by the strength of the evidence

Where a measurement flagged the files, read how each was flagged. In one programme 277 of 1,523 flagged files
carried a literal run of ten or more lines in a row from one outside file, and the other 1,246 were flagged by
a weaker rule, a shared name with a median of one line in eight. Doing the strongest first means the part that
matters is finished even if the tail is never done.

## Schedule the waves

Write a unit in dependency order. A wave starts when its upstream file COMPILES, not when its reviewer passes:
a send-back changes a proof, and a statement is byte-equal by construction, so downstream work survives it.
The exception is a send-back on a definition, notation, record or inductive, which changes a body and so every
downstream digest; review a wave's definition-like items before its lemmas. One unit held 168 definitions
among 515 items, and its chain was 13 waves deep with the last six single files one after another, which set
the end date for the whole programme.
