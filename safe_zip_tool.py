"""Inspect ZIP members and extract validated archives into a new directory."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import queue
import stat
import sys
import threading
import zipfile

MAX_ARCHIVE = 256 * 1024 * 1024
MAX_MEMBER = 512 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_MEMBERS = 10000
METHODS = {0: 'Stored', 8: 'Deflate', 12: 'BZIP2', 14: 'LZMA'}
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    prefix + digit for prefix in ('COM', 'LPT') for digit in '123456789¹²³'}

def cancel(event):
    if event is not None and event.is_set():
        raise InterruptedError('已取消；已创建的解压文件可能保留，请自行检查。')

def linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)

def identity(info):
    value = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    return value if os.name == 'nt' else value + (info.st_ctime_ns,)

def ordinary_directory(path):
    # Inspect ancestors too: do not follow junctions or symlinks supplied by the user.
    for parent in (path, *path.parents):
        info = parent.lstat()
        if linked(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f'文件夹路径包含链接或非文件夹：{parent}')

@contextlib.contextmanager
def opened(path):
    path = Path(path).absolute()
    ordinary_directory(path.parent)
    before = path.lstat()
    if linked(before) or not stat.S_ISREG(before.st_mode):
        raise ValueError('ZIP 必须是普通文件，不能是链接。')
    if before.st_size > MAX_ARCHIVE:
        raise ValueError('ZIP 文件上限 256 MiB。')
    with path.open('rb') as source:
        if identity(os.fstat(source.fileno())) != identity(before):
            raise OSError('ZIP 在打开时发生变化。')
        with zipfile.ZipFile(source) as archive:
            yield archive
        if identity(os.fstat(source.fileno())) != identity(before) or identity(path.lstat()) != identity(before):
            raise OSError('ZIP 在操作期间发生变化；解压结果可能不完整。')

def valid_parts(name):
    if '\\' in name or '\x00' in name or name.startswith('/'):
        raise ValueError('绝对路径、反斜杠或 NUL')
    value = name[:-1] if name.endswith('/') else name
    parts = value.split('/')
    if len(parts) > 64 or len(value) > 220:
        raise ValueError('路径超过 64 层或 220 字符')
    for part in parts:
        if not part or part in {'.', '..'} or part.endswith((' ', '.')):
            raise ValueError('空路径、点路径或末尾空格/点')
        if any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            raise ValueError('Windows 非法字符或备用数据流')
        if part.split('.')[0].rstrip(' ').upper() in RESERVED:
            raise ValueError('Windows 保留设备名')
    return parts

def inspect_archive(archive, event=None):
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ValueError('ZIP 条目上限 10000。')
    rows, nodes, explicit, total = [], {}, set(), 0
    for info in infos:
        cancel(event)
        reason = ''
        try:
            parts = valid_parts(info.orig_filename)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (kind == stat.S_IFDIR) != info.is_dir() and kind:
                raise ValueError('链接、特殊文件或类型不一致')
            if info.flag_bits & 1:
                raise ValueError('不支持加密 ZIP')
            if info.compress_type not in METHODS:
                raise ValueError('不支持的压缩方法')
            if info.file_size > MAX_MEMBER:
                raise ValueError('单条目超过 512 MiB')
            if info.file_size / max(1, info.compress_size) > 1000:
                raise ValueError('压缩膨胀比例超过 1000 倍')
            if info.is_dir() and info.file_size:
                raise ValueError('文件夹条目包含数据')
            key = '/'.join(parts).casefold()
            if key in explicit:
                raise ValueError('重名条目（忽略大小写）')
            proposed = [('/'.join(parts[:i]).casefold(), True) for i in range(1, len(parts))]
            proposed.append((key, info.is_dir()))
            for node, is_dir in proposed:
                if node in nodes and (not is_dir or not nodes[node]):
                    raise ValueError('文件和文件夹路径冲突')
            nodes.update(proposed)
            explicit.add(key)
        except ValueError as error:
            reason = str(error)
        total += info.file_size
        rows.append({'name': info.orig_filename, 'bytes': info.file_size,
            'compressed_bytes': info.compress_size, 'method': METHODS.get(info.compress_type, str(info.compress_type)),
            'crc32': f'{info.CRC:08x}', 'issue': reason})
    if total > MAX_TOTAL:
        raise ValueError('ZIP 声明的解压总量超过 1 GiB。')
    return {'entries': len(rows), 'bytes': total, 'extractable': not any(r['issue'] for r in rows), 'members': rows}

def inspect(path, event=None):
    with opened(path) as archive:
        return inspect_archive(archive, event)

def extract(path, output, event=None):
    target = Path(output).absolute()
    ordinary_directory(target.parent)
    if os.path.lexists(target):
        raise FileExistsError('仅能解压到尚不存在的新文件夹。')
    with opened(path) as archive:
        report = inspect_archive(archive, event)
        if not report['extractable']:
            bad = next(r for r in report['members'] if r['issue'])
            raise ValueError(f"拒绝解压 {bad['name']}：{bad['issue']}")
        cancel(event)
        target.mkdir()
        files, written = 0, 0
        for info in archive.infolist():
            cancel(event)
            destination = target.joinpath(*valid_parts(info.orig_filename))
            destination.parent.mkdir(parents=True, exist_ok=True)
            ordinary_directory(destination.parent)
            if info.is_dir():
                destination.mkdir(exist_ok=True)
                ordinary_directory(destination)
                continue
            size = 0
            with archive.open(info) as source, destination.open('xb') as result:
                while block := source.read(1024 * 1024):
                    cancel(event)
                    size += len(block)
                    written += len(block)
                    if size > info.file_size or written > MAX_TOTAL:
                        raise ValueError('实际解压大小超出声明或限制。')
                    result.write(block)
            if size != info.file_size:
                raise ValueError('实际解压大小与声明不一致。')
            files += 1
        cancel(event)
    return {'output': str(target), 'files': files, 'bytes': written}

def gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('ZIP 查看与安全解压器')
    root.geometry('920x650')
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='查看条目与路径问题；只解压到新文件夹，不运行任何内容。').pack(anchor='w')
    path, output = tk.StringVar(), tk.StringVar()
    controls = []
    def row(label, variable, browse):
        bar = ttk.Frame(frame)
        bar.pack(fill='x', pady=6)
        ttk.Label(bar, text=label, width=12).pack(side='left')
        entry = ttk.Entry(bar, textvariable=variable)
        entry.pack(side='left', fill='x', expand=True)
        button = ttk.Button(bar, text='选择', command=browse)
        button.pack(side='left', padx=8)
        controls.extend([entry, button])
    def choose_zip():
        value = filedialog.askopenfilename(filetypes=[('ZIP', '*.zip'), ('所有文件', '*.*')])
        if value:
            path.set(value)
            output.set(str(Path(value).with_suffix('')) + '-解压')
    def choose_output():
        value = filedialog.askdirectory(title='选择父文件夹；将在其中创建 ZIP 文件名对应的新文件夹')
        if value:
            output.set(str(Path(value) / (Path(path.get()).stem + '-解压')))
    row('ZIP 文件', path, choose_zip)
    row('新解压文件夹', output, choose_output)
    tree = ttk.Treeview(frame, columns=('size', 'method', 'issue'), show='tree headings', height=15)
    tree.heading('#0', text='条目路径')
    for key, label, width in [('size', '原大小/字节', 110), ('method', '压缩', 85), ('issue', '限制或路径问题', 280)]:
        tree.heading(key, text=label)
        tree.column(key, width=width)
    scrollbar = ttk.Scrollbar(frame, command=tree.yview)
    scrollbar.pack(side='right', fill='y')
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(fill='both', expand=True, pady=8)
    status = tk.StringVar(value='上限：ZIP 256 MiB、单项 512 MiB、解压总量 1 GiB、10000 条。')
    ttk.Label(frame, textvariable=status, wraplength=850).pack(anchor='w', pady=6)
    ttk.Label(frame, text='取消、损坏或磁盘错误可能留下部分文件。此工具不检查病毒，勿运行不可信内容。', wraplength=850).pack(anchor='w')
    messages, event = queue.Queue(), threading.Event()
    state = {'busy': False, 'closing': False}
    def start(action):
        if not path.get().strip() or action == 'extract' and not output.get().strip():
            messagebox.showerror('缺少路径', '请选择 ZIP 和新输出位置。')
            return
        source, target = path.get(), output.get()
        event.clear()
        state['busy'] = True
        for control in controls:
            control.configure(state='disabled')
        cancel_button.configure(state='normal')
        status.set('正在检查…' if action == 'inspect' else '正在检查并解压…')
        def worker():
            try:
                messages.put(('ok', action, inspect(source, event) if action == 'inspect' else extract(source, target, event)))
            except Exception as error:
                messages.put(('error', action, str(error)))
        threading.Thread(target=worker, daemon=True).start()
    bar = ttk.Frame(frame)
    bar.pack(fill='x', pady=10)
    for label, action in [('查看 ZIP', 'inspect'), ('解压到新文件夹', 'extract')]:
        button = ttk.Button(bar, text=label, command=lambda a=action: start(a))
        button.pack(side='left', padx=4)
        controls.append(button)
    cancel_button = ttk.Button(bar, text='取消', state='disabled', command=event.set)
    cancel_button.pack(side='left', padx=8)
    def close():
        if state['busy']:
            state['closing'] = True
            event.set()
            status.set('正在取消，等待当前操作结束…')
        else:
            root.destroy()
    def poll():
        try:
            kind, action, data = messages.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        state['busy'] = False
        if state['closing']:
            root.destroy()
            return
        for control in controls:
            control.configure(state='normal')
        cancel_button.configure(state='disabled')
        if kind == 'error':
            status.set(data + ' 如已创建输出文件夹，请检查其中的部分文件。')
        elif action == 'inspect':
            tree.delete(*tree.get_children())
            for item in data['members'][:1000]:
                tree.insert('', 'end', text=item['name'], values=(item['bytes'], item['method'], item['issue']))
            status.set(f"{data['entries']} 条，{data['bytes']} 字节；" + ('路径与声明限制检查通过。' if data['extractable'] else '含问题条目，拒绝解压。') + '界面最多显示 1000 条。')
        else:
            status.set(f"解压完成：{data['files']} 个文件，{data['bytes']} 字节。输出：{data['output']}")
        root.after(100, poll)
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, poll)
    root.mainloop()

def main():
    parser = argparse.ArgumentParser(description='ZIP 条目检查及仅向新文件夹解压。')
    subs = parser.add_subparsers(dest='action', required=True)
    subs.add_parser('list').add_argument('archive')
    sub = subs.add_parser('extract')
    sub.add_argument('archive')
    sub.add_argument('output')
    args = parser.parse_args()
    try:
        result = inspect(args.archive) if args.action == 'list' else extract(args.archive, args.output)
        print(json.dumps(result, ensure_ascii=True))
        return 1 if result.get('extractable') is False else 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(json.dumps({'error': str(error), 'partial_output_possible': args.action == 'extract'}, ensure_ascii=True), file=sys.stderr)
        return 2

if __name__ == '__main__':
    if len(sys.argv) == 1:
        gui()
    else:
        raise SystemExit(main())
