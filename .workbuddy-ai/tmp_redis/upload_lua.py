import base64, paramiko, json
from sshrun import HOST, USER, PWD

FILES = [
    ("lua/order_dedup.lua",     "/etc/redis/scripts/order_dedup.lua"),
    ("lua/stock_deduct.lua",    "/etc/redis/scripts/stock_deduct.lua"),
    ("lua/points_deduct.lua",   "/etc/redis/scripts/points_deduct.lua"),
    ("lua/points_credit.lua",   "/etc/redis/scripts/points_credit.lua"),
    ("lua/login_throttle.lua",  "/etc/redis/scripts/login_throttle.lua"),
    ("lua/batch_get.lua",       "/etc/redis/scripts/batch_get.lua"),
]

def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PWD, timeout=25, allow_agent=False,
              look_for_keys=False, banner_timeout=30, auth_timeout=30)
    digests = {}
    for local, remote in FILES:
        data = open(local, "rb").read()
        b64 = base64.b64encode(data).decode()
        c.exec_command("printf '%%s' '%s' | base64 -d > %s" % (b64, remote))[1].channel.recv_exit_status()
        # sha1 of the file content = the SHA1 Redis uses for EVALSHA
        import hashlib
        digests[local.split("/")[-1]] = hashlib.sha1(data).hexdigest()
    c.exec_command("chown -R root:redis /etc/redis/scripts && chmod 640 /etc/redis/scripts/*.lua && ls -l /etc/redis/scripts/")[1].read()
    _, out, _ = c.exec_command("ls -1 /etc/redis/scripts/ && echo '---' && sha1sum /etc/redis/scripts/*.lua")
    print(out.read().decode())
    c.close()
    json.dump(digests, open("sha1.json", "w"), indent=2)
    print("LOCAL SHA1 (use for EVALSHA):")
    for k, v in digests.items():
        print("  %-22s %s" % (k, v))

main()
