# "Reauthentication failed"

The box identity and the applier never expire, so this error means the call ran as a user login.

1. **Run it again** with no configuration or account selected.
   - For an apply, use its CI route.
2. **For a build or measurement machine, use `az vm create`** (resource group `sami`, location `westus2`): az runs as the machine identity.
3. **Do not run `gcloud auth login`, `gcloud auth application-default login` or `revoke`.**
   - Do not add `--update-adc`.
   - If an apply under Sami's login (route 4 of `SKILL.md`, "A permission error") finds it expired, post the step on the open re-login ask (`dispatch search --query 'gcloud re-login'`), or open one.
