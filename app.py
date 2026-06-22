#!/usr/bin/env python3
"""
Cloudflare Tunnel Manager - Web UI
管理 Cloudflare Tunnel 的轻量 Web 面板
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import hashlib
from pathlib import Path
from flask import Flask, render_template, request, jsonify, g

app = Flask(__name__)
SRC_DIR = Path(__file__).parent
DATA_DIR = Path.home() / ".cf-tunnel-manager"
DB_PATH = DATA_DIR / "tunnels.db"
CLOUDFLARED_BIN = DATA_DIR / "bin" / "cloudflared"
CREDS_FILE = DATA_DIR / "credentials.json"
CONFIG_DIR = DATA_DIR / "configs"
LOG_DIR = DATA_DIR / "logs"
PID_DIR = DATA_DIR / "pids"

API_BASE = "https://api.cloudflare.com/client/v4"

for d in [DATA_DIR, CONFIG_DIR, LOG_DIR, PID_DIR, DATA_DIR / "bin"]:
    d.mkdir(parents=True, exist_ok=True)

# Cache zones to avoid repeated Cloudflare API calls
_zones_cache = None
_zones_cache_key = None


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tunnels (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            domain TEXT NOT NULL,
            subdomain TEXT NOT NULL,
            target TEXT NOT NULL,
            tunnel_token TEXT,
            dns_record_id TEXT,
            zone_id TEXT,
            status TEXT DEFAULT 'stopped',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tunnel_id TEXT,
            message TEXT,
            level TEXT DEFAULT 'info',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    db.commit()
    db.close()


def load_credentials():
    if CREDS_FILE.exists():
        with open(CREDS_FILE) as f:
            return json.load(f)
    return {}


def save_credentials(data):
    global _zones_cache, _zones_cache_key
    _zones_cache = None  # invalidate cache
    _zones_cache_key = None
    with open(CREDS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def cf_headers():
    creds = load_credentials()
    return {
        "Authorization": f"Bearer {creds.get('api_token', '')}",
        "Content-Type": "application/json",
    }


def cf_request(method, path, body=None):
    """Call Cloudflare API"""
    import urllib.request
    import urllib.error

    url = f"{API_BASE}{path}"
    headers = cf_headers()
    data = json.dumps(body).encode() if body else None

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read())
        return {"success": False, "errors": err.get("errors", [{"message": str(e)}])}


def add_log(tunnel_id, message, level="info"):
    db = get_db()
    db.execute(
        "INSERT INTO logs (tunnel_id, message, level) VALUES (?, ?, ?)",
        (tunnel_id, message, level),
    )
    db.commit()


def env_flag(name, default=True):
    """Read a boolean environment flag."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def ensure_cloudflared_available():
    """
    Ensure DATA_DIR/bin/cloudflared exists.

    In Docker, /root/.cf-tunnel-manager is normally a mounted volume. That
    volume hides the build-time symlink created in the image, so recreate a
    symlink inside the mounted data directory on every startup when needed.
    """
    if CLOUDFLARED_BIN.exists():
        try:
            CLOUDFLARED_BIN.chmod(CLOUDFLARED_BIN.stat().st_mode | 0o111)
        except OSError:
            pass
        return True

    bundled = Path("/usr/local/bin/cloudflared")
    source = bundled if bundled.exists() else None
    if source is None:
        found = shutil.which("cloudflared")
        source = Path(found) if found else None
    if source is None:
        return False

    CLOUDFLARED_BIN.parent.mkdir(parents=True, exist_ok=True)
    if CLOUDFLARED_BIN.exists() or CLOUDFLARED_BIN.is_symlink():
        CLOUDFLARED_BIN.unlink(missing_ok=True)

    try:
        CLOUDFLARED_BIN.symlink_to(source)
    except OSError:
        try:
            shutil.copy2(source, CLOUDFLARED_BIN)
            CLOUDFLARED_BIN.chmod(0o755)
        except OSError:
            return False
    return True


def _pid_matches_tunnel(pid, tunnel_id):
    """Best-effort guard against stale persisted PID files."""
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.exists():
        # Non-Linux fallback: os.kill(pid, 0) already proved the PID exists.
        return True
    try:
        cmdline = cmdline_path.read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
    except OSError:
        return False
    return "cloudflared" in cmdline and tunnel_id in cmdline


