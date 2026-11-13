# Linking your ORCID iD to the publishing address

`omicchain_identity.py` produces a file, `orcid_link.json`, that ties your ORCID iD to the
Ethereum address that publishes your fingerprints, in a way a third party can check.

## What it proves

Two independent signatures are combined:

1. **ORCID's**: an OpenID Connect `id_token`, signed by ORCID, showing that the holder of
   the ORCID account logged in. The login request carries a `nonce` derived from your
   Ethereum address, so the login is bound to *this* address and cannot be reused for another.
2. **Your wallet's**: a signature over a message naming the ORCID iD, the address and the
   hash of that token, showing that the holder of the address agrees.

## What it does not prove

- The public key stored in the file is **not** a proof by itself: anyone can forge a token and
  embed their own key. The only anchor is the key list **published by ORCID**; `verify`
  compares the two. Offline, the result is *"consistent but key not confirmed"*, never *verified*.
- If ORCID rotates its keys, an old link can no longer be confirmed against the live list.
- It says nothing about the analysis. An ORCID iD identifies a person, not the quality of the work.
- **Only approve ORCID logins you started yourself.** ORCID's consent screen does not display
  the Ethereum address that is being linked.
- The `id_token` is stored in the file and may contain your name if your ORCID record makes it
  public. Keep that in mind before sharing the file.

## Set up (once)

1. Register an ORCID API client for your own use (ORCID's *Developer tools*; start with the
   **sandbox**, which has its own accounts). Register this redirect URI, exactly:
   `http://127.0.0.1:8765/callback`
   ORCID refuses `localhost`, hence the IP address. `ORCID_REDIRECT_HOST` in `.env` can switch it
   to `localhost` (the only other accepted value: the callback is received on this computer only);
   the URI sent must match the registered one character for character.
   **In production**, ORCID only accepts **HTTPS** redirect URIs, so the local address cannot be
   registered. Instead, publish `portal/orcid-callback.html` on an HTTPS site you control (for
   example GitHub Pages, next to the verification portal), register **its exact address** as
   the redirect URI, and put the same address in `.env` as `ORCID_REDIRECT_URI`. That page only
   forwards ORCID's answer to `http://127.0.0.1:8765/callback` on your computer; the login itself
   is unchanged. The authorization code it passes on is single-use and useless without your
   client secret, which stays on your computer.
2. In `.env` set `ORCID_CLIENT_ID`, `ORCID_CLIENT_SECRET` and, for the first try, `ORCID_ENV=sandbox`.
   `PRIVATE_KEY` and `ACCOUNT_ADDRESS` must already be there (the same wallet that publishes).
3. `pip install -r requirements.txt` (adds `pyjwt[crypto]`).

## Use

```
python omicchain_identity.py link                    # opens ORCID, writes orcid_link.json
python omicchain_identity.py verify orcid_link.json  # fetches ORCID's key list and checks
```

Use `--orcid-env sandbox` with `verify` for a link made on the sandbox. A sandbox identity proves
nothing about a real ORCID account.

## In the application

The **Certificate** tab has a *Publisher identity (ORCID)* section: **Link my ORCID iD** runs the
same login as the command line and writes `orcid_link.json` next to `OmicChain.py`;
**Remove the ORCID link** deletes it.

When a certificate is generated, the link is checked again (fresh, against ORCID's key list):

| Link state | Certificate | RO-Crate |
|---|---|---|
| verified | ORCID iD + "confirmed with ORCID's published keys on <date>" | `orcid_link.json` + ORCID iD declared as author |
| ORCID unreachable | ORCID iD + "NOT confirmed" | `orcid_link.json` only, no author claimed |
| sandbox, invalid, other address | nothing | nothing (any earlier identity entry is removed) |
| no link | nothing | nothing |
