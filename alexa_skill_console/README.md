# Family Kitchen — Alexa Skill setup

This turns your existing `/kitchen` recipe app into a **voice + Echo Show** experience.
Your Flask app **is** the skill's backend — there is **no AWS Lambda** to manage, and
recipe editing stays exactly where it is now (`/kitchen/manage`).

- **Endpoint (what Alexa calls):** `https://www.jackcapstaff.com/kitchen/alexa`
- **Data source:** your live recipe database (published recipes only)
- **Invocation name:** *"family kitchen"* → say **"Alexa, open family kitchen"**

---

## 1. Create the skill

1. Go to the **Alexa Developer Console** → <https://developer.amazon.com/alexa/console/ask>.
2. **Create Skill**:
   - Name: `Family Kitchen`
   - Primary locale: **English (UK)** (the responses are written in en-GB).
   - Model: **Custom**
   - Hosting: **Provision your own** (this is the key choice — we use our own HTTPS endpoint, not Alexa-hosted/Lambda).
   - Template: **Start from Scratch**.

## 2. Interaction model

1. In the left nav: **Build → JSON Editor**.
2. Drag in / paste the contents of [`interaction-model.json`](./interaction-model.json).
3. Click **Save Model**, then **Build Model** (wait for it to finish).

## 3. Endpoint

1. Left nav: **Build → Endpoint**.
2. Select **HTTPS**.
3. Default Region → enter: `https://www.jackcapstaff.com/kitchen/alexa`
4. SSL certificate type → choose **"My development endpoint is a sub-domain of a domain that has a wildcard certificate from a certificate authority"** (Heroku provides a valid CA cert for `*.jackcapstaff.com`).
5. **Save Endpoints**.

## 4. Lock the endpoint to your skill (recommended)

So only *your* skill can call the endpoint, set the skill ID on Heroku:

1. In the console, top of the page, copy your **Skill ID** (looks like `amzn1.ask.skill.xxxxxxxx-...`).
2. On your machine:
   ```powershell
   heroku config:set ALEXA_SKILL_ID="amzn1.ask.skill.xxxxxxxx-...." -a <your-heroku-app>
   ```

The endpoint **always** verifies Amazon's request signature (using the `cryptography`
library). The skill-ID check is an extra layer. You normally never need to touch
`ALEXA_VERIFY_SIGNATURE` — leave it on. (It exists only for local testing, see below.)

## 5. (Optional) Native Alexa timers

If you want **"start the timer"** to create a real Alexa timer (rings/announces on the
device), enable the permission:

1. Console left nav: **Build → Permissions** (or **Tools → Permissions**).
2. Toggle on **Timers**.
3. On the device/Alexa app the first time, grant the timers permission when prompted.

Without this permission the skill will instead tell you the duration and suggest
"Alexa, set a timer for N minutes". Everything else works regardless.

## 6. Test it

- **Console simulator:** left nav **Test** → set to **Development** → type or say
  *"open family kitchen"*.
- **On your Echo Show** (signed into the same Amazon account as the dev account):
  *"Alexa, open family kitchen"*.

### Things to say

| You say | What happens |
| --- | --- |
| "Alexa, open family kitchen" | Welcome + how many recipes |
| "what's on the menu" | Reads the list (with day numbers) |
| "open day one" / "cook the chicken tikka curry" | Opens that recipe, shows equipment |
| "what equipment do I need" / "ingredients" / "prep" | Reads those, shows them on screen |
| "start cooking" | Step 1 (voice + on-screen), shows the next step too |
| "next" / "previous" / "repeat" | Move through steps |
| "start the timer" | Sets a timer for the current step (if it has one) |
| "where am I" | "You're on step 3 of 6…" |
| "stop" | Exits |

---

## Adding / editing recipes

Nothing changes — manage recipes at **`/kitchen/manage`** as before. The skill reads
them live, so new recipes are instantly available by **day number** and by the built-in
voice navigation.

> **Note on recipe *names*:** matching a recipe by spoken *name* uses the `RecipeName`
> list in `interaction-model.json`, which is seeded with the built-in 14 recipes. If you
> add brand-new recipes and want to open them by name (rather than "open day N"), add
> their titles/synonyms to that list and re-**Build Model**. Day-number and the rest of
> the flow need no changes.

---

## Local testing (optional, for developers)

The signature check requires real Amazon headers, so disable it locally:

```powershell
$env:ALEXA_VERIFY_SIGNATURE = "0"
python -c "import app"   # then POST sample-requests/*.json to /kitchen/alexa
```

Sample request envelopes are in [`sample-requests/`](./sample-requests). Example with curl:

```powershell
curl.exe -s -X POST http://localhost:5000/kitchen/alexa `
  -H "Content-Type: application/json" `
  --data "@alexa_skill_console/sample-requests/launch.json"
```

Never set `ALEXA_VERIFY_SIGNATURE=0` in production.
