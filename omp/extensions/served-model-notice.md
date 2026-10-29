<!-- Texts for served-model-notice.ts. Each section starts at a line `<!-- name -->`; {{asked}},
{{served}}, {{from}}, {{floor}} and {{reason}} are filled in. A notice is one head (chain, startup,
anthropic, restored or moved), then one placement (top, below or unplaced; after restored only when
part of the work ran below the floor), then the tail (restored-tail after restored). -->
<!-- chain -->
Model substitution: omp moved this session to a different model. You were asked to run {{asked}}; omp moved off {{from}} after it could not serve a request ({{reason}}), down its configured fallback chain. {{served}} is serving you now. If the model line in your system prompt names a different model, this notice is correct and that line is not.
<!-- startup -->
Model substitution: omp started this session on {{served}} in place of {{asked}}, the model you were asked to run, which could not serve (no usable credential, or its usage window is spent). If the model line in your system prompt names a different model, this notice is correct and that line is not.
<!-- anthropic -->
Model substitution: Anthropic answered a turn of yours with {{served}}, not with {{asked}}, the model you were asked to run. Anthropic makes this change inside one request and can keep sending this conversation to {{served}} for about an hour. If the model line in your system prompt names a different model, this notice is correct and that line is not.
<!-- restored -->
Model restored: {{asked}}, the model you were asked to run, is serving you again. Earlier turns in this session were served by {{served}}.
<!-- moved -->
Model substitution, updated: omp moved this session to {{served}}, a model it already served you from. You were asked to run {{asked}}, so this is still a substitution.
<!-- top -->
{{served}} is at or above {{floor}} in the configured fallback chains.
<!-- below -->
{{served}} is below {{floor}} in the configured fallback chains, where hard work is more likely to fail for capability. Start your result with this line: `MODEL BELOW FLOOR: asked {{asked}}, served {{served}}{{part}}`.
<!-- unplaced -->
No configured fallback chain places {{served}} against {{floor}}, so treat it as below that line. Start your result with this line: `MODEL BELOW FLOOR: asked {{asked}}, served {{served}} (not placed){{part}}`.
<!-- tail -->
This is a substitution, not a model failure. A rule to stop and report on a model failure is about a hard failure: a refusal that ends your work, or an error. This notice is not that failure: continue the work. In your result, state both models: asked {{asked}}, served {{served}}. This notice replaces any earlier model notice in this session.
<!-- restored-tail -->
A substitution is not a model failure: a rule to stop and report on a model failure is about a hard failure, a refusal that ends your work or an error. Continue the work. In your result, state both models: asked {{asked}}, served {{served}} for part of the work. This notice replaces any earlier model notice in this session.
