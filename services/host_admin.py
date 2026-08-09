import os
import shutil
import subprocess
import threading


SCHEDULER_ENV_PATH = "/var/www/nest.apstudy.org/.env"
SCHEDULER_SERVICE_NAME = "nest"
SCHEDULER_COMMAND_TIMEOUT_SECONDS = 20
SYSTEM_GIT_REPO_PATH = "/var/www/nest.apstudy.org"
SYSTEM_GIT_COMMAND_TIMEOUT_SECONDS = 60
SYSTEM_RESTART_DELAY_SECONDS = 2
SYSTEM_RESTART_COMMAND_TIMEOUT_SECONDS = 20
SYSTEM_STORAGE_LIMIT_GB = 150
SCHEDULER_EXECUTABLE_FALLBACKS = {
    "git": ("/usr/bin/git", "/bin/git"),
    "sed": ("/usr/bin/sed", "/bin/sed"),
    "sh": ("/bin/sh", "/usr/bin/sh"),
    "ssh": ("/usr/bin/ssh", "/bin/ssh"),
    "sudo": ("/usr/bin/sudo", "/bin/sudo"),
    "systemctl": ("/usr/bin/systemctl", "/bin/systemctl"),
}


def resolve_scheduler_executable(
    name,
    which=shutil.which,
    exists=os.path.exists,
    access=os.access,
    executable_fallbacks=SCHEDULER_EXECUTABLE_FALLBACKS,
):
    found = which(name)
    if found:
        return found
    for candidate in executable_fallbacks.get(name, ()):
        if exists(candidate) and access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(f"Required scheduler command not found: {name}")


def scheduler_command_for_action(
    action,
    resolve_executable=resolve_scheduler_executable,
    env_path=SCHEDULER_ENV_PATH,
    service_name=SCHEDULER_SERVICE_NAME,
):
    if action == "pause":
        replacement = "s/SCHEDULER_ENABLED=1/SCHEDULER_ENABLED=0/g"
    elif action == "resume":
        replacement = "s/SCHEDULER_ENABLED=0/SCHEDULER_ENABLED=1/g"
    else:
        raise ValueError("Unsupported scheduler action.")
    return [
        [resolve_executable("sed"), "-i", replacement, env_path],
        [resolve_executable("systemctl"), "restart", service_name],
    ]


def run_scheduler_control_action(
    action,
    command_for_action=scheduler_command_for_action,
    run_command=subprocess.run,
    timeout_seconds=SCHEDULER_COMMAND_TIMEOUT_SECONDS,
):
    commands = command_for_action(action)
    completed = []
    for command in commands:
        run_command(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        completed.append(command[0])
    return completed


def run_system_git_pull(
    environ=os.environ,
    resolve_executable=resolve_scheduler_executable,
    run_command=subprocess.run,
    repo_path=SYSTEM_GIT_REPO_PATH,
    timeout_seconds=SYSTEM_GIT_COMMAND_TIMEOUT_SECONDS,
):
    git_env = environ.copy()
    git_env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    git_env["GIT_SSH"] = resolve_executable("ssh")
    command = [resolve_executable("git"), "-C", repo_path, "pull"]
    return run_command(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
        timeout=timeout_seconds,
    )


def schedule_system_restart(
    environ=os.environ,
    resolve_executable=resolve_scheduler_executable,
    popen=subprocess.Popen,
    devnull=subprocess.DEVNULL,
    timeout_expired=subprocess.TimeoutExpired,
    thread_factory=threading.Thread,
    delay_seconds=SYSTEM_RESTART_DELAY_SECONDS,
    command_timeout_seconds=SYSTEM_RESTART_COMMAND_TIMEOUT_SECONDS,
    service_name=SCHEDULER_SERVICE_NAME,
):
    restart_env = environ.copy()
    restart_env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    sudo_path = resolve_executable("sudo")
    systemctl_path = resolve_executable("systemctl")
    command = [
        resolve_executable("sh"),
        "-c",
        f"sleep {delay_seconds}; exec {sudo_path} {systemctl_path} restart {service_name}",
    ]
    process = popen(
        command,
        stdin=devnull,
        stdout=devnull,
        stderr=devnull,
        env=restart_env,
        start_new_session=True,
    )
    if callable(getattr(process, "wait", None)):
        def stop_hung_restart():
            try:
                process.wait(timeout=command_timeout_seconds)
            except timeout_expired:
                process.kill()

        thread_factory(target=stop_hung_restart, daemon=True).start()
    return process


def git_pull_already_up_to_date(completed):
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
    return "already up to date" in output or "already up-to-date" in output


def scheduler_command_label(command):
    if isinstance(command, (list, tuple)):
        return " ".join(str(part) for part in command)
    return str(command or "")
