// span-account e2e harness (run.sh): store one fake Anthropic OAuth login (no real token) in an isolated
// agent.db. Run from an oh-my-pi checkout: bun seed.ts <agent.db>
import { AuthStorage, SqliteAuthCredentialStore } from "@oh-my-pi/pi-ai/auth-storage";

const store = await SqliteAuthCredentialStore.open(process.argv[2]!);
const storage = new AuthStorage(store);
await storage.credentials.set("anthropic", [
	{
		type: "oauth",
		access: "sk-ant-oat01-e2e-fake",
		refresh: "e2e-fake-refresh",
		expires: Date.now() + 24 * 60 * 60_000,
		accountId: "acct-e2e-a",
		email: "a@example.invalid",
	},
]);
storage.close();
console.log("seeded");
