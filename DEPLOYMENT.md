# Running this in production

Sized for the target we picked: **~50–150 CA firms, 500 of their staff signed
in at once at peak** (GST filing week is the peak — the 11th to the 20th of
each month is when everyone is working at the same time), documents read by a
model **on your own hardware**, everything hosted in India.

Everything below is one machine plus a managed database. That is not a
compromise for this size — 500 concurrent dashboard users is a few hundred
requests a second at worst, and most of them are reads. Distributed
architecture at this scale buys you complexity and a bigger bill.

---

## 1. What you are provisioning

| | Spec | Why this and not less |
|---|---|---|
| **App server** | 8 vCPU, 16 GB RAM, 200 GB NVMe | Runs API, workers, Redis, Caddy and the dashboard. CPU is the binding constraint, not RAM. |
| **GPU** | 1× RTX 4090 / L4, 24 GB VRAM | Reads documents locally. A 7B vision model needs ~16 GB to be quick. See §4 — you may not need this on day one. |
| **Postgres** | Managed, 4 vCPU / 16 GB, 100 GB | Managed because you do not want to be the person restoring a database at midnight. |
| **Object storage** | 500 GB, ap-south-1 | Only once you run more than one app server. Skip initially. |
| **Domains** | `api.example.com`, `app.example.com` | Both must resolve to the app server *before* first start or the TLS challenge fails. |

Self-hosting the GPU is what makes "your clients' invoices never leave our
infrastructure" a true sentence. Keep it true — it is the strongest thing
you have against Zoho and the rest.

---

## 2. The arithmetic that actually bites

Every API worker and every job worker holds its own database connection
pool. The ceiling Postgres must survive is:

```
(API_WORKERS + WORKER_REPLICAS) × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
```

The shipped defaults: `(4 + 2) × (10 + 10) = 120`, against
`POSTGRES_MAX_CONNECTIONS=200`. Comfortable.

**If you scale workers, scale `max_connections` with them.** This is the
single most common way a deployment that tested fine falls over on filing
day — you add workers because it is slow, and the extra workers exhaust the
connection limit, so requests start failing with `too many clients` under
exactly the load you sized for.

| Concurrent staff | `API_WORKERS` | `WORKER_REPLICAS` | Connections | `POSTGRES_MAX_CONNECTIONS` |
|---|---|---|---|---|
| ~100 | 2 | 1 | 60 | 100 |
| **~500 (target)** | **4** | **2** | **120** | **200** |
| ~1,500 | 8 | 4 | 240 | 300 |
| ~4,000 | 16 (two machines × 8) | 6 | 440 | 500 + PgBouncer |

Past roughly 300 connections, put **PgBouncer** in transaction mode between
the app and Postgres instead of raising the limit again. Postgres spends real
memory per connection and gets slower, not faster, as you add them.

### Two other limits that are not the database

**Redis is mandatory.** The in-memory rate limiter counts inside one process,
so four API workers without Redis would grant every firm four times its
limit — which is not a rate limit, it is a number in a log line. The API now
**refuses to boot** under `APP_ENV=production` without `REDIS_URL`. If you
genuinely run a single worker, `ALLOW_IN_MEMORY_RATE_LIMIT=true` is the
documented escape hatch.

**Local disk does not survive a second machine.** With
`STORAGE_BACKEND=local`, a document written on server A is not on server B,
so downloads 404 depending on which one answers. Move to `s3` *before* you
add the second app server, not after.

---

## 3. Deploying

On a GPU box, install the **NVIDIA container toolkit** first — without it
the `ollama` service fails to start and the error names the missing runtime
rather than the GPU. On a CPU-only box, delete the `deploy.resources` block
from the `ollama` service instead; it will read documents slowly but it will
read them.

```bash
# On the server, as a non-root user in the docker group
git clone <your-repo> docuparse && cd docuparse
cp .env.production.example deploy/.env
chmod 600 deploy/.env
$EDITOR deploy/.env          # every blank must be filled

cd deploy
docker compose -f docker-compose.prod.yml up -d --build

# Pull the vision model once (a few GB)
docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen2.5vl:7b

docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f api
```

Caddy gets Let's Encrypt certificates on first start. Migrations run
automatically in the API container; the job workers wait rather than racing
to stamp the same revision.

