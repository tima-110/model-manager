"""Domain logic for CLI scheduling, service management, and pipeline execution."""
from __future__ import annotations

import json
import shlex
import sys
import shutil
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from model_manager.config import AppConfig, save_config
from model_manager.dashboard import generate_dashboard
from model_manager.domain import generate_all as gen_all_mod
from model_manager.domain import blocks, providers, restart, scores, tags
from model_manager.domain import service_env as service_env_mod

SYSTEMD_SERVICE_NAME = "model-manager-schedule"
LAUNCHD_LABEL = "com.model-manager.schedule"


def _get_executable_cmd() -> str:
    """Find the path to the model-manager executable or Python invocation."""
    mm_bin = shutil.which("model-manager")
    if mm_bin:
        return mm_bin
    # NOTE: `model_manager.main` only defines main(); `-m model_manager`
    # routes through __main__.py which actually calls it.
    return f"{sys.executable} -m model_manager"


def _parse_time_hh_mm(time_str: str) -> tuple[int, int]:
    """Parse HH:MM time string into hour and minute integers.

    Raises:
        ValueError: If the string is not a valid HH:MM time.
    """
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time '{time_str}': expected HH:MM format.")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid time '{time_str}': expected HH:MM format.") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time '{time_str}': hour must be 00-23, minute 00-59.")
    return hour, minute


VALID_FREQUENCIES = ("daily", "hourly", "weekly")

# Canonical weekday order (Python convention: Monday=0 .. Sunday=6).
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAY_ABBREVS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SYSTEMD_ABBREVS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_weekday(day: str) -> int:
    """Parse a weekday name or abbreviation into 0=Monday .. 6=Sunday.

    Raises:
        ValueError: If the day is not recognized.
    """
    clean = day.strip().lower()
    if clean in WEEKDAYS:
        return WEEKDAYS.index(clean)
    if clean in WEEKDAY_ABBREVS:
        return WEEKDAY_ABBREVS.index(clean)
    raise ValueError(
        f"Invalid day '{day}'. Use a weekday name (e.g. saturday) or "
        f"abbreviation ({', '.join(WEEKDAY_ABBREVS)})."
    )


def _run_service_cmd(args: list[str]) -> str | None:
    """Run a service-manager command; return warning text on failure, else None."""
    try:
        proc = subprocess.run(args, capture_output=True)
    except FileNotFoundError:
        return f"{args[0]} not found; service step skipped"
    except Exception as e:
        return f"service command failed ({' '.join(args)}): {e}"
    if proc.returncode != 0:
        stderr = proc.stderr
        detail = stderr.decode().strip() if isinstance(stderr, bytes) else str(stderr or "").strip()
        return f"{' '.join(args)} failed: {detail or f'exit {proc.returncode}'}"
    return None


