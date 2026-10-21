"""Owner-only local UI; it is deliberately excluded from customer native builds."""
import base64
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import secrets
import sys
import tkinter as tk
from tkinter import messagebox
from cryptography.hazmat.primitives import serialization


def authority_path():
    if sys.platform=='darwin':return Path.home()/'Library/Application Support/eMaktab Manager Authority'
    if sys.platform=='win32':
        import os
        return Path(os.environ['LOCALAPPDATA'])/'eMaktab Manager Authority'
    return Path.home()/'.local/share/emaktab-manager-authority'


def encode(raw):return base64.urlsafe_b64encode(raw).decode().rstrip('=')


root=tk.Tk();root.title('eMaktab Manager — faqat egasi uchun');root.geometry('620x520')
root.configure(bg='#f4f5ef')
tk.Label(root,text='Litsenziya yaratish',font=('Helvetica',22,'bold'),bg='#f4f5ef',fg='#102c25').pack(pady=(24,8))
tk.Label(root,text='Mijoz yuborgan qurilma kodini kiriting.\nMaxfiy imzo kaliti bu kompyuterdan chiqmaydi.',bg='#f4f5ef',fg='#102c25').pack(pady=8)
device=tk.Text(root,height=4,font=('Courier',12),wrap='word',bg='white',fg='#102c25',insertbackground='#102c25');device.pack(fill='x',padx=24,pady=12)
result=tk.Text(root,height=9,font=('Courier',11),wrap='word',bg='white',fg='#102c25')
def create():
    code=device.get('1.0','end').strip().upper()
    if not re.fullmatch(r'EM1-(?:[A-F0-9]{8}-){7}[A-F0-9]{8}',code):
        messagebox.showerror('Qurilma kodi','To‘liq qurilma kodini kiriting.');return
    try:
        private=serialization.load_pem_private_key((authority_path()/'issuer-private.pem').read_bytes(),password=None)
        raw=json.dumps({'v':1,'product':'emaktab-manager','device':code,'id':secrets.token_hex(16),'issued':datetime.now(timezone.utc).isoformat(timespec='seconds')},sort_keys=True,separators=(',',':')).encode()
        value='EML1.'+encode(raw)+'.'+encode(private.sign(raw))
        result.configure(state='normal');result.delete('1.0','end');result.insert('1.0',value);result.configure(state='disabled')
    except Exception:
        messagebox.showerror('Maxfiy kalit','Egaga tegishli maxfiy imzo kaliti topilmadi yoki o‘qilmadi. U foydalanuvchi paketida bo‘lmaydi.')
tk.Button(root,text='Shu qurilma uchun kalit yaratish',command=create).pack(pady=8)
result.pack(fill='both',expand=True,padx=24,pady=12)
def copy():
    value=result.get('1.0','end').strip()
    if value:root.clipboard_clear();root.clipboard_append(value);root.update()
tk.Button(root,text='Litsenziyani nusxalash',command=copy).pack(pady=(0,20))
root.mainloop()