def get_tunnel_runtime_status(tunnel_id, cleanup_stale=True):
    """Return (running, pid) and remove stale PID files when safe."""
    pid_file = PID_DIR / f"{tunnel_id}.pid"
    if not pid_file.exists():
        return False, None
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        if cleanup_stale:
            pid_file.unlink(missing_ok=True)
        return False, None

    if not _pid_matches_tunnel(pid, tunnel_id):
        if cleanup_stale:
            pid_file.unlink(missing_ok=True)
        return False, None
    return True, pid


def start_tunnel_process(tunnel_id, reason="manual"):
    """Start a tunnel from its persisted cloudflared config."""
    db = get_db()
    tunnel = db.execute("SELECT * FROM tunnels WHERE id = ?", (tunnel_id,)).fetchone()
    if not tunnel:
        return False, "隧道不存在", None

    if not ensure_cloudflared_available():
        return False, "cloudflared 未安装，请先安装", None

    config_file = CONFIG_DIR / f"{tunnel_id}.yml"
    if not config_file.exists():
        return False, "配置文件不存在", None

    # Kill an existing cloudflared process for this tunnel, and clean stale PID files.
    stop_tunnel_process(tunnel_id)

    log_file = LOG_DIR / f"{tunnel_id}.log"
    pid_file = PID_DIR / f"{tunnel_id}.pid"

    try:
        with open(log_file, "a") as log:
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {reason} start ===\n")
            log.flush()
            proc = subprocess.Popen(
                [
                    str(CLOUDFLARED_BIN),
                    "tunnel",
                    "--config",
                    str(config_file),
                    "run",
                    tunnel_id,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(CONFIG_DIR),
            )
        pid_file.write_text(str(proc.pid))
        db.execute("UPDATE tunnels SET status = 'running' WHERE id = ?", (tunnel_id,))
        db.commit()
        if reason == "autostart":
            add_log(tunnel_id, "容器启动自动恢复隧道")
        else:
            add_log(tunnel_id, "隧道已启动")
        return True, "", proc.pid
    except Exception as e:
        return False, f"启动失败: {e}", None


def autostart_tunnels():
    """Start all persisted tunnels during container/app startup."""
    if not env_flag("AUTO_START_TUNNELS", True):
        print("AUTO_START_TUNNELS is disabled; existing tunnels will not be started.")
        return {"started": 0, "already_running": 0, "skipped": 0, "failed": 0}

    db = get_db()
    tunnels = db.execute("SELECT * FROM tunnels ORDER BY created_at ASC").fetchall()
    summary = {"started": 0, "already_running": 0, "skipped": 0, "failed": 0}
    if not tunnels:
        print("No persisted tunnels to auto-start.")
        return summary

    ensure_cloudflared_available()

    for tunnel in tunnels:
        tunnel_id = tunnel["id"]
        running, _pid = get_tunnel_runtime_status(tunnel_id)
        if running:
            summary["already_running"] += 1
            continue

        config_file = CONFIG_DIR / f"{tunnel_id}.yml"
        if not config_file.exists():
            summary["skipped"] += 1
            add_log(tunnel_id, "容器启动自动恢复跳过：配置文件不存在", "warn")
            continue

        ok, message, _pid = start_tunnel_process(tunnel_id, reason="autostart")
        if ok:
            summary["started"] += 1
        else:
            summary["failed"] += 1
            add_log(tunnel_id, f"容器启动自动恢复失败：{message}", "error")

    print(
        "Auto-start tunnels: "
        f"started={summary['started']}, "
        f"already_running={summary['already_running']}, "
        f"skipped={summary['skipped']}, failed={summary['failed']}"
    )
    return summary


# ─── Routes ────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        creds = load_credentials()
        return jsonify(
            {
                "account_id": creds.get("account_id", ""),
                "api_token_set": bool(creds.get("api_token")),
                "domains": creds.get("domains", []),
                "proxy_enabled": creds.get("proxy_enabled", False),
                "proxy_host": creds.get("proxy_host", ""),
                "proxy_port": creds.get("proxy_port", ""),
            }
        )

    data = request.json
    creds = load_credentials()

    if data.get("account_id"):
        creds["account_id"] = data["account_id"]
    if data.get("api_token"):
        creds["api_token"] = data["api_token"]
    if data.get("domains"):
        creds["domains"] = data["domains"]
    # Proxy settings (only for cloudflared download/update)
    if "proxy_enabled" in data:
        creds["proxy_enabled"] = data["proxy_enabled"]
    if "proxy_host" in data:
        creds["proxy_host"] = data["proxy_host"]
    if "proxy_port" in data:
        creds["proxy_port"] = data["proxy_port"]

    save_credentials(creds)

    # Verify credentials
    result = cf_request("GET", "/user/tokens/verify")
    if result.get("success"):
        return jsonify({"ok": True, "message": "凭证验证通过"})
    return jsonify({"ok": False, "message": result.get("errors", [{}])[0].get("message", "验证失败")}), 400


@app.route("/api/zones")
def api_zones():
    """List available zones (domains), cached until credentials change"""
    global _zones_cache, _zones_cache_key
    creds = load_credentials()
    cache_key = hashlib.md5(
        (creds.get("account_id", "") + "|" + "|".join(sorted(creds.get("domains", [])))).encode()
    ).hexdigest()
    if _zones_cache is not None and _zones_cache_key == cache_key:
        return jsonify(_zones_cache)

    if creds.get("domains"):
        zones = []
        for domain in creds["domains"]:
            result = cf_request("GET", "/zones?name=" + domain)
            if result.get("success"):
                for z in result.get("result", []):
                    zones.append({"id": z["id"], "name": z["name"]})
    else:
        result = cf_request("GET", "/zones")
        if result.get("success"):
            zones = [
                {"id": z["id"], "name": z["name"]} for z in result.get("result", [])
            ]
        else:
            return jsonify({"error": result.get("errors", [{}])[0].get("message")}), 400

    _zones_cache = zones
    _zones_cache_key = cache_key
    return jsonify(zones)


@app.route("/api/tunnels", methods=["GET", "POST"])
def api_tunnels():
    db = get_db()

    if request.method == "GET":
        tunnels = db.execute(
            "SELECT * FROM tunnels ORDER BY created_at DESC"
        ).fetchall()
        # Merge with running status
        results = []
        for t in tunnels:
            d = dict(t)
            running, _pid = get_tunnel_runtime_status(d["id"])
            d["status"] = "running" if running else "stopped"
            results.append(d)
        return jsonify(results)

    # POST: Create tunnel
    data = request.json
    name = data.get("name", f"tunnel-{int(time.time())}")
    domain = data["domain"]
    subdomain = data["subdomain"]
    target = data["target"]
    protocol = data.get("protocol", "http2")
    hostname = f"{subdomain}.{domain}"

    creds = load_credentials()
    account_id = creds["account_id"]

    # 1. Create tunnel in Cloudflare (no config_src → use local config)
    result = cf_request(
        "POST",
        f"/accounts/{account_id}/cfd_tunnel",
        {"name": name},
    )
    if not result.get("success"):
        return jsonify({"error": result.get("errors", [{}])[0].get("message", "创建隧道失败")}), 400

    tunnel = result["result"]
    tunnel_id = tunnel["id"]

    # 2. Get tunnel token (base64-encoded JSON with a/t/s fields)
    token_result = cf_request(
        "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token"
    )
    token_raw = token_result.get("result", "")
    # Decode: token is base64 JSON {a: AccountTag, t: TunnelID, s: TunnelSecret}
    import base64
    try:
        token_json = json.loads(base64.b64decode(token_raw))
        tunnel_account_tag = token_json.get("a", account_id)
        tunnel_secret = token_json.get("s", "")
        tunnel_token = token_raw  # store raw for DB reference
    except Exception:
        tunnel_account_tag = account_id
        tunnel_secret = ""
        tunnel_token = token_raw

    # 3. Get zone ID
    zone_result = cf_request("GET", f"/zones?name={domain}")
    if not zone_result.get("success") or not zone_result.get("result"):
        # Cleanup tunnel on failure
        cf_request("DELETE", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")
        return jsonify({"error": f"找不到域名 {domain}"}), 400
    zone_id = zone_result["result"][0]["id"]

    # 4. Clean existing DNS records for this subdomain, then create new one
    existing_dns = cf_request(
        "GET", f"/zones/{zone_id}/dns_records?name={subdomain}.{domain}"
    )
    if existing_dns.get("success"):
        for rec in existing_dns.get("result", []):
            cf_request("DELETE", f"/zones/{zone_id}/dns_records/{rec['id']}")
    dns_result = cf_request(
        "POST",
        f"/zones/{zone_id}/dns_records",
        {
            "type": "CNAME",
            "name": subdomain,
            "content": f"{tunnel_id}.cfargotunnel.com",
            "proxied": True,
        },
    )
    dns_record_id = None
    if dns_result.get("success"):
        dns_record_id = dns_result["result"]["id"]

    # 5. Write config for cloudflared (use relative paths for Docker portability)
    config = {
        "tunnel": tunnel_id,
        "credentials-file": f"{tunnel_id}.json",
        "protocol": protocol,
        "ingress": [
            {"hostname": hostname, "service": target},
            {"service": "http_status:404"},
        ],
    }

    # Write tunnel credentials file
    cred_json = {
        "AccountTag": tunnel_account_tag,
        "TunnelSecret": tunnel_secret,
        "TunnelID": tunnel_id,
    }
    with open(CONFIG_DIR / f"{tunnel_id}.json", "w") as f:
        json.dump(cred_json, f)

    with open(CONFIG_DIR / f"{tunnel_id}.yml", "w") as f:
        import yaml

        yaml.dump(config, f, default_flow_style=False)

    # 6. Store in DB
    db.execute(
        """INSERT INTO tunnels (id, name, domain, subdomain, target, tunnel_token, dns_record_id, zone_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tunnel_id,
            name,
            domain,
            subdomain,
            target,
            tunnel_token if isinstance(tunnel_token, str) else "",
            dns_record_id,
            zone_id,
        ),
    )
    db.commit()

    add_log(tunnel_id, f"隧道创建成功: {hostname} → {target}")
    return jsonify({"ok": True, "tunnel_id": tunnel_id, "hostname": hostname})


@app.route("/api/tunnels/<tunnel_id>", methods=["DELETE"])
def api_delete_tunnel(tunnel_id):
    db = get_db()
    tunnel = db.execute("SELECT * FROM tunnels WHERE id = ?", (tunnel_id,)).fetchone()
    if not tunnel:
        return jsonify({"error": "隧道不存在"}), 404

    # Stop if running
    stop_tunnel_process(tunnel_id)

    creds = load_credentials()
    account_id = creds["account_id"]

    # Delete from Cloudflare
    cf_request("DELETE", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")

    # Delete DNS record if exists
    if tunnel["dns_record_id"] and tunnel["zone_id"]:
        cf_request(
            "DELETE",
            f"/zones/{tunnel['zone_id']}/dns_records/{tunnel['dns_record_id']}",
        )

    # Clean up files
    for ext in [".json", ".yml"]:
        (CONFIG_DIR / f"{tunnel_id}{ext}").unlink(missing_ok=True)
    (PID_DIR / f"{tunnel_id}.pid").unlink(missing_ok=True)
    (LOG_DIR / f"{tunnel_id}.log").unlink(missing_ok=True)

    db.execute("DELETE FROM tunnels WHERE id = ?", (tunnel_id,))
    db.execute("DELETE FROM logs WHERE tunnel_id = ?", (tunnel_id,))
    db.commit()

    add_log(tunnel_id, "隧道已删除", "warn")
    return jsonify({"ok": True})


@app.route("/api/tunnels/<tunnel_id>/start", methods=["POST"])
def api_start_tunnel(tunnel_id):
    ok, message, pid = start_tunnel_process(tunnel_id)
    if ok:
        return jsonify({"ok": True, "pid": pid})
    status = 404 if message == "隧道不存在" else 400
    if message.startswith("启动失败"):
        status = 500
    return jsonify({"error": message}), status


@app.route("/api/tunnels/<tunnel_id>/stop", methods=["POST"])
def api_stop_tunnel(tunnel_id):
    stopped = stop_tunnel_process(tunnel_id)
    if stopped:
        add_log(tunnel_id, "隧道已停止")
        db = get_db()
        db.execute("UPDATE tunnels SET status = 'stopped' WHERE id = ?", (tunnel_id,))
        db.commit()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "message": "隧道未在运行"})


@app.route("/api/tunnels/<tunnel_id>/status")
def api_tunnel_status(tunnel_id):
    running, pid = get_tunnel_runtime_status(tunnel_id)

    # Get recent logs
    log_file = LOG_DIR / f"{tunnel_id}.log"
    logs = ""
    if log_file.exists():
        try:
            logs = log_file.read_text()[-5000:]
        except Exception:
            logs = ""

    return jsonify({"running": running, "pid": pid, "logs": logs})


@app.route("/api/tunnels/<tunnel_id>/services", methods=["GET", "POST"])
def api_tunnel_services(tunnel_id):
    db = get_db()
    tunnel = db.execute("SELECT * FROM tunnels WHERE id = ?", (tunnel_id,)).fetchone()
    if not tunnel:
        return jsonify({"error": "隧道不存在"}), 404

    if request.method == "GET":
        # Parse existing ingress rules from config
        config_file = CONFIG_DIR / f"{tunnel_id}.yml"
        services = []
        if config_file.exists():
            import yaml

            with open(config_file) as f:
                config = yaml.safe_load(f)
                for rule in config.get("ingress", []):
                    if "hostname" in rule:
                        services.append(
                            {"hostname": rule["hostname"], "service": rule["service"]}
                        )
        return jsonify(services)

    # POST: Add service to existing tunnel
    data = request.json
    new_hostname = f"{data['subdomain']}.{tunnel['domain']}"
    new_target = data["target"]

    config_file = CONFIG_DIR / f"{tunnel_id}.yml"
    if not config_file.exists():
        return jsonify({"error": "配置文件不存在"}), 400

    import yaml

    with open(config_file) as f:
        config = yaml.safe_load(f)

    # Check duplicate hostname
    for rule in config["ingress"]:
        if rule.get("hostname") == new_hostname:
            return jsonify({"error": "该子域名已存在"}), 400

    # Insert before the catch-all
    catch_all = config["ingress"].pop()  # Remove 404 rule
    config["ingress"].append({"hostname": new_hostname, "service": new_target})
    config["ingress"].append(catch_all)

    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    # Create DNS record for the new hostname
    creds = load_credentials()
    zone_result = cf_request("GET", f"/zones?name={tunnel['domain']}")
    if zone_result.get("success") and zone_result.get("result"):
        zone_id = zone_result["result"][0]["id"]
        cf_request(
            "POST",
            f"/zones/{zone_id}/dns_records",
            {
                "type": "CNAME",
                "name": data["subdomain"],
                "content": f"{tunnel_id}.cfargotunnel.com",
                "proxied": True,
            },
        )

    # Restart tunnel if running
    was_running, _pid = get_tunnel_runtime_status(tunnel_id)
    if was_running:
        stop_tunnel_process(tunnel_id)
        time.sleep(1)
        start_tunnel_process(tunnel_id, reason="config-change")

    update_db_tunnel_targets(tunnel_id, config["ingress"])
    add_log(tunnel_id, f"添加服务: {new_hostname} → {new_target}")

    return jsonify({"ok": True, "hostname": new_hostname})


@app.route("/api/logs")
def api_logs():
    tunnel_id = request.args.get("tunnel_id")
    db = get_db()
    if tunnel_id:
        logs = db.execute(
            "SELECT * FROM logs WHERE tunnel_id = ? ORDER BY created_at DESC LIMIT 100",
            (tunnel_id,),
        ).fetchall()
    else:
        logs = db.execute(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return jsonify([dict(row) for row in logs])


@app.route("/api/check-update", methods=["POST"])
def api_check_update():
    """Check if a newer cloudflared version exists"""
    local_version = _get_local_version()
    latest_version = _get_latest_version()
    update_available = False
    if local_version and latest_version and local_version != latest_version:
        update_available = True
    return jsonify({
        "local": local_version,
        "latest": latest_version,
        "update_available": update_available,
    })


def _get_local_version():
    if not CLOUDFLARED_BIN.exists():
        return ""
    try:
        r = subprocess.run([str(CLOUDFLARED_BIN), "--version"], capture_output=True, text=True, timeout=5)
        import re
        m = re.search(r'version\s+(\S+)', r.stdout)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _get_proxy_env():
    """Return proxy env dict based on saved settings"""
    creds = load_credentials()
    if creds.get("proxy_enabled") and creds.get("proxy_host"):
        host = creds["proxy_host"]
        port = creds.get("proxy_port", "7893")
        proxy_url = f"http://{host}:{port}"
        return {
            "HTTPS_PROXY": proxy_url,
            "HTTP_PROXY": proxy_url,
        }
    return {}


def _get_latest_version():
    try:
        import urllib.request
        proxy_env = _get_proxy_env()
        proxy_handler = None
        if proxy_env.get("HTTPS_PROXY"):
            from urllib.request import ProxyHandler, build_opener, install_opener
            proxy_handler = ProxyHandler({"https": proxy_env["HTTPS_PROXY"], "http": proxy_env["HTTP_PROXY"]})
            install_opener(build_opener(proxy_handler))
        req = urllib.request.Request(
            "https://api.github.com/repos/cloudflare/cloudflared/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "cf-tunnel-manager"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            release = json.loads(resp.read())
            return release.get("tag_name", "").lstrip("v")
    except Exception:
        return ""


@app.route("/api/install-cloudflared", methods=["GET", "POST"])
def api_install_cloudflared():
    """GET: check if installed + version; POST: install or update"""
    import platform

    if request.method == "GET":
        if CLOUDFLARED_BIN.exists():
            try:
                result = subprocess.run([str(CLOUDFLARED_BIN), "--version"], capture_output=True, text=True, timeout=5)
                return jsonify({"installed": True, "version": result.stdout.strip()})
            except Exception:
                return jsonify({"installed": False})
        return jsonify({"installed": False})

    # POST: download and install (latest version, no checks)
    system = platform.system().lower()
    machine = platform.machine().lower()

    arch_map = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm"}
    arch = arch_map.get(machine, "amd64")

    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-{system}-{arch}"

    try:
        proxy_env = {**os.environ, **_get_proxy_env()}
        CLOUDFLARED_BIN.unlink(missing_ok=True)
        subprocess.run(
            ["curl", "-sSL", "-o", str(CLOUDFLARED_BIN), url],
            env=proxy_env, check=True, timeout=120
        )
        CLOUDFLARED_BIN.chmod(0o755)
        result = subprocess.run([str(CLOUDFLARED_BIN), "--version"], capture_output=True, text=True)
        return jsonify({"ok": True, "version": result.stdout.strip()})
    except Exception as e:
        return jsonify({"error": f"安装失败: {e}"}), 500


# ─── Helpers ───────────────────────────────────────────────


def stop_tunnel_process(tunnel_id):
    pid_file = PID_DIR / f"{tunnel_id}.pid"
    running, pid = get_tunnel_runtime_status(tunnel_id)
    if not running or pid is None:
        return False
    try:
        # Kill process group
        os.killpg(os.getpgid(pid), 15)  # SIGTERM
        time.sleep(1)
        try:
            os.killpg(os.getpgid(pid), 9)  # SIGKILL
        except OSError:
            pass
    except (OSError, ValueError):
        pass
    pid_file.unlink(missing_ok=True)
    return True


def update_db_tunnel_targets(tunnel_id, ingress_rules):
    db = get_db()
    targets = [r["service"] for r in ingress_rules if "hostname" in r]
    db.execute(
        "UPDATE tunnels SET target = ? WHERE id = ?",
        (", ".join(targets), tunnel_id),
    )
    db.commit()


def check_pyyaml():
    try:
        import yaml  # noqa
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml"])


# ─── Main ──────────────────────────────────────────────────

if __name__ == "__main__":
    check_pyyaml()
    init_db()
    ensure_cloudflared_available()
    with app.app_context():
        autostart_tunnels()
    print("=" * 50)
    print("  Cloudflare Tunnel Manager")
    print("  http://127.0.0.1:5000")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
