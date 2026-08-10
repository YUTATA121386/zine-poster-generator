# -*- coding: utf-8 -*-
"""部署门户/工具页/知识库/服务端代码到线上服务器（SFTP + systemctl 重启）。

用法（密码只走环境变量，不写进任何文件）：
    $env:ALI_PWD='<服务器密码>'
    python scripts/deploy_site.py [--kb-repo 路径] [--skip-kb]

本机需先安装 paramiko。知识库默认构建 base=/kb/ 版本后整体上传（先清空远端 kb 目录再传）。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HOST = "59.110.224.189"
REMOTE_BASE = "/opt/poster-gen"


def build_kb(kb_repo):
    out = tempfile.mkdtemp(prefix="kb_dist_")
    env = dict(os.environ)
    env["KB_BASE"] = "/kb/"
    npm = "npm.cmd" if os.name == "nt" else "npm"
    cmd = [npm, "run", "docs:build", "--", "--outDir", out]
    print("build KB:", " ".join(cmd), "in", kb_repo)
    r = subprocess.run(cmd, cwd=kb_repo, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit("KB build failed")
    print("KB build OK ->", out)
    return out


def walk_files(root):
    for base, dirs, files in os.walk(root):
        for f in files:
            fp = os.path.join(base, f)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            yield fp, rel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb-repo", default=os.environ.get("KB_REPO",
                    r"C:\Users\beppi\Documents\Codex\YUTATA121386.github.io"))
    ap.add_argument("--skip-kb", action="store_true")
    args = ap.parse_args()

    pwd = os.environ.get("ALI_PWD")
    if not pwd:
        raise SystemExit("缺少 ALI_PWD 环境变量（服务器密码），拒绝执行")

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kb_local = None
    if not args.skip_kb:
        kb_local = build_kb(args.kb_repo)

    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pwd, timeout=30,
                   look_for_keys=False, allow_agent=False)
    sftp = client.open_sftp()

    def mkdirs(remote_dir):
        parts = [p for p in remote_dir.replace("\\", "/").split("/") if p]
        cur = ""
        for part in parts:
            cur = ("/" + part) if not cur else (cur + "/" + part)
            try:
                sftp.stat(cur)
            except FileNotFoundError:
                sftp.mkdir(cur)

    def run(cmd, timeout=180):
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        print("$", cmd[:90], "->", code)
        if out.strip():
            print(out.strip()[-400:])
        if err.strip() and code != 0:
            print("ERR:", err.strip()[-400:])
        return code

    # 1. code + pages
    art_files = []
    art_dir = os.path.join(here, "web", "art")
    if os.path.isdir(art_dir):
        for f in sorted(os.listdir(art_dir)):
            art_files.append("web/art/" + f)
    for rel in tuple(["poster_server.py", "web/index.html", "web/tool.html"]) + tuple(art_files):
        src = os.path.join(here, rel)
        if os.path.isfile(src):
            sftp.put(src, REMOTE_BASE + "/" + rel)
            print("uploaded:", rel)
        else:
            print("SKIP (missing):", rel)

    # 2. kb dir
    if kb_local:
        if run("rm -rf %s/web/kb && mkdir -p %s/web/kb" % (REMOTE_BASE, REMOTE_BASE)) != 0:
            raise SystemExit("remote kb reset failed")
        n = 0
        for fp, rel in walk_files(kb_local):
            rp = REMOTE_BASE + "/web/kb/" + rel
            mkdirs(os.path.dirname(rp))
            sftp.put(fp, rp)
            n += 1
        print("kb uploaded:", n, "files")
        shutil.rmtree(kb_local, ignore_errors=True)

    sftp.close()

    # 3. restart + self-check
    run("systemctl restart poster-gen")
    run("sleep 2; curl -s -o /dev/null -w 'index HTTP %{http_code}\\n' http://127.0.0.1/")
    run("curl -s -o /dev/null -w 'kb HTTP %{http_code}\\n' -H 'Authorization: Bearer invalid' http://127.0.0.1/kb/")
    client.close()
    print("DONE")


if __name__ == "__main__":
    main()
