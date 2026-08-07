# -*- coding: utf-8 -*-
"""部署 web/index.html 到线上服务器（sftp 单文件直传）。

用法（密码只走环境变量，不写进任何文件）：
    $env:ALI_PWD='<服务器密码>'
    python scripts/deploy_web.py

本机需先安装 paramiko：pip install paramiko
"""
import os
import paramiko

HOST = "59.110.224.189"
REMOTE_INDEX = "/opt/poster-gen/web/index.html"
LOCAL_INDEX = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "index.html"))


def main():
    pwd = os.environ.get("ALI_PWD")
    if not pwd:
        raise SystemExit("缺少 ALI_PWD 环境变量（服务器密码），拒绝执行")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pwd, timeout=30,
                   look_for_keys=False, allow_agent=False)
    sftp = client.open_sftp()
    sftp.put(LOCAL_INDEX, REMOTE_INDEX)
    sftp.close()
    print("uploaded:", LOCAL_INDEX, "->", REMOTE_INDEX)

    def run(cmd, timeout=60):
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        print("$", cmd[:70], "->", code)
        if out.strip():
            print(out.strip()[-300:])
        if err.strip() and code != 0:
            print("ERR:", err.strip()[-300:])
        return code

    run("curl -s -o /dev/null -w 'index HTTP %{http_code}\\n' http://127.0.0.1/")
    client.close()
    print("DONE")


if __name__ == "__main__":
    main()