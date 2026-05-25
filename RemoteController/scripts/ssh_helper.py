#!/usr/bin/env python3
"""SSH helper: run command or upload file. Password from RC_SSH_PASSWORD env."""
import os
import sys
import paramiko

HOST = os.environ.get("RC_SSH_HOST", "192.168.1.16")
USER = os.environ.get("RC_SSH_USER", "master")
PASSWORD = os.environ.get("RC_SSH_PASSWORD", "")


def connect():
    if not PASSWORD:
        print("Set RC_SSH_PASSWORD", file=sys.stderr)
        sys.exit(2)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    return client


def run(cmd: str) -> int:
    client = connect()
    _, stdout, stderr = client.exec_command(cmd, get_pty=True)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    client.close()
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, file=sys.stderr, end="" if err.endswith("\n") else "\n")
    return code


def upload(local_path: str, remote_path: str) -> None:
    client = connect()
    sftp = client.open_sftp()
    sftp.put(local_path, remote_path)
    sftp.close()
    client.close()
    print(f"uploaded {local_path} -> {remote_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: ssh_helper.py run '<cmd>' | upload <local> <remote>")
        sys.exit(1)
    if sys.argv[1] == "run":
        sys.exit(run(sys.argv[2]))
    if sys.argv[1] == "upload":
        upload(sys.argv[2], sys.argv[3])
    else:
        sys.exit(run(" ".join(sys.argv[1:])))