**Verify before you point a customer at it:**

```bash
curl https://api.example.com/health     # {"status":"ok"}
curl https://api.example.com/ready      # checks the database too
curl -I http://api.example.com          # 308 redirect to https
```

Then open `https://app.example.com`, create the first firm, and invite
yourself a second login from **Your firm**.

Two failures worth recognising on sight:

- **A certificate error on first load** usually means DNS was not pointing
  here when Caddy started. Fix the record, then `docker compose restart caddy`.
- **The dashboard loads but every request fails with no status** is CORS:
  `APP_URL` is not the address the browser is actually on. It is the
  dashboard's URL, not the API's.

### Updating

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

One caveat: `NEXT_PUBLIC_API_URL` is baked into the browser bundle at build
time, so changing `API_DOMAIN` needs a rebuild of `web`, not a restart.

---

## 4. Monthly cost

Rupee figures are indicative at ~₹85/USD and move — **check current
pricing before you commit to anything.** Cloud providers change prices and
Meta has changed WhatsApp pricing more than once.

### Infrastructure

| | Cheap (Hetzner + your own GPU) | Comfortable (AWS Mumbai) |
|---|---|---|
| App server | ~₹5,000 (CCX33) | ~₹12,000 (c6i.2xlarge) |
| GPU for Ollama | ~₹15,000 (GEX44, RTX 4000) | ~₹55,000 (g5.xlarge) |
| Postgres managed | ~₹3,500 | ~₹14,000 (RDS db.m6g.large) |
| Object storage 500 GB | ~₹500 (R2/B2) | ~₹1,200 (S3) |
| Backups + bandwidth | ~₹1,000 | ~₹3,000 |
| **Infrastructure** | **~₹25,000/mo** | **~₹85,000/mo** |

Hetzner is in Germany, which weakens the data-residency pitch you chose.
**E2E Networks, Tata Communications and AWS Mumbai are the Indian options** —
E2E in particular sits between the two columns on price, and is worth a
quote given you want data in India.

A real alternative for year one: **buy the GPU box outright.** An RTX 4090
workstation is roughly ₹2.5–3 lakh and pays for itself against AWS in about
six months, then costs you electricity. Colocate it, or run it in the office
if your connection is stable.

### Per-message: WhatsApp

Meta bills per 24-hour *conversation*, not per message, and prices by
category. Chasing a client for documents is a **utility** conversation.
Indian utility conversations have been in the region of **₹0.30–0.40** each;
**service** conversations — where the *client* messages you first — have been
free.

That last point matters a lot for your unit economics: when a client replies
"bhej diya" and sends the invoice, that whole exchange is on the free side.
You pay for the chase, not the conversation.

| Firms | Clients | Chases/month | Estimated |
|---|---|---|---|
| 50 | 5,000 | ~15,000 | ~₹5,500 |
| 150 | 15,000 | ~45,000 | ~₹16,500 |

**Verify against Meta's current rate card for India before you price your
own plans.** Being wrong here is being wrong about your margin.

### Per-document: AI

**Zero marginal cost.** That is the point of the GPU box, and it is a real
competitive advantage — your cost per document does not rise with volume,
so you can price per firm instead of per document and not get squeezed.

For comparison, hosted vision APIs land roughly ₹1.5–4 per document
depending on model. At 30,000 documents a month that is ₹45,000–120,000 —
more than the entire GPU-inclusive infrastructure bill. Confirm the current
rates if you ever reconsider.

Most B2B invoices are digital PDFs that the `qr` and `text_layer` tiers read
with no model at all. The model is the fallback for scans and photos.

### Per-call: voice

Exotel outbound to Indian mobiles runs in the region of **₹0.50–0.70 per
minute**, plus a platform fee (roughly ₹1,000–3,000/mo depending on plan).
Chase calls are short — 30 to 60 seconds. If 5% of chases escalate to a
call, 150 firms is ~2,250 calls ≈ **₹1,500–2,500/month**.

Voice is off by default for every firm, so this is genuinely optional spend.

### All in

Infrastructure here is an Indian host (E2E or AWS Mumbai), so it sits
between the two columns above rather than matching either. It rises from 50
to 150 firms because that is roughly where you add the second app server and
move documents to object storage — everything else on the line is fixed.