def install_schedule(
    config: AppConfig,
    frequency: str = "daily",
    day: str = "monday",
    time: str = "02:00",
    max_scans: int = 2,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Install OS schedule service/timer and update config file.

    Raises:
        ValueError: If frequency, day, or time is invalid.
    """
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(
            f"Invalid frequency '{frequency}'. Supported values: {', '.join(VALID_FREQUENCIES)}."
        )
    weekday = parse_weekday(day)
    _parse_time_hh_mm(time)  # validate early, before writing anything
    config.schedule.enabled = True
    config.schedule.frequency = frequency
    config.schedule.day = WEEKDAYS[weekday]
    config.schedule.time = time
    config.schedule.max_scans = max_scans
    save_config(config, config_path)

    exec_cmd = _get_executable_cmd()
    system_type = platform.system().lower()
    warnings: list[str] = []
    details: dict[str, Any] = {
        "enabled": True,
        "frequency": frequency,
        "day": WEEKDAYS[weekday],
        "time": time,
        "max_scans": max_scans,
        "os": system_type,
        "installed_files": [],
        "status": "installed",
    }

    # Export keychain secrets for the service. The timer may fire without a
    # login session (locked keyring), so scheduled runs read secrets from
    # this file instead. Interactive use keeps the keychain path.
    env_path = service_env_mod.default_service_env_path()
    try:
        exported = service_env_mod.export_keyring_to_env(env_path)
        details["installed_files"].append(str(env_path))
        details["env_keys"] = exported
    except Exception as e:
        warnings.append(
            f"Could not export keyring to {env_path}: {e}; "
            "scheduled runs will fall back to the keychain"
        )

    if system_type == "darwin":
        # macOS launchd plist
        hour, minute = _parse_time_hh_mm(time)
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True, exist_ok=True)
        plist_path = plist_dir / f"{LAUNCHD_LABEL}.plist"

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exec_cmd.split()[0]}</string>
"""
        if len(exec_cmd.split()) > 1:
            for arg in exec_cmd.split()[1:]:
                plist_content += f"        <string>{arg}</string>\n"
        plist_content += """        <string>schedule</string>
        <string>run</string>
        <string>--env-file</string>
        <string>""" + str(env_path) + """</string>
    </array>
    <key>WorkingDirectory</key>
    <string>""" + str(config.data_dir) + """</string>
    <key>StartCalendarInterval</key>
    <dict>
"""
        if frequency == "hourly":
            plist_content += f"""        <key>Minute</key>
        <integer>{minute}</integer>
"""
        elif frequency == "weekly":
            # launchd Weekday uses tm_wday convention (Sunday=0 .. Saturday=6).
            launchd_weekday = (weekday + 1) % 7
            plist_content += f"""        <key>Weekday</key>
        <integer>{launchd_weekday}</integer>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
"""
        else:  # daily
            plist_content += f"""        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
"""
        plist_content += """    </dict>
</dict>
</plist>
"""
        plist_path.write_text(plist_content)
        details["installed_files"].append(str(plist_path))

        warn = _run_service_cmd(["launchctl", "unload", str(plist_path)])
        if warn:
            warnings.append(warn)
        warn = _run_service_cmd(["launchctl", "load", "-w", str(plist_path)])
        if warn:
            warnings.append(warn)

    else:
        # Default Linux systemd user service & timer
        hour, minute = _parse_time_hh_mm(time)
        systemd_dir = Path.home() / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True, exist_ok=True)

        service_path = systemd_dir / f"{SYSTEMD_SERVICE_NAME}.service"
        timer_path = systemd_dir / f"{SYSTEMD_SERVICE_NAME}.timer"

        service_content = f"""[Unit]
Description=Model Manager Scheduled Task

[Service]
Type=oneshot
WorkingDirectory={config.data_dir}
EnvironmentFile=-{env_path}
ExecStart={exec_cmd} schedule run --env-file {env_path}
"""
        service_path.write_text(service_content)
        details["installed_files"].append(str(service_path))

        if frequency == "hourly":
            on_calendar = f"*-*-* *:{minute:02d}:00"
        elif frequency == "weekly":
            on_calendar = f"{SYSTEMD_ABBREVS[weekday]} *-*-* {hour:02d}:{minute:02d}:00"
        else:  # daily
            on_calendar = f"*-*-* {hour:02d}:{minute:02d}:00"

        timer_content = f"""[Unit]
Description=Model Manager Scheduled Task Timer

[Timer]
OnCalendar={on_calendar}
Persistent=true

[Install]
WantedBy=timers.target
"""
        timer_path.write_text(timer_content)
        details["installed_files"].append(str(timer_path))

        warn = _run_service_cmd(["systemctl", "--user", "daemon-reload"])
        if warn:
            warnings.append(warn)
        warn = _run_service_cmd(
            ["systemctl", "--user", "enable", "--now", f"{SYSTEMD_SERVICE_NAME}.timer"]
        )
        if warn:
            warnings.append(warn)

    # The timer fires whatever executable is on PATH at run time. Verify it
    # actually supports `schedule run` (a stale install would fail silently
    # on every firing).
    probe = shlex.split(exec_cmd) + ["schedule", "run", "--help"]
    try:
        probe_proc = subprocess.run(probe, capture_output=True)
        if probe_proc.returncode != 0:
            warnings.append(
                "Installed executable does not support 'schedule run'; "
                "reinstall model-manager, then reinstall the schedule."
            )
    except Exception as e:
        warnings.append(f"Could not verify scheduled executable: {e}")

    if warnings:
        details["warning"] = "; ".join(warnings)

    return details


