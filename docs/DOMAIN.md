# Getting a free short domain

Your site works today at `currentkothai.imteenan13.workers.dev`. This is how to
get `currentkothai.is-a.dev` instead, for free, permanently.

`is-a.dev` gives free subdomains to developers. It is run by volunteers, the
whole registry is a public GitHub repo, and you claim a name by adding one small
JSON file to it. No payment, no account beyond GitHub.

---

## Step 1: fork the registry

Go to <https://github.com/is-a-dev/register> and click **Fork** (top right).
You now have your own copy at `github.com/imteenan/register`.

## Step 2: add your file

In **your fork**, navigate into the `domains/` folder, then click
**Add file → Create new file**.

Name the file exactly:

```
currentkothai.json
```

The filename becomes the subdomain, so this claims `currentkothai.is-a.dev`.

Paste this as the contents:

```json
{
  "owner": {
    "username": "imteenan",
    "email": "aiimteebithi@gmail.com"
  },
  "records": {
    "CNAME": "currentkothai.imteenan13.workers.dev"
  }
}
```

Two things to get right:

- **No trailing dot** on the CNAME value.
- The CNAME points at your **Workers URL**, not at a Cloudflare IP.

Scroll down, write a commit message like `Add currentkothai.is-a.dev`, and click
**Commit new file**.

## Step 3: open the pull request

Go to your fork's main page. GitHub shows a banner: **Contribute → Open pull
request**. Title it `Add currentkothai.is-a.dev` and submit.

An automated check runs first and will tell you immediately if the JSON is
malformed. Then a human reviews. **Typical wait is a few days.** Do not open a
second PR while the first is pending; that slows it down.

## Step 4: tell Cloudflare about it

Once the PR is **merged**, DNS starts resolving within about an hour. Then:

1. Cloudflare dashboard → **Workers & Pages** → your `currentkothai` project
2. **Settings** → **Domains** → **Add** → **Custom domain**
3. Enter `currentkothai.is-a.dev` → **Add domain**

Cloudflare issues the TLS certificate automatically. Both URLs keep working; the
`workers.dev` one stays as a fallback.

## Step 5: point the site at the new name

Once it resolves, update the canonical URL so shared links and search engines
use the short name. Ask me and I will change the `og:url` and canonical tags
across the three pages in one commit.

---

## Verifying it worked

```bash
curl -sI https://currentkothai.is-a.dev | head -3
```

`HTTP/2 200` means you are done.

If you get a certificate error in the first hour, that is normal while
Cloudflare issues the cert. If it persists past a few hours, check that the
CNAME in your merged JSON exactly matches your Workers hostname.

---

## The alternatives, briefly

| Option | Example | Wait | Catch |
|---|---|---|---|
| **is-a.dev** | `currentkothai.is-a.dev` | Days | Must be a developer/personal project. Fine here |
| **js.org** | `currentkothai.js.org` | Days to weeks | They expect a JavaScript project. This qualifies |
| **eu.org** | `currentkothai.eu.org` | Weeks | Slowest, but a real second-level domain |
| **Buy one** | `currentkothai.xyz` | Instant | Roughly $1-3 for the first year, then renewal |

## One rule

Do not register anything containing `desco`, `dpdc`, `nesco`, `gov`, or
`bd-power`. This project's entire legal and ethical position is that it never
pretends to be official, and a domain that implies otherwise throws that away
and invites a takedown.
