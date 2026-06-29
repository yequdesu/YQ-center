# croc Release 包

本目录存放 YeQu Node 大文件/跨 Node 传输实验所需的 `croc` release 包。

当前版本：

```text
v10.4.4
```

当前随附文件：

```text
v10.4.4/croc_v10.4.4_checksums.txt
v10.4.4/croc_v10.4.4_Linux-64bit.tar.gz
v10.4.4/croc_v10.4.4_Windows-64bit.zip
```

来源：

```text
https://github.com/schollz/croc/releases/tag/v10.4.4
```

校验：

```powershell
Get-FileHash .\v10.4.4\croc_v10.4.4_Windows-64bit.zip -Algorithm SHA256
Get-FileHash .\v10.4.4\croc_v10.4.4_Linux-64bit.tar.gz -Algorithm SHA256
Select-String .\v10.4.4\croc_v10.4.4_checksums.txt -Pattern "Windows-64bit|Linux-64bit"
```

本目录只是部署便利包，不代表 Center 可以假设所有 Node 都已安装 croc。Node 必须通过自己的 `*.transfer.croc.status` capability 上报事实状态；Center 只能基于该状态进行传输能力判断和调度。

Linux 安装示例：

```bash
cd /tmp
tar -xzf /path/to/croc_v10.4.4_Linux-64bit.tar.gz
sudo install -m 0755 croc /usr/local/bin/croc
croc --version
```

无 sudo 安装示例：

```bash
mkdir -p "$HOME/.local/bin"
tar -xzf /path/to/croc_v10.4.4_Linux-64bit.tar.gz -C "$HOME/.local/bin" croc
chmod 0755 "$HOME/.local/bin/croc"
"$HOME/.local/bin/croc" --version
```
