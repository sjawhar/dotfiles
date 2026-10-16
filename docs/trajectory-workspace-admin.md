# Trajectory Labs Workspace administration

The `admin` tier of the `trajectory` account in `gws/accounts.json` releases `GWS_TRAJECTORY_ADMIN_OAUTH`, a human-tier secretsd key in the `private` source, with one YubiKey touch per session. `admin-sami@`, Sami's admin identity, consented it with 8 scopes, among them `admin.directory.userschema.readonly`. A credential minted by `sami@` has no admin authority.

gws cannot call the Admin SDK Directory API. Use the google-workspace skill's recipe for APIs gws lacks, with `--arg a trajectory --arg t admin` and `GWS_TRAJECTORY_ADMIN_OAUTH`.

## Calls that have worked

| Call | Result |
|---|---|
| `GET /groups?customer=my_customer&maxResults=200` | finds a group by email |
| `GET /groups/<groupKey>/members` | the members |
| `GET /users/<email>?fields=primaryEmail,isEnrolledIn2Sv,isEnforcedIn2Sv,suspended,lastLoginTime` | one user |
| `GET /users?customer=my_customer&query=isEnrolledIn2Sv%3Dfalse&maxResults=200` | the org-wide 2SV sweep |
| `DELETE /groups/<groupKey>/members/<memberEmail>` | 204, empty body |
| `POST /groups/<groupKey>/members` with `{"email": "<user>", "role": "MEMBER"}` | 200, `status: ACTIVE` |
| `PUT /users/<key>` with `{"password": "<value>", "changePasswordAtNextLogin": false}` | 200. Generate the value in the script and never print it |
| `POST /users/<key>/signOut` | 204, empty body; ends every session |

Creating and suspending users (`POST /users`, and `PUT /users/<key>` with `{"suspended": true}`) has not been run yet. The first session to run them adds the exact calls here.

## Signing in to a machine mailbox in a 2SV-enforced OU

A user in an OU that enforces 2-Step Verification who has not enrolled cannot sign in (`Your sign-in settings don't meet your organization's 2-Step Verification policy`). The sanctioned grace path is temporary membership in the hand-staffed `2sv-exempt@` group:

1. Add the user to `2sv-exempt@`. `isEnforcedIn2Sv` turns false after about 20 minutes of propagation (21 minutes when measured).
2. Sign in to the mailbox during that window.
3. Remove the membership. Enforcement returns by propagation too: `isEnforcedIn2Sv` still read false 30 seconds after the 204, and true within about 4 minutes. The 204 is the truth about membership; re-read `isEnforcedIn2Sv` after a pause before concluding enforcement is back.
4. Rotate the password to a value nobody records, then `signOut`.

Neither the password reset nor `signOut` revokes an OAuth grant whose scopes include no Gmail scope. Taiga's sign-in grant (`openid`, `userinfo.email`, `userinfo.profile`) survived both, checked by a token refresh and an authenticated call before and after.
