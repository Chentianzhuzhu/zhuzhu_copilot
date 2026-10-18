import sys, paramiko
from sshrun import HOST, USER, PWD

def upload(local, remote):
    t = paramiko.Transport((HOST, 22))
    t.connect(username=USER, password=PWD)
    s = paramiko.SFTPClient.from_transport(t)
    s.put(local, remote)
    s.chmod(remote, 0o700)
    s.close(); t.close()
    print("uploaded", remote)

if __name__ == "__main__":
    upload(sys.argv[1], sys.argv[2])