def remove_schedule(
    config: AppConfig,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Remove OS schedule service/timer and disable in config file."""
    config.schedule.enabled = False
    save_config(config, config_path)

    system_type = platform.system().lower()
    details: dict[str, Any] = {
        "enabled": False,
        "os": system_type,
        "removed_files": [],
        "status": "removed",
    }

    if system_type == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        if plist_path.exists():
            _run_service_cmd(["launchctl", "unload", "-w", str(plist_path)])
            plist_path.unlink()
            details["removed_files"].append(str(plist_path))
    else:
        systemd_dir = Path.home() / ".config" / "systemd" / "user"
        service_path = systemd_dir / f"{SYSTEMD_SERVICE_NAME}.service"
        timer_path = systemd_dir / f"{SYSTEMD_SERVICE_NAME}.timer"

        _run_service_cmd(["systemctl", "--user", "stop", f"{SYSTEMD_SERVICE_NAME}.timer"])
        _run_service_cmd(["systemctl", "--user", "disable", f"{SYSTEMD_SERVICE_NAME}.timer"])

        if timer_path.exists():
            timer_path.unlink()
            details["removed_files"].append(str(timer_path))
        if service_path.exists():
            service_path.unlink()
            details["removed_files"].append(str(service_path))

        _run_service_cmd(["systemctl", "--user", "daemon-reload"])

    # The env file holds secrets: remove it on uninstall. (Not recorded as
    # an error if already absent.)
    env_path = service_env_mod.default_service_env_path()
    if env_path.exists():
        env_path.unlink()
        details["removed_files"].append(str(env_path))

    return details


def get_schedule_status(config: AppConfig) -> dict[str, Any]:
    """Return status details of the schedule configuration and system service."""
    system_type = platform.system().lower()
    status_info: dict[str, Any] = {
        "enabled": config.schedule.enabled,
        "frequency": config.schedule.frequency,
        "day": config.schedule.day,
        "time": config.schedule.time,
        "max_scans": config.schedule.max_scans,
        "os": system_type,
        "service_installed": False,
    }

    if system_type == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        status_info["service_installed"] = plist_path.exists()
    else:
        timer_path = Path.home() / ".config" / "systemd" / "user" / f"{SYSTEMD_SERVICE_NAME}.timer"
        status_info["service_installed"] = timer_path.exists()

    return status_info


def execute_schedule_pipeline(config: AppConfig) -> dict[str, Any]:
    """Execute the full model-manager schedule pipeline.

    Phase A (plan from desired state):
    1. scores fetch
    2. scores sync
    3. providers fetch-all (absent mappings are ledger-blocked)
    4. providers scan-all (up to ``config.schedule.max_scans`` cycles)
    5. tier tags (block-aware re-tag: fresh scores + fresh ledger, so
       stored tiers match what generation is about to emit)
    6. litellm generate all (provider configs, fallbacks, aliases,
       router_settings — same shared step as the CLI command; ledger
       blocks are honored, user exclusions always win)
    7. litellm request-restart (signals the service to pick up new configs)

    Phase B (validate the deployment, at most one re-pass):
    8. poll the proxy until responsive (cap 5 min), then proxy-scan the
       live deployment; new ledger entries trigger a re-tag plus exactly
       one regenerate + second restart, then finish regardless.
    9. dashboard (regenerates the status page last)

    The pre-generate proxy scan was intentionally removed: it tested the
    *old* deployment, while upstream health is already covered by fetch
    and provider scans. Only the post-restart scan can validate what was
    just deployed.

    Every run appends a record to ``data_dir/schedule_runs.jsonl`` with its
    steps and errors. The restart-request file is left untouched: it is
    consumed by the LiteLLM-side watcher, where every line means restart.
    """
    results: dict[str, Any] = {"steps": [], "errors": []}

    # Step 1: scores fetch
    try:
        api_key = scores.get_api_key()
        if api_key:
            raw_scores = scores.fetch_aa_data(api_key, config)
            if raw_scores:
                scores.process_aa_data(raw_scores, config)
                scores.merge_agentic_scores(config, api_key)
                results["steps"].append("scores fetch: success")
            else:
                results["errors"].append("scores fetch: failed to fetch AA data")
        else:
            results["errors"].append("scores fetch: ARTIFICIAL_ANALYSIS_API_KEY missing")
    except Exception as e:
        results["errors"].append(f"scores fetch: {e}")

    # Step 2: scores sync
    try:
        updated_count = scores.sync_scores_to_models(config)
        results["steps"].append(f"scores sync: updated {updated_count} variants")
    except Exception as e:
        results["errors"].append(f"scores sync: {e}")

    # Step 3: providers fetch-all
    all_providers = providers.list_providers()
    fetch_success = 0
    for p in all_providers:
        try:
            fetched = providers.run_discovery_workflow(p, config, probe=False)
            fetch_success += 1
            try:
                blocks.record_fetch_observations(
                    config, p.name, {m["id"] for m in fetched if m.get("id")}
                )
            except Exception:
                pass
        except Exception as e:
            results["errors"].append(f"providers fetch ({p.name}): {e}")
    results["steps"].append(f"providers fetch-all: completed for {fetch_success}/{len(all_providers)} providers")

    # Step 4: providers scan-all (up to config.schedule.max_scans cycles)
    max_scans = config.schedule.max_scans
    scan_success = 0
    for p in all_providers:
        try:
            summary = providers.scan_provider_models(p, config, max_scans=max_scans)
            scan_success += 1
            try:
                blocks.record_assessment_observations(
                    config, p.name, summary.get("assessments", {})
                )
            except Exception:
                pass
            results["steps"].append(
                f"providers scan ({p.name}): {summary['scanned']} models, {summary['cycles']} cycles"
            )
        except Exception as e:
            results["errors"].append(f"providers scan ({p.name}): {e}")
    results["steps"].append(f"providers scan-all: completed for {scan_success}/{len(all_providers)} providers")

    # Step 5: tier tags (block-aware). Scores are fresh from Step 2 and the
    # ledger is fresh from Steps 3-4, so this is the only point where both
    # inputs are current. Generation below reads these tags.
    try:
        updated, _tier_map, leader = tags.assign_tier_tags(config)
        results["steps"].append(
            f"tags: updated {updated} variants (live leader {leader:.1f})"
        )
    except Exception as e:
        results["errors"].append(f"tags: {e}")

    # Step 6: litellm generate all (shared with the CLI command)
    try:
        gen_result = gen_all_mod.run_generate_all(config, dry_run=False)
        results["steps"].extend(f"litellm {s}" for s in gen_result["steps"])
        results["errors"].extend(f"litellm {e}" for e in gen_result["errors"])
    except Exception as e:
        results["errors"].append(f"litellm generate all: {e}")

    configs_valid = _validate_configs(config, results, phase="generate")

    # Step 7: litellm request-restart so the service picks up the new configs.
    # Skipped when validation failed: never bounce into invalid configs.
    if not configs_valid:
        results["errors"].append("litellm request-restart: skipped (config check failed)")
    else:
        try:
            if results["errors"]:
                reason = f"Scheduled pipeline completed with {len(results['errors'])} error(s)"
            else:
                reason = "Scheduled pipeline completed (config check clean)"
            record = restart.request_restart(config, reason=reason)
            results["steps"].append(f"litellm request-restart: logged at {record['timestamp']}")
        except Exception as e:
            results["errors"].append(f"litellm request-restart: {e}")

    # Step 8: poll, then validate the live deployment with a proxy scan.
    # New ledger entries trigger a re-tag plus exactly one regenerate +
    # second restart.
    _validate_deployment(config, results)

    # Step 9: dashboard (regenerated last, from the freshest data)
    try:
        dashboard_path = generate_dashboard(config)
        results["steps"].append(f"dashboard: wrote {dashboard_path}")
    except Exception as e:
        results["errors"].append(f"dashboard: {e}")

    # Record the run. Kept separate from the restart-request file, which an
    # external watcher consumes line-by-line as restart orders.
    _record_schedule_run(config, results)

    return results


def _wait_for_proxy(
    base_url: str,
    api_key: str,
    *,
    interval: int = 15,
    timeout: int = 300,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll the proxy model list until responsive or the attempt cap expires.

    Attempts are bounded (``ceil(timeout / interval)``) rather than
    wall-clock-gated, so behavior is identical under real and fake clocks.
    """
    import math

    attempts = max(1, math.ceil(timeout / interval))
    for n in range(attempts):
        try:
            from model_manager.domain import litellm_scan as _scan

            _scan.fetch_proxy_models(base_url, api_key, timeout=10)
            return True
        except Exception:
            pass
        if n < attempts - 1:
            sleep(interval)
    return False


def _validate_deployment(config: AppConfig, results: dict[str, Any]) -> None:
    """Phase B: poll, proxy-scan the live deployment, single conditional re-pass.

    Appends step/error lines into ``results`` in place. Regenerates and
    re-restarts at most once, only when the validation scan adds new
    ledger blocks (a re-tag runs first so tiers match the new ledger).
    A dead proxy can only produce track-only failures, so
    it can never trigger a regenerate on its own.
    """
    from model_manager.domain import auth as _auth
    from model_manager.domain import litellm_scan as _scan

    proxy_key = _auth.get_secret(_scan.LITELLM_API_KEY_NAME)
    if not proxy_key:
        results["errors"].append("litellm proxy scan: LITELLM_MASTER_KEY missing")
        return

    if _wait_for_proxy(_scan.DEFAULT_BASE_URL, proxy_key):
        results["steps"].append("litellm proxy: responsive after restart")
    else:
        results["errors"].append("litellm proxy: unresponsive 5 min after restart request")

    before = blocks.blocked_set(config)
    try:
        proxy_targets = _scan.enumerate_targets(config)
        proxy_records = _scan.scan_targets(
            _scan.DEFAULT_BASE_URL, proxy_key,
            proxy_targets["models"] + proxy_targets["aliases"],
            kinds={**{m: "model" for m in proxy_targets["models"]},
                   **{a: "alias" for a in proxy_targets["aliases"]}},
        )
        try:
            blocks.record_probe_observations(
                config, proxy_records, proxy_targets.get("alias_targets")
            )
        except Exception:
            pass
        path = _scan.save_litellm_scan(config, proxy_records)
        n_up = sum(1 for r in proxy_records if r["status"] == "up")
        results["steps"].append(
            f"litellm proxy scan: {n_up}/{len(proxy_records)} up (saved to {path})"
        )
    except Exception as e:
        results["errors"].append(f"litellm proxy scan: {e}")
        return

    new_blocks = blocks.blocked_set(config) - before
    if not new_blocks:
        results["steps"].append("litellm validation: clean, no re-generate needed")
        return

    results["steps"].append(
        f"litellm validation: {len(new_blocks)} new block(s), re-generating once"
    )
    try:
        updated, _tier_map, leader = tags.assign_tier_tags(config)
        results["steps"].append(
            f"tags re-run: updated {updated} variants (live leader {leader:.1f})"
        )
    except Exception as e:
        results["errors"].append(f"tags re-run: {e}")
    try:
        gen_result = gen_all_mod.run_generate_all(config, dry_run=False)
        results["steps"].extend(f"litellm re-generate {s}" for s in gen_result["steps"])
        regen_errors = list(gen_result["errors"])
        results["errors"].extend(f"litellm re-generate {e}" for e in regen_errors)
    except Exception as e:
        results["errors"].append(f"litellm re-generate all: {e}")
        return
    if regen_errors:
        results["errors"].append("litellm re-generate had errors; skipping second restart")
        return
    if not _validate_configs(config, results, phase="re-generate"):
        results["errors"].append("litellm request-restart (re-run): skipped (config check failed)")
        return
    try:
        record = restart.request_restart(
            config,
            reason=f"Scheduled re-run: {len(new_blocks)} new block(s) found post-restart (config check clean)",
        )
        results["steps"].append(f"litellm request-restart (re-run): logged at {record['timestamp']}")
    except Exception as e:
        results["errors"].append(f"litellm request-restart (re-run): {e}")


def _validate_configs(config: AppConfig, results: dict[str, Any], phase: str) -> bool:
    """Run the config integrity check; record outcome. Returns True if clean."""
    from model_manager.domain import litellm_check as check_mod

    try:
        report = check_mod.check_litellm_configs(config)
    except Exception as e:
        results["errors"].append(f"litellm config check ({phase}): {e}")
        return False
    if report["errors"]:
        results["errors"].append(
            f"litellm config check ({phase}): {len(report['errors'])} failure(s): "
            + "; ".join(report["errors"][:3])
        )
        return False
    results["steps"].append(f"litellm config check ({phase}): clean ({len(report['checks'])} checks)")
    return True


def _record_schedule_run(config: AppConfig, results: dict[str, Any]) -> Path | None:
    """Append a per-run record to data_dir/schedule_runs.jsonl.

    Returns the log path, or None if recording failed (never raises; a
    status write must not fail the pipeline it reports on).
    """
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "steps": results.get("steps", []),
        "errors": results.get("errors", []),
        "error_count": len(results.get("errors", [])),
    }
    try:
        log_path = config.data_dir / "schedule_runs.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return log_path
    except Exception:
        return None
