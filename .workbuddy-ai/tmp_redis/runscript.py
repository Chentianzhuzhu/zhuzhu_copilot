import sys, paramiko
from sshrun import HOST, USER, PWD

def run_script(path, env=None):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PWD, timeout=25, allow_agent=False,
              look_for_keys=False, banner_timeout=30, auth_timeout=30)
    try:
        prefix = ""
        if env:
            prefix = "export " + " ".join("%s=%s" % (k, v) for k, v in env.items()) + "\n"
        body = prefix + open(path, "r", encoding="utf-8").read()
        stdin, stdout, stderr = c.exec_command("bash -s", timeout=600)
        stdin.write(body)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err
    finally:
        c.close()

if __name__ == "__main__":
    pw = sys.argv[2] if len(sys.argv) > 2 else ""
    rc, out, err = run_script(sys.argv[1], {"REDIS_PASS": pw} if pw else None)
    print(out)
    if err.strip():
        print("--- STDERR ---")
        print(err)
    print("[exit %s]" % rc)
