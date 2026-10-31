"""Local-only credential form. Never calls a provider or prints the API key."""
import copy
import json
import tkinter as tk
from tkinter import messagebox, ttk
from run_pilot import ROOT, ConfigError, validate_config


def main():
    target = ROOT / 'config.local.json'
    data = json.loads((target if target.exists() else ROOT / 'config.example.json').read_text(encoding='utf-8-sig'))
    window = tk.Tk()
    window.title('模型试运行 · 本机配置')
    window.geometry('700x490')
    window.minsize(650, 470)
    container = ttk.Frame(window, padding=24)
    container.pack(fill='both', expand=True)
    container.columnconfigure(1, weight=1)
    ttk.Label(container, text='填写服务商后台的接入信息', font=('Microsoft YaHei', 15, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
    ttk.Label(container, text='仅保存到本机。此窗口不会发送任何模型请求。').grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 18))
    fields = {}
    for row, (name, label) in enumerate([('provider','服务商'), ('base_url','Base URL'), ('model','模型名称'), ('api_key','API Key')], start=2):
        ttk.Label(container, text=label).grid(row=row, column=0, sticky='w', padx=(0, 18), pady=8)
        value = tk.StringVar(value=data.get(name, ''))
        entry = ttk.Entry(container, textvariable=value, show='●' if name == 'api_key' else '')
        entry.grid(row=row, column=1, sticky='ew', pady=8)
        fields[name] = value
    api_format = tk.StringVar(value=data.get('api_format', 'chat_completions'))
    ttk.Label(container, text='接口类型').grid(row=6, column=0, sticky='w', pady=8)
    ttk.Combobox(container, textvariable=api_format, values=['responses','chat_completions'], state='readonly').grid(row=6, column=1, sticky='ew', pady=8)
    json_mode = tk.BooleanVar(value=data.get('json_mode', True))
    stream = tk.BooleanVar(value=data.get('stream', False))
    ttk.Checkbutton(container, text='发送 JSON 格式参数', variable=json_mode).grid(row=7, column=0, columnspan=2, sticky='w', pady=4)
    ttk.Checkbutton(container, text='Responses流式返回', variable=stream).grid(row=8, column=0, columnspan=2, sticky='w', pady=4)
    ttk.Label(container, text='西牧预填项是试连配置，尚非实测成功。只需在本机填写完整密钥后保存。', wraplength=610).grid(row=9, column=0, columnspan=2, sticky='w', pady=(8, 14))

    def save():
        updated = dict(data)
        updated.update({name: value.get().strip() for name, value in fields.items()})
        updated['json_mode'] = json_mode.get()
        updated['api_format'] = api_format.get()
        updated['stream'] = stream.get()
        try:
            validate_config(copy.deepcopy(updated))
            target.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
        except (ConfigError, OSError) as exc:
            messagebox.showerror('尚未保存', str(exc), parent=window)
            return
        messagebox.showinfo('配置已保存', '密钥已保存在本机配置文件，未发送模型请求。\n回到对话告知已填写即可，不要发送密钥。', parent=window)
        window.destroy()

    ttk.Button(container, text='保存到本机', command=save).grid(row=10, column=1, sticky='e')
    window.mainloop()


if __name__ == '__main__':
    main()
