import sys, base64, paramiko
from sshrun import HOST, USER, PWD

def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PWD, timeout=25, allow_agent=False,
              look_for_keys=False, banner_timeout=30, auth_timeout=30)
    return c

def put(local, remote):
    data = open(local, "rb").read()
    b64 = base64.b64encode(data).decode()
    c = connect()
    try:
        c.exec_command("rm -f %s.b64" % remote)[1].channel.recv_exit_status()
        # chunk to keep each command line modest
        step = 3000
        for i in range(0, len(b64), step):
            part = b64[i:i+step]
            cmd = "printf '%%s' '%s' >> %s.b64" % (part, remote)
            _, out, err = c.exec_command(cmd)
            rc = out.channel.recv_exit_status()
            if rc != 0:
                raise RuntimeError("chunk failed: " + err.read().decode())
        cmd = "base64 -d %s.b64 > %s && chmod 700 %s && rm -f %s.b64 && wc -c %s" % (remote, remote, remote, remote, remote)
        _, out, err = c.exec_command(cmd)
        rc = out.channel.recv_exit_status()
        print(out.read().decode(), err.read().decode(), "rc=", rc)
    finally:
        c.close()

if __name__ == "__main__":
    put(sys.argv[1], sys.argv[2])
