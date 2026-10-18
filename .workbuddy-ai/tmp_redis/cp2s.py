import sys
import rs

MARK = "___WB_TRANSFER_END___"

def upload(local, remote, mode="700"):
    body = open(local, "r", encoding="utf-8").read()
    assert MARK not in body, "marker collision"
    wrapper = (
        "#!/bin/bash\n"
        "set -e\n"
        "cat > %s <<'%s'\n"
        "%s"
        "%s\n"
        "chmod %s %s\n"
        "ls -l %s\n"
        "sha1sum %s\n"
    ) % (remote, MARK, body, MARK, mode, remote, remote, remote)
    rc, out, err = rs.run_data(wrapper)
    print(out)
    if err.strip():
        print("STDERR:", err[:2000])
    print("[exit %s]" % rc)

if __name__ == "__main__":
    upload(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "700")
