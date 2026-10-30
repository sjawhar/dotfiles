# Offer documents, spec sheets and catalogs

For a document a buyer reads to decide: an offer, a product spec sheet, a catalog of what we sell, a price
page, a pilot proposal. `speaking-as-sami` governs every sentence. This file governs what goes in, in what
order, and which passes run before a buyer sees it.

Plan all of section 7 before you draft. A document that reaches the red team late gets edited instead of
rebuilt, and the wrong structure survives.

## 1. Build a source pack first

Draft from a source pack you assembled, never from what you remember of the product. A draft from memory
describes how we work, which is the one thing the buyer did not ask about.

Read and quote into one file:

- What the buyer asked for, in their words: their meeting notes, their Slack messages, their emails.
- The signed contracts and work orders that already bind the offer: what they bought, what the exclusivity
  and IP terms already say.
- The internal meeting transcripts where the offer's shape and price were decided.
- The Dispatch issues and answered asks behind each item in the catalog.
- One interview per item with the agent or person who owns its facts.

Keep the file. Section 6's claim check and every later pass read it.

## 2. Write the thesis before the structure

The thesis is what changes for the buyer if they say yes. Not what we built, and not how we work.

Write it in one sentence and test it: a competitor must not be able to write the same sentence. "We sell the
verified RL stack: tasks whose grader and environment have machine-checked proofs" passes. "We build
high-quality RL environments" fails, because anybody can write it.

## 3. One page, then appendices

Page 1, in this order:

1. The thesis, in one bold sentence.
2. The proof points: what is true today, each one the buyer's own people can check without us.
3. The offers, one line each, each pointing to its appendix.
4. The limits: where the offer does not apply, in our words. A buyer who finds a limit we hid stops trusting
   the claims we did state.
5. What we need from them to start.

Everything else is an appendix: the catalog, the fairness contract that applies to every item, each service,
the scope and exclusivity terms.

Page 1 is short because the reader decides from it. An appendix is as long as its facts require. Where the
buyer already read a colleague's document on the same offer, match that document's depth. A shorter document
reads as a smaller offer.

## 4. The same template for every catalog item

```
## A<n>. <What the buyer gets, as an imperative>

*<One line: what the model must do, in the buyer's terms.>*

**Context.** What the material is, where it comes from, and what a task removes or asks for.

**The skills learnt.** Three to five bullets. Each is a bold capability, then one or two sentences on the
choice it demands and the tension that makes the choice hard.

**Fair by construction.** Why nothing without the skill passes, and why a correct answer unlike ours still
earns full credit.

**The reward teaches the skill.** What earns credit, what earns none, and the control that separates this
skill from sheer volume.

**Scale.** The smallest and largest version of the item, and what sets the size.
```

A skill bullet states a capability and its difficulty: "Choose the contract at a boundary. A specification
must be strong enough for every caller and weak enough for the callee to prove." A failure symptom is not a
skill: "runs out of context", "copies the wrong lemma" and "struggles with large repositories" describe what
goes wrong, so they tell the buyer nothing about what their model would learn.

Use the identical headings in every item. A buyer compares items across one row at a time.

## 5. What never appears

- **Our internal status.** Which parts are live, paused, stopped or rebuilt, what we tried first, what we
  have not built. The framing is: do you want this? Then we will give it to you. A planned part is stated as
  a deliverable with its range ("three to six months"), never as our current state.
- **Internal process numbers.** Task counts, theorem counts, test counts, scores, image or version names,
  dates against a figure. Each one starts a conversation about our pipeline instead of the offer.
- **Hedges about our capacity.** "We aim to", "we hope to", "we are working towards". Every statement is
  either proved today or named as in progress with its range. Nothing sits between.
- **Any claim section 6 did not verify.**

## 6. The fact owner checks each claim

Before the first editing pass, send each section to the agent or person who owns its facts. Ask for one
verdict per claim: verified with its source, false, or unverified. Delete or fix everything not verified.

Run this before the editing passes, never after. An editing pass makes a false claim read better, which
makes it harder to catch.

## 7. The passes, in order

Each pass takes the previous pass's output.

1. **STE pass.** `skill://asd-ste100`, applied for flavour, not as a strict rewrite: short sentences, one
   idea each, no phrasal verbs, one meaning per word. A strict rewrite loses facts.
2. **Slop pass.** `skill://ce-noslop` in edit mode over the whole document.
3. **Red team per section.** One fresh `astra-api` agent per section, given that section, the source pack
   and who the reader is. Ask what the buyer's own technical reviewer would attack. Fix each finding or
   write down why not.
4. **Red team of the whole flow.** One fresh `astra-api` agent reads the assembled document: order,
   repetition, whether page 1 leads to the right appendix, whether the thesis survives to the last page.
5. **Final line edit.** You read the document from the top, as the buyer, and cut.

Each red team gets a fresh agent. An agent that helped write the draft defends it.

## 8. Mechanical checks

```bash
python3 ~/.dotfiles/plugins/sjawhar/skills/speaking-as-sami/scripts/offer_check.py <file>
```

It reports banned verbs, sentences over 25 words, semicolons, en and em dashes, phrasal verbs, hedges and
internal-status words. Run it after pass 5 and fix every hit before delivery. A program catches these where
a reading pass does not.

Write the concrete action in place of a banned verb, because each one is a metaphor that replaces the fact.
"The page carries the price shapes" tells the reader nothing about the price.

| Never | Write instead |
|---|---|
| carry, carries, carrying | what the document states, includes or costs |
| name, names, naming (as a verb) | states, lists, is called |
| pay, pays, paying for | the price, or what the buyer receives |
| show, shows, showing | proves, measures, reports, or the number itself |
| land, lands, landing | is delivered, is merged, is released |
| arrive, arrives, arriving | is delivered, the recipient receives it |
| ride, rides, riding | the dependency itself |

## 9. Delivery

- One copy, as a Dispatch document on the issue (`dispatch doc-edit`). Never a Google Doc, never a second
  copy elsewhere: two copies diverge and the buyer reads the stale one.
- End the Dispatch copy with an ask block that gives Sami the next step as options, each with its cost, and
  your recommendation with its reason.
- The buyer's export excludes the ask block and every internal link.
