"""Probe candidate alpha wallets: SOL, recent sigs, pump creates."""
import json
import time
import urllib.request

RPC = "https://api.mainnet-beta.solana.com"
PUMP = "https://frontend-api-v3.pump.fun"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

WALLETS = [
    ("seed-martinshkreli", "AkK5BtfBhj3cJi1f9LVXodbBLxRiePqffm5uiQYpDYQr"),
    ("decu", "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9"),
    ("trunoest", "ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT"),
    ("samsrep", "CUHBzSPSaNS3tArEtM3maSV6pNdJhHJFYZpurPPK9P7H"),
    ("tdmilky", "AuPp4YTMTyqxYXQnHc5KUc6pUuCSsHQpBJhgnD45yqrf"),
    ("kaythedoc", "DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt"),
    ("trenchman", "Hw5UKBU5k3YudnGwaykj5E8cYUidNMPuEewRRar5Xoc7"),
    ("slingoor", "5YRgrP3mjGzrzirYYN5HAQH19cTYREYwGxW6XRJQUzij"),
    ("theo", "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt"),
    ("chester", "PMJA8UQDyWTFw2Smhyp9jGA6aTaP7jKHR7BPudrgyYN"),
    ("kev", "BTf4A2exGK9BCVDNzy65b9dUzXgMqB4weVkvTMFQsadd"),
    ("jason", "ACTbvbNm5qTLuofNRPxFPMtHAAtdH1CtzhCZatYHy831"),
    ("publix", "86AEJExyjeNNgcp7GrAvCXTDicf5aGWgoERbXFiG1EdD"),
    ("cented", "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"),
]


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        RPC, data=body, headers={"Content-Type": "application/json", **UA}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


now = time.time()
for name, w in WALLETS:
    try:
        bal = rpc("getBalance", [w])
        lam = ((bal.get("result") or {}).get("value") or 0) / 1e9
    except Exception as e:
        lam = -1
        print(name, "bal err", e)
    try:
        sigs = rpc("getSignaturesForAddress", [w, {"limit": 8}])
        rows = sigs.get("result") or []
    except Exception:
        rows = []
    last_h = -1
    if rows:
        bt = rows[0].get("blockTime") or 0
        last_h = (now - bt) / 3600 if bt else -1
    created = []
    try:
        created = http_get(
            f"{PUMP}/coins/user-created-coins/{w}?offset=0&limit=5"
        )
        if isinstance(created, dict):
            created = created.get("coins") or created.get("data") or []
    except Exception:
        created = []
    n_create = len(created) if isinstance(created, list) else 0
    print(
        f"{name:14} sol={lam:8.3f} last_tx={last_h:6.1f}h sigs={len(rows):2} created~{n_create}"
    )
    time.sleep(0.15)
