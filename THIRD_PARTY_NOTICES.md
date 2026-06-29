# Third-party notices

I hate Grid is MIT-licensed. It uses the following third-party components.
All are free to redistribute.

| Component | License | Notes |
|-----------|---------|-------|
| **PySide6 / Qt** | **LGPL-3.0** | The Qt bindings (the GUI). LGPL: you may use it in any app — including this MIT one — as long as the LGPL parts stay replaceable and this notice is kept. Full source is on this repo and pinned in `requirements.txt`, so the Qt libraries can be rebuilt/relinked. |
| **uiautomation** | Apache-2.0 | Python UI Automation wrapper (reads on-screen text). |
| **comtypes** | MIT | COM support that `uiautomation` builds on. |
| **Python standard library** | PSF | `ctypes`, `tkinter`-free; no extra installs. |

The bundled `.exe` is built with PyInstaller and packs the above. To swap the
Qt libraries (per LGPL), rebuild from source:

```bash
pip install -r requirements.txt
python -m PyInstaller --onefile --windowed --name "I hate Grid" \
  --collect-submodules comtypes --collect-submodules uiautomation app.py
```

Each dependency ships its full license text inside its package
(e.g. `PySide6/`, `comtypes-*.dist-info/licenses/`).
