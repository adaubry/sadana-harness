# Installing a box

Fresh Ubuntu 22.04 or 24.04 to a box connected to a console, in the steps
below — or run `scripts/install.sh`, which performs exactly this sequence
unattended, parameterized by the environment variables its own header
documents.

## 1. Prerequisites

```bash
sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-venv git
```

## 2. Clone at a tag

```bash
git clone --branch <tag> -- <repository-url> ~/sadana-harness
cd ~/sadana-harness
```

Always a tag, never a branch — `sadana --version` and a remote `upgrade`
both depend on the checked-out tag naming a real release
(`CLAUDE.md`: "a release tag name is byte-identical to
`sadana.__version__` at that commit").

## 3. The virtual environment

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e . --quiet
```

Nothing here is ever activated — every command below calls `.venv/bin/sadana`
directly, or lets `make`/the installed unit file put `.venv/bin` on `PATH`
for you.

## 4. `sadana setup`

```bash
.venv/bin/sadana setup --openrouter-key <key> --webhook-secret <secret>
```

Fills `state_dir/.env`: the model provider key and the secret the gateway's
own webhook channel signs with. Neither is ever passed on a command line a
process list can see for longer than this one call.

## 5. `sadana enroll`

```bash
.venv/bin/sadana enroll <token> --relay <relay-url> --console <console-url>
```

`<token>` is the one-time, one-hour enrollment token minted in the console.
The box generates its own key pair here — the private half never leaves
this machine, in a log, an error, or anywhere else — and posts only the
public half to the relay. `--console` is optional when the token names its
own issuer; give it explicitly when it does not, and the box will say
plainly that it is trusting the token's claims unverified in that case.

## 6. Install and start the gateway

```bash
sudo .venv/bin/sadana gateway install
sudo .venv/bin/sadana gateway start
```

Installs `sadana-gateway` as a systemd service running as the user who
invoked `sudo` (never `root`, unless `--run-as-user root` is passed
explicitly) and starts it. This is also the process that opens the one
outbound tether connection.

**Before this step**, grant that same user permission to restart its own
service without a password — the tether's own `upgrade` action needs to run
`systemctl restart sadana-gateway` from inside the (non-root) gateway
process itself:

```bash
echo "$(whoami) ALL=(root) NOPASSWD: /usr/bin/systemctl restart sadana-gateway" \
  | sudo tee /etc/sudoers.d/sadana-gateway-restart
sudo chmod 0440 /etc/sudoers.d/sadana-gateway-restart
```

`scripts/install.sh` does this for you, scoped to exactly that one command.

## 7. Confirm it's connected

```bash
.venv/bin/sadana gateway status
```

## No inbound rule, ever

**The box dials out. It never listens for a console-facing connection, and
no security-group or firewall rule should ever forward a port to it for
that purpose.** If one exists, that is a finding, not a convenience —
hermes-agent's own relay protocol was retrofitted away from an
inbound-delivery design for exactly this reason: it required every gateway
to expose a reachable inbound URL, which is impossible for a box behind an
arbitrary customer's own firewall (`docs/relay-connector-contract.md` §3,
cited in `spec.md`).

## Reconnecting

A dropped connection is expected, not exceptional. The box reconnects with
exponential backoff and full jitter, capped at 60 seconds between attempts;
a `429` from the relay is obeyed exactly, sleeping for the `Retry-After`
it names rather than backing off further. Nothing about a reconnect needs
an operator, and nothing here is instantaneous — the console's own
`GET /changes?since=` catch-up, seeded by the reconnected box's own `hello`
frame, is what closes the gap a drop leaves, not a promise that no gap ever
opens.

## Lifecycle commands

- `sudo .venv/bin/sadana gateway stop` / `restart` / `status` — the
  installed service itself.
- An `upgrade` action, sent through the console, checks out a new tag,
  reinstalls, and restarts the service — the box reports whether it landed
  on the requested version the next time it boots.
- A `deregister` action stops the tether and deletes this box's local
  identity and private key. Everything else this box holds is the
  customer's and is left exactly as it was.
- A `purge-account` action removes everything tied to one account (memory,
  persona selection, schedules, conversation ownership) — not the box's
  identity, and not shared data like conversation content itself.

## Local development: the test relay

No real console relay exists yet. `scripts/test_relay.py` speaks the same
protocol from one local process — `/enroll`, `/connect`, `/forward`,
`/token`, its own JWKS, and a hand-run `--retry-after`/`--die-after` for
exercising the reconnect paths above without waiting on a real outage.
`scripts/prove_tether_e2e.py` drives the real `sadana` CLI against it
end to end; its own output is this work item's Deploy-stage evidence.
Treat the test relay as a fixture, never as a second production path — the
box does not change when the console's real relay replaces it.
