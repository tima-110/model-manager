"""Domain logic for CLI scheduling, service management, and pipeline execution."""
from __future__ import annotations

import os
import sys
import shutil
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict

from model_manager.config import AppConfig, save_config
from model_manager.domain import scores, providers, yaml_gen, fallbacks, model_group_aliases

SYSTEMD_SERVICE_NAME = "model-manager-schedule"
LAUNCHD_LABEL = "com.model-manager.schedule"


def _get_executable_cmd() -> str:
    """Find the path to the model-manager executable or Python invocation."""
    mm_bin = shutil.which("model-manager")
    if mm_bin:
        return mm_bin
    return f"{sys.executable} -m model_manager.main"


def _parse_time_hh_mm(time_str: str) -> tuple[int, int]:
    """Parse HH:MM time string into hour and minute integers."""
    parts = time_str.split(":")
    if len(parts) != 2:
        return 2, 0
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        return hour, minute
    except ValueError:
        return 2, 0


def install_schedule(
    config: AppConfig,
    frequency: str = "daily",
    time: str = "02:00",
    max_scans: int = 2,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Install OS schedule service/timer and update config file."""
    config.schedule.enabled = True
    config.schedule.frequency = frequency
    config.schedule.time = time
    config.schedule.max_scans = max_scans
    save_config(config, config_path)

    exec_cmd = _get_executable_cmd()
    system_type = platform.system().lower()
    details: dict[str, Any] = {
        "enabled": True,
        "frequency": frequency,
        "time": time,
        "max_scans": max_scans,
        "os": system_type,
        "installed_files": [],
        "status": "installed",
    }

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
    </array>
    <key>StartCalendarInterval</key>
    <dict>
"""
        if frequency == "hourly":
            plist_content += f"""        <key>Minute</key>
        <integer>{minute}</integer>
"""
        elif frequency == "weekly":
            plist_content += f"""        <key>Weekday</key>
        <integer>1</integer>
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

        try:
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
            subprocess.run(["launchctl", "load", "-w", str(plist_path)], capture_output=True)
        except Exception as e:
            details["warning"] = f"launchctl execution failed: {e}"

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
ExecStart={exec_cmd} schedule run
"""
        service_path.write_text(service_content)
        details["installed_files"].append(str(service_path))

        if frequency == "hourly":
            on_calendar = f"*-*-* *:{minute:02d}:00"
        elif frequency == "weekly":
            on_calendar = f"Mon *-*-* {hour:02d}:{minute:02d}:00"
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

        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", f"{SYSTEMD_SERVICE_NAME}.timer"],
                capture_output=True,
            )
        except Exception as e:
            details["warning"] = f"systemctl execution failed: {e}"

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
            try:
                subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)
            except Exception:
                pass
            plist_path.unlink()
            details["removed_files"].append(str(plist_path))
    else:
        systemd_dir = Path.home() / ".config" / "systemd" / "user"
        service_path = systemd_dir / f"{SYSTEMD_SERVICE_NAME}.service"
        timer_path = systemd_dir / f"{SYSTEMD_SERVICE_NAME}.timer"

        try:
            subprocess.run(
                ["systemctl", "--user", "stop", f"{SYSTEMD_SERVICE_NAME}.timer"],
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "--user", "disable", f"{SYSTEMD_SERVICE_NAME}.timer"],
                capture_output=True,
            )
        except Exception:
            pass

        if timer_path.exists():
            timer_path.unlink()
            details["removed_files"].append(str(timer_path))
        if service_path.exists():
            service_path.unlink()
            details["removed_files"].append(str(service_path))

        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        except Exception:
            pass

    return details


def get_schedule_status(config: AppConfig) -> dict[str, Any]:
    """Return status details of the schedule configuration and system service."""
    system_type = platform.system().lower()
    status_info: dict[str, Any] = {
        "enabled": config.schedule.enabled,
        "frequency": config.schedule.frequency,
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

    1. scores fetch
    2. scores sync
    3. providers fetch-all
    4. providers scan-all --max-scans <max_scans>
    5. litellm generate config --all-providers
    6. litellm generate fallbacks
    7. litellm generate aliases
    """
    results: dict[str, Any] = {"steps": [], "errors": []}

    # Step 1: scores fetch
    try:
        api_key = scores.get_api_key()
        if api_key:
            raw_scores = scores.fetch_aa_data(api_key, config)
            if raw_scores:
                scores.process_aa_data(raw_scores, config)
                scores.merge_agentic_scores(config)
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
            providers.fetch_provider_models(p, probe=False, config=config)
            fetch_success += 1
        except Exception as e:
            results["errors"].append(f"providers fetch ({p.name}): {e}")
    results["steps"].append(f"providers fetch-all: completed for {fetch_success}/{len(all_providers)} providers")

    # Step 4: providers scan-all --max-scans <max_scans>
    max_scans = config.schedule.max_scans
    scan_success = 0
    for p in all_providers:
        try:
            providers.scan_provider_models(
                p,
                config=config,
                filter_str=None,
                only_up=False,
                only_down=False,
                max_scans=max_scans,
                debug=False,
            )
            scan_success += 1
        except Exception as e:
            results["errors"].append(f"providers scan ({p.name}): {e}")
    results["steps"].append(f"providers scan-all: completed for {scan_success}/{len(all_providers)} providers")

    # Step 5: litellm generate config --all-providers
    providers_to_gen = [
        p_name for p_name, pc in config.providers.items()
        if pc.keys and pc.litellm_prefix
    ]
    gen_config_count = 0
    for prov in providers_to_gen:
        try:
            yaml_gen.generate_provider_yaml(config, prov, dry_run=False)
            gen_config_count += 1
        except Exception as e:
            results["errors"].append(f"litellm generate config ({prov}): {e}")
    results["steps"].append(f"litellm generate config: completed for {gen_config_count} providers")

    # Step 6: litellm generate fallbacks
    try:
        fallbacks.generate_fallbacks_yaml(config, dry_run=False)
        results["steps"].append("litellm generate fallbacks: success")
    except Exception as e:
        results["errors"].append(f"litellm generate fallbacks: {e}")

    # Step 7: litellm generate aliases
    try:
        model_group_aliases.generate_aliases_yaml(config, dry_run=False)
        results["steps"].append("litellm generate aliases: success")
    except Exception as e:
        results["errors"].append(f"litellm generate aliases: {e}")

    return results