| | 50 firms | 150 firms |
|---|---|---|
| Infrastructure (Indian host) | ~₹35,000 | ~₹45,000 |
| WhatsApp | ~₹5,500 | ~₹16,500 |
| AI | ₹0 | ₹0 |
| Voice (if on) | ~₹800 | ~₹2,500 |
| **Total** | **~₹41,000/mo** | **~₹64,000/mo** |
| **Per firm** | **~₹820** | **~₹425** |

At ₹3,000–5,000 per firm per month, gross margin is comfortable and it
*improves* with scale, because the GPU and the servers are fixed. That is a
much better shape than a per-document-cost business.

**Do not read these as a quote.** They are the right order of magnitude and
the right structure; the exact numbers need current rate cards.

---

## 5. Before a real customer

Security:

- [ ] `JWT_SECRET`, `WEBHOOK_SECRET`, `POSTGRES_PASSWORD` freshly generated, never pasted anywhere
- [ ] `deploy/.env` is `chmod 600` and not in git
- [ ] `APP_ENV=production` (refuses mock providers, refuses wildcard CORS, refuses an unbacked rate limiter)
- [ ] `WHATSAPP_WEBHOOK_SECRET` set — without it anyone who learns the URL can post fake client messages into a firm's timeline
- [ ] Firewall: only 80, 443 and SSH; Postgres never public
- [ ] SSH keys only, root login off, unattended-upgrades on

Operational:

- [ ] `/backups` copied somewhere off this machine — a backup on the same disk protects you from a bad migration, not from losing the box
- [ ] **A restore rehearsed.** An untested backup is a hope.
- [ ] Uptime check on `https://api.example.com/ready`
- [ ] Disk alert at 80% — documents and Postgres share the volume
- [ ] `docker compose logs` bounded (already set: 20 MB × 5 per service)

Honesty, which is the part that costs you a customer if you skip it:

- [ ] **Accuracy measured on ~200 real invoices** before you put a number in front of anyone. Nothing in this repository has ever been measured against real documents, and the README deliberately says so.
- [ ] **Tally export tested against a real Tally install**, not just the format
- [ ] **The Exotel flow's opening line says it is an automated assistant** calling on the firm's behalf. This code cannot put words into the call — that script lives in Exotel's dashboard and it is on you.
- [ ] A privacy policy that matches what the system does: documents kept `DOCUMENT_RETENTION_DAYS`, read on your own hardware, never sent to a third-party model.

---

## 6. What is still unproven

Said plainly, because a deployment guide that implies more confidence than
exists is how people get hurt.

What *has* been checked, booted in `APP_ENV=production` against real
Postgres and Redis:

| Misconfiguration | What happens |
|---|---|
| No `REDIS_URL` | Refuses to boot, naming the variable and why |
| `REDIS_URL` set but unreachable | Refuses to boot rather than silently multiplying every limit by the worker count |
| `ALLOW_IN_MEMORY_RATE_LIMIT=true` | Starts — the escape hatch is explicit, so it cannot happen by accident |
| `WHATSAPP_PROVIDER=mock` | Refused: sends nothing and reports success |
| Wildcard CORS | Refuses to boot |
| A site that is not the dashboard | Preflight rejected, no `access-control-allow-origin` returned |

And the things that are still open:

- **No live WhatsApp or Exotel call has ever been made from this code.** Both adapters are tested against a mocked transport, which proves the request shape and the error handling — not that Meta accepts it. The first real send will surface something. Budget a debugging session, not a rewrite.
- **No accuracy figure exists.** Not a low one, not a high one. None has been measured.
- **This compose file has never been run end to end.** It validates (`docker compose config` resolves every variable, and only Caddy publishes a port), and the application itself has been booted in `APP_ENV=production` with four uvicorn workers against real Postgres and Redis — `/health` and `/ready` both answered, `/ready` confirmed the database, CORS allowed the dashboard origin and refused another site, and each of the four refusals below fired. What has *not* run is the containers: the build machine could not pull base images, so Caddy, Ollama, the TLS handshake and the dashboard image are untried. Do the first `up -d --build` on a staging box, not on the box a customer is pointed at.
- **Load has never been tested.** The sizing above is arithmetic from connection pools and normal FastAPI throughput, not a measured benchmark. Run `k6` or `locust` against staging before filing week, not during it.
