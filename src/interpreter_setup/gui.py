# ruff: noqa: E501
"""Ventana del instalador (tkinter: viene con Python y mantiene el .exe pequeño)."""

from __future__ import annotations

import contextlib
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import core

ACCENT = "#0000FF"  # Ford Blue de la app
ACCENT_SOFT = "#E3E3FF"
SURFACE = "#F4F6FA"
DEFAULT_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Interpreter"


def resource(name: str) -> Path | None:
    """Archivo incluido en el .exe (o en el repo al probar desde el código)."""
    bundled = getattr(sys, "_MEIPASS", None)
    for base in ([Path(bundled)] if bundled else []) + [Path(__file__).resolve().parents[2]]:
        p = base / name
        if p.exists():
            return p
    return None


class SetupWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Instalador de Interpreter {core.payload_version()}")
        self.geometry("760x640")
        self.minsize(680, 560)
        self.configure(bg="white")
        ico = resource("assets/interpreter.ico")
        if ico:
            with contextlib.suppress(tk.TclError):
                self.iconbitmap(default=str(ico))
        self._q: queue.Queue[tuple[str, str]] = queue.Queue()
        self._busy = False

        head = tk.Frame(self, bg=ACCENT)
        head.pack(fill="x")
        tk.Label(head, text="Interpreter", bg=ACCENT, fg="white", font=("Segoe UI", 16, "bold")).pack(
            anchor="w", padx=18, pady=(12, 0)
        )
        tk.Label(
            head,
            text="Verifica este equipo e instala todo lo necesario para que el intérprete funcione.",
            bg=ACCENT,
            fg="#C9D3FF",
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=18, pady=(0, 12))

        body = tk.Frame(self, bg="white")
        body.pack(fill="x", padx=18, pady=12)
        body.columnconfigure(1, weight=1)

        self.var_dir = tk.StringVar(value=str(DEFAULT_DIR))
        self.var_models = tk.BooleanVar(value=True)
        self.var_clone = tk.BooleanVar(value=False)
        self.var_shortcuts = tk.BooleanVar(value=True)
        self.var_from = tk.StringVar()
        self.var_proxy = tk.StringVar(value=os.environ.get("HTTPS_PROXY", ""))

        self._row(body, 0, "Instalar en", self.var_dir, self._pick_dir)
        self._row(body, 1, "Copiar modelos desde otra PC (opcional)", self.var_from, self._pick_models)
        tk.Label(
            body,
            text="Solo si ya bajaste los modelos en otra computadora: elige su carpeta «models» (en un pendrive o "
            "en red) y se copian aquí en vez de descargarse. Si lo dejas vacío, se descargan de internet.",
            bg="white", fg="#757E8C", font=("Segoe UI", 8), wraplength=620, justify="left",
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=(0, 4))  # fmt: skip
        self._row(body, 3, "Proxy (opcional)", self.var_proxy, None, hint=core.FORD_PROXY)
        opts = tk.Frame(body, bg="white")
        opts.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for text, var in (
            ("Descargar los modelos ahora (~6 GB)", self.var_models),
            ("Voz clonada (YourTTS, +3 GB)", self.var_clone),
            ("Crear accesos directos", self.var_shortcuts),
        ):
            ttk.Checkbutton(opts, text=text, variable=var).pack(anchor="w")

        btns = tk.Frame(self, bg="white")
        btns.pack(fill="x", padx=18)
        self.btn_check = tk.Button(
            btns, text="Solo verificar", command=self._on_check, **self._btn_style(False)
        )
        self.btn_install = tk.Button(btns, text="Instalar", command=self._on_install, **self._btn_style(True))
        self.btn_check.pack(side="left")
        self.btn_install.pack(side="left", padx=8)
        tk.Button(btns, text="Cerrar", command=self.destroy, **self._btn_style(False)).pack(side="right")

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=18, pady=(12, 4))
        self.lbl_step = tk.Label(
            self, text="Listo para empezar.", bg="white", anchor="w", font=("Segoe UI", 10, "bold")
        )
        self.lbl_step.pack(fill="x", padx=18)
        frame = tk.Frame(self, bg="white")
        frame.pack(fill="both", expand=True, padx=18, pady=(4, 14))
        self.text = tk.Text(
            frame, bg=SURFACE, relief="flat", wrap="word", font=("Cascadia Mono", 9), state="disabled"
        )
        sb = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.after(100, self._drain)

    @staticmethod
    def _btn_style(primary: bool) -> dict:
        if primary:
            return {"bg": ACCENT, "fg": "white", "activebackground": "#3D3DFF", "activeforeground": "white",
                    "relief": "flat", "padx": 18, "pady": 6, "font": ("Segoe UI", 10, "bold")}  # fmt: skip
        return {
            "bg": "white",
            "fg": "black",
            "relief": "solid",
            "bd": 1,
            "padx": 14,
            "pady": 5,
            "font": ("Segoe UI", 10),
        }

    def _row(self, parent, row, label, var, browse, hint="") -> None:  # noqa: ANN001
        tk.Label(parent, text=label, bg="white", font=("Segoe UI", 9), fg="#454C58").grid(
            row=row, column=0, sticky="w", pady=3, padx=(0, 10)
        )
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        if browse:
            ttk.Button(parent, text="Examinar…", command=browse).grid(row=row, column=2, padx=(8, 0))
        elif hint:
            tk.Label(parent, text=f"p. ej. {hint}", bg="white", fg="#757E8C", font=("Segoe UI", 8)).grid(
                row=row, column=2, padx=(8, 0)
            )

    # -- acciones --------------------------------------------------------------------------------------------------

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(title="Carpeta de instalación", initialdir=self.var_dir.get() or None)
        if d:
            self.var_dir.set(str(Path(d)))

    def _pick_models(self) -> None:
        d = filedialog.askdirectory(title="Carpeta «models» copiada de otra PC")
        if d:
            self.var_from.set(str(Path(d)))

    def options(self) -> core.Options:
        return core.Options(
            install_dir=Path(self.var_dir.get().strip() or str(DEFAULT_DIR)),
            clone=self.var_clone.get(),
            models=self.var_models.get(),
            models_from=Path(self.var_from.get().strip()) if self.var_from.get().strip() else None,
            shortcuts=self.var_shortcuts.get(),
            proxy=self.var_proxy.get().strip(),
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for b in (self.btn_check, self.btn_install):
            b.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _on_check(self) -> None:
        opts = self.options()
        self._clear()
        self._start(lambda: self._do_check(opts))

    def _do_check(self, opts: core.Options) -> None:
        self._q.put(("step", "Verificando este equipo…"))
        checks = core.check_system(opts)
        for c in checks:
            self._q.put(("log", c.line()))
        bad = core.blocking_failures(checks)
        self._q.put(("step", "Todo en orden: puedes instalar." if not bad else "Hay requisitos sin cumplir."))

    def _on_install(self) -> None:
        opts = self.options()
        if opts.models_from and not opts.models_from.exists():
            messagebox.showerror("Interpreter", "La carpeta de modelos indicada no existe.")
            return
        self._clear()
        self._start(lambda: self._do_install(opts))

    def _do_install(self, opts: core.Options) -> None:
        res = core.run_install(opts, lambda m: self._q.put(("log", m)), lambda s: self._q.put(("step", s)))
        self._q.put(("step", "✓ Instalación terminada." if res.ok else "✗ La instalación no se completó."))
        if res.log_file:
            self._q.put(("log", f"\nRegistro completo: {res.log_file}"))
        self._q.put(("done", "ok" if res.ok else "error"))

    def _start(self, fn) -> None:  # noqa: ANN001
        self._set_busy(True)

        def run() -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - se muestra en la ventana en vez de morir en silencio
                self._q.put(("log", f"✗ Error inesperado: {exc}"))
            self._q.put(("idle", ""))

        threading.Thread(target=run, daemon=True).start()

    # -- cola de mensajes (el hilo de trabajo no toca los widgets) ---------------------------------------------------

    def _clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _append(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def _drain(self) -> None:
        try:
            while True:
                kind, msg = self._q.get_nowait()
                if kind == "log":
                    self._append(msg)
                elif kind == "step":
                    self.lbl_step.configure(text=msg)
                elif kind == "idle":
                    self._set_busy(False)
                elif kind == "done" and msg == "ok":
                    messagebox.showinfo(
                        "Interpreter", "Instalación terminada.\nAbre «Interpreter» desde el escritorio."
                    )
        except queue.Empty:
            pass
        self.after(100, self._drain)


def main() -> int:
    SetupWindow().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
