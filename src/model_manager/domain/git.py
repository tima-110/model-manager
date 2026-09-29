"""Git operations for publishing generated artifacts."""
from __future__ import annotations

import os
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ArtifactGitError(Exception):
    """Raised when a git publish step fails."""


def _run_git(
    cmd: list[str],
    cwd: Path,
    timeout: int = 60,
    check: bool = True,
) -> tuple[int, str, str]:
    """Execute a git command with timeout and disabled interactive prompts."""
    if not shutil.which("git"):
        raise ArtifactGitError("git executable not found on PATH.")

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            ["git"] + cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise ArtifactGitError(f"git command 'git {' '.join(cmd)}' timed out after {timeout}s.") from e
    except Exception as e:
        raise ArtifactGitError(f"Failed to execute git command 'git {' '.join(cmd)}': {e}") from e

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    if check and proc.returncode != 0:
        msg = stderr or stdout or f"exit code {proc.returncode}"
        raise ArtifactGitError(f"git {' '.join(cmd)} failed: {msg}")

    return proc.returncode, stdout, stderr


def publish_artifact_git(output: Path, branch: str = "") -> dict[str, Any]:
    """Commit and push an artifact file to a git remote repository.

    Args:
        output: Path to the generated artifact file.
        branch: Target remote branch name, or empty string to push current branch.

    Returns:
        dict containing:
            committed (bool)
            pushed (bool)
            commit (str | None)
            branch (str)
            output (Path)
            reason (str | None)

    Raises:
        ArtifactGitError: If any git step fails (e.g., non-repo directory, git failure).
    """
    out_path = Path(output).resolve()
    if not out_path.exists():
        raise ArtifactGitError(f"Artifact file does not exist: {out_path}")

    parent_dir = out_path.parent

    # 1. Verify parent directory is inside a git work tree
    code, is_tree, err = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=parent_dir, check=False)
    if code != 0 or is_tree != "true":
        raise ArtifactGitError(f"Directory '{parent_dir}' is not inside a git repository.")

    # Get current local branch name
    _, cur_branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=parent_dir, check=False)
    target_branch = branch.strip() or cur_branch.strip() or "main"

    # 2. Stage the artifact file
    _run_git(["add", out_path.name], cwd=parent_dir)

    # 3. Check git status for changes
    _, status_out, _ = _run_git(["status", "--porcelain", "--", out_path.name], cwd=parent_dir)
    if not status_out:
        return {
            "committed": False,
            "pushed": False,
            "commit": None,
            "branch": target_branch,
            "output": out_path,
            "reason": "no changes",
        }

    # 4. Commit changed artifact
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    commit_msg = f"Update {out_path.name} ({ts})"
    _run_git(["commit", "-m", commit_msg, "--", out_path.name], cwd=parent_dir)

    # 5. Get commit SHA
    _, commit_sha, _ = _run_git(["rev-parse", "HEAD"], cwd=parent_dir)

    # 6. Push to remote
    if branch.strip():
        _run_git(["push", "origin", branch.strip()], cwd=parent_dir, timeout=300)
        pushed_branch = branch.strip()
    else:
        # Plain git push
        push_code, _, push_err = _run_git(["push"], cwd=parent_dir, timeout=300, check=False)
        if push_code == 0:
            pushed_branch = target_branch
        else:
            # Fallback to git push -u origin <current-branch> if no upstream or plain push failed
            err_msg = push_err.lower()
            if "no upstream" in err_msg or "has no upstream branch" in err_msg or "set-upstream" in err_msg or push_code != 0:
                _run_git(["push", "-u", "origin", target_branch], cwd=parent_dir, timeout=300)
                pushed_branch = target_branch
            else:
                raise ArtifactGitError(f"git push failed: {push_err}")

    return {
        "committed": True,
        "pushed": True,
        "commit": commit_sha,
        "branch": pushed_branch,
        "output": out_path,
        "reason": None,
    }
