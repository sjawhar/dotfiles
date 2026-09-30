### Gate request
The agent is about to run `{{tool}}` with these arguments:
```json
{{args}}
```
{{priorReasons}}
Answer on the last line with exactly one JSON object and nothing after it: {"decision":"allow"} or {"decision":"revise","reason":"<failure number and gate, the quoted words, the corrected text>"}.
