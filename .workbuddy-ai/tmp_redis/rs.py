import sys, paramiko
from sshrun import HOST, USER, PWD

def conn():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PWD, timeout=25, allow_agent=False,
              look_for_keys=False, banner_timeout=30, auth_timeout=30)
    return c

def run_data(body, env=None):
    c = conn()
    try:
        prefix = ""
        if env:
            prefix = "export " + " ".join("%s=%s" % (k, v) for k, v in env.items()) + "\n"
        stdin, stdout, stderr = c.exec_command("bash -s", timeout=600)
        stdin.write(prefix + body)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err
    finally:
        c.close()

def run_file(path, env=None):
    return run_data(open(path, "r", encoding="utf-8").read(), env)

if __name__ == "__main__":
    pw = sys.argv[2] if len(sys.argv) > 2 else ""
    rc, out, err = run_file(sys.argv[1], {"REDIS_PASS": pw} if pw else None)
    print(out)
    if err.strip():
        print("--- STDERR ---")
        print(err)
    print("[exit %s]" % rc)
