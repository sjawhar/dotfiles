# Response: Check Today's Meeting Decisions

## What I Would Say to the User

"Sure, let me check your Ghost Whisper transcripts and Google Drive for any important decisions from today's meetings. Give me a moment."

## Actions I Would Take

### Step 1: Load Skills

I would invoke two skills before doing anything:

1. **`ghost-wispr`** — to get instructions on how to search meeting transcripts
2. **`google-workspace`** — to get instructions on how to search Google Drive docs

### Step 2: Search Ghost Whisper Transcripts

Based on the ghost-wispr skill instructions, I would search for today's meeting transcripts. This would likely involve:

- Searching recordings/transcripts filtered to today's date
- Looking for keywords like "decided", "agreed", "action item", "next steps", "approved", "go with", "let's do"
- Extracting key decisions and participants from each meeting

### Step 3: Search Google Drive

Based on the google-workspace skill instructions, I would search Google Drive for meeting-related documents from today:

- Search for documents modified today with terms like "meeting notes", "minutes", "decisions", "agenda"
- Read the contents of any matching documents
- Extract decisions, action items, and key outcomes

### Step 4: Synthesize and Respond

I would combine findings from both sources and respond with something like:

"Okay, here's what I found from your meetings today. You had [N] meetings. Here are the key decisions:

1. **[Meeting name/time]** — [Decision summary]
2. **[Meeting name/time]** — [Decision summary]

And a few action items that came out of those:
- [Action item 1]
- [Action item 2]

Want me to go deeper into any of these?"

## What I Would NOT Do

- I would **not** dump raw transcript text into the conversation — voice mode would read it all aloud, making it unbearable
- I would **not** list every trivial detail — I'd focus on decisions and action items since that's what was asked
- I would keep responses **concise and structured** for audio consumption — short sentences, clear numbering, natural speech patterns
