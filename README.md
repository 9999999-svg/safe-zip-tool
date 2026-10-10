# ZIP 查看与安全解压器

查看 ZIP 内的文件路径、大小、压缩方式和 CRC32；检查越界路径、重名、链接和异常膨胀，再解压到新文件夹。适合先检查下载的压缩包及整理收到的 ZIP。

[下载 Windows EXE](https://github.com/9999999-svg/safe-zip-tool/releases/download/v1.0.0/SafeZipTool.exe) · [下载 Python 源码](https://github.com/9999999-svg/safe-zip-tool/releases/download/v1.0.0/safe-zip-tool-source-v1.0.0.zip) · [SHA-256](https://github.com/9999999-svg/safe-zip-tool/releases/download/v1.0.0/SHA256SUMS.txt)

## 使用

双击 EXE，选择 ZIP，点击「查看 ZIP」。选择尚不存在的新解压文件夹，点击「解压到新文件夹」。路径有问题时整包拒绝解压；已有文件夹不覆盖。界面最多显示前 1000 条，命令行 `list` 返回全部条目。

```powershell
python safe_zip_tool.py
python safe_zip_tool.py list "资料.zip"
python safe_zip_tool.py extract "资料.zip" "新的解压文件夹"
SafeZipTool.exe list "资料.zip"
```

命令行输出 JSON。退出码：0 完成，1 条目存在限制或路径问题，2 失败，130 中断。查看只检查目录声明，不读取所有内容；解压时读取文件并检查 CRC。

## 必要限制

支持 Stored、Deflate、BZIP2、LZMA；不支持加密、多卷、RAR、7z 或 Zstandard。中文文件名遵循 ZIP 自带的 UTF-8/CP437 声明，旧压缩包的非标准文件名编码可能乱码。

ZIP 本体最多 256 MiB、10000 个条目、单项最多 512 MiB、解压总量最多 1 GiB、膨胀比例最多 1000 倍、条目路径最多 220 字符/64 层。拒绝绝对路径、`..`、反斜杠、Windows 非法字符及保留设备名、大小写重名、文件/文件夹冲突、链接及特殊文件。输入和输出路径不能经过链接或目录联接。

取消、CRC 损坏、磁盘不足或运行时错误可能留下部分解压文件，请自行检查和删除。不会自动清理已创建的输出目录，不保留原权限和时间戳。此工具不查杀病毒，路径检查不能保证内容可信，也不能防御其他本地进程在操作时恶意替换文件夹；请勿运行不可信压缩包中的程序。

EXE 为 Windows x64，约 12.5 MiB，未签名，包含 Python/Tcl/Tk，无需另装 Python。源码只使用标准库，运行需带 Tkinter 的 Python 3.11 或更高版本。构建：在已有 Python 环境中安装 PyInstaller 6.21.0 后运行 `python build.py`。依赖版本、体积、卸载和第三方许可见 [DEPENDENCIES.md](DEPENDENCIES.md)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。卸载工具直接删除 EXE 和相关文件即可。

## 自愿赞助

功能和自有 MIT 源码全部免费，赞助完全自愿。

<img src="assets/donate-wechat.png" alt="自愿微信赞助二维码" width="280">
